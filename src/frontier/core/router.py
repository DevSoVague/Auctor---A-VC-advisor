"""Model router: the live backend behind ctx.llm (owner: person 2).

Follows the PRD (Services, Model router):
1. Look up the model for (agent, profile) in profiles.yaml.
2. Cache key = sha256 of model, messages, schema and temperature; a hit returns the
   stored response and logs cache_hit = true.
3. Call through LiteLLM with JSON-schema structured output. On a 429 or 5xx, retry
   once, then walk the fallback list.
4. Log every call to llm_calls: tokens in, cached and out; list price; latency.
5. Enforce caps: max output tokens per call, max calls per run, max USD per run.
   Breaching one raises BudgetExceeded and stops the run. Each call also has a
   time limit (REQUEST_TIMEOUT_SECONDS); a timeout is retried like a 5xx.
6. LLM_MODE=stub never reaches the router (core/context.py uses StubLLM).

Caps are checked before each paid call, so a run can overshoot the USD cap by at
most the one call that crossed it. Cache hits are free and don't count.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from frontier.core.cache import CachedResponse, PostgresCache, ResponseCache, cache_key
from frontier.core.config import Settings, load_yaml
from frontier.core.costs import (
    CallRecord,
    CostLog,
    PostgresCostLog,
    Price,
    load_prices,
    price_for,
)

Completion = Callable[..., Awaitable[Any]]

RETRY_DELAY_SECONDS = 1.0
TIMEOUT_GRACE_SECONDS = 15.0  # our hard limit fires this long after LiteLLM's


class BudgetExceeded(RuntimeError):
    """A per-run cap (calls or USD) was reached; the run must stop."""


class AllModelsFailed(RuntimeError):
    """The primary model and every fallback failed with retryable errors."""


class ModelNotConfigured(RuntimeError):
    """profiles.yaml has no model for this agent and profile."""


@dataclass(frozen=True)
class Target:
    """How to reach one model through LiteLLM."""

    name: str  # the ID used in profiles.yaml and prices.yaml
    litellm_model: str
    api_key: str
    api_base: str | None = None


def is_retryable(exc: BaseException) -> bool:
    """429 and 5xx responses, timeouts and dropped connections."""
    if isinstance(exc, TimeoutError):  # includes asyncio's, raised by our own time limit
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    return type(exc).__name__ in {"APIConnectionError", "APITimeoutError", "Timeout"}


async def _litellm_completion(**kwargs: Any) -> Any:
    import litellm  # imported lazily: slow to import, and unused in stub mode

    return await litellm.acompletion(**kwargs)


class Router:
    def __init__(
        self,
        *,
        settings: Settings,
        profiles: dict,
        prices: dict[str, Price],
        providers: dict,
        cache: ResponseCache,
        cost_log: CostLog,
        completion: Completion = _litellm_completion,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.profiles = profiles
        self.prices = prices
        self.aliases: dict = providers.get("aliases", {})
        self.cache = cache
        self.cost_log = cost_log
        self.completion = completion
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "Router":
        """The production router: YAML config from core/, Postgres cache and cost log."""
        settings = settings or Settings()
        return cls(
            settings=settings,
            profiles=load_yaml("profiles"),
            prices=load_prices(),
            providers=load_yaml("providers"),
            cache=PostgresCache(settings.database_url),
            cost_log=PostgresCostLog(settings.database_url),
        )

    def model_for(self, agent: str) -> str:
        profile = self.profiles.get(self.settings.profile) or {}
        model = profile.get(agent) or profile.get("default")
        if not model:
            raise ModelNotConfigured(
                f"profiles.yaml has no model for {agent!r} in profile "
                f"{self.settings.profile!r} and no default"
            )
        return model

    def _target(self, name: str) -> tuple[Target | None, str]:
        """Resolve a model ID or fallback alias; (None, reason) if it can't be called."""
        if name in self.aliases:
            alias = self.aliases[name]
            if "REPLACE-ME" in alias["model"]:
                return None, f"{name}: model ID not set in core/providers.yaml"
            key = getattr(self.settings, alias["api_key_env"].lower(), "")
            if not key:
                return None, f"{name}: {alias['api_key_env']} is not set"
            return Target(name, alias["model"], key), ""
        if not (self.settings.nebius_api_key and self.settings.nebius_base_url):
            return None, f"{name}: NEBIUS_API_KEY and NEBIUS_BASE_URL must be set in .env"
        # Nebius Token Factory is OpenAI-compatible.
        return Target(
            name,
            f"openai/{name}",
            self.settings.nebius_api_key,
            self.settings.nebius_base_url,
        ), ""

    async def _check_budget(self, run_id: str) -> None:
        totals = await self.cost_log.totals(run_id)
        if totals.calls >= self.settings.max_calls_per_run:
            raise BudgetExceeded(
                f"run {run_id}: reached MAX_CALLS_PER_RUN={self.settings.max_calls_per_run}"
            )
        if totals.usd >= self.settings.max_usd_per_run:
            raise BudgetExceeded(
                f"run {run_id}: spent ${totals.usd:.4f}, "
                f"MAX_USD_PER_RUN={self.settings.max_usd_per_run}"
            )

    async def complete(
        self, *, run_id: str, agent: str, messages: list[dict], schema: type[BaseModel]
    ) -> str:
        """Return the model's raw JSON text (the LLMBackend interface in core/context.py)."""
        temperature = self.settings.temperature
        primary = self.model_for(agent)

        key = cache_key(primary, messages, schema, temperature)
        hit = await self.cache.get(key)
        if hit is not None:
            await self.cost_log.record(
                CallRecord(
                    run_id, agent, hit.model, hit.input_tokens, 0, hit.output_tokens, 0.0, 0, True
                )
            )
            return hit.response

        await self._check_budget(run_id)

        problems: list[str] = []
        chain = [primary, *self.profiles.get("fallbacks", [])]
        for position, name in enumerate(chain):
            target, reason = self._target(name)
            if target is None:
                problems.append(reason)
                continue
            price = price_for(self.prices, name)  # fail before paying, not after
            attempts = 2 if position == 0 else 1  # retry the primary once
            for attempt in range(1, attempts + 1):
                started = time.monotonic()
                try:
                    response = await self._call(target, messages, schema, temperature)
                except Exception as exc:
                    if not is_retryable(exc):
                        raise
                    problems.append(f"{name} attempt {attempt}: {type(exc).__name__}: {exc}")
                    if attempt < attempts:
                        await self.sleep(RETRY_DELAY_SECONDS)
                    continue
                return await self._finish(
                    run_id, agent, name, price, response, started, messages, schema, temperature
                )

        raise AllModelsFailed(f"{agent}: every model failed:\n" + "\n".join(problems))

    async def _call(
        self, target: Target, messages: list[dict], schema: type[BaseModel], temperature: float
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": target.litellm_model,
            "messages": messages,
            "api_key": target.api_key,
            "temperature": temperature,
            "max_tokens": self.settings.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            },
        }
        if target.api_base:
            kwargs["api_base"] = target.api_base
        limit = self.settings.request_timeout_seconds
        kwargs["timeout"] = limit  # LiteLLM's own limit
        # Our hard limit, a little later, in case a provider ignores LiteLLM's.
        return await asyncio.wait_for(
            self.completion(**kwargs), timeout=limit + TIMEOUT_GRACE_SECONDS
        )

    async def _finish(
        self,
        run_id: str,
        agent: str,
        model: str,
        price: Price,
        response: Any,
        started: float,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float,
    ) -> str:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached_tokens = getattr(details, "cached_tokens", 0) or 0
        content = response.choices[0].message.content or ""

        await self.cost_log.record(
            CallRecord(
                run_id=run_id,
                agent=agent,
                model=model,
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                list_price_usd=price.cost(input_tokens, cached_tokens, output_tokens),
                latency_ms=int((time.monotonic() - started) * 1000),
                cache_hit=False,
            )
        )
        # Only cache output that validates, so a bad answer isn't replayed forever.
        try:
            schema.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError):
            return content
        await self.cache.put(
            cache_key(model, messages, schema, temperature),
            CachedResponse(model, content, input_tokens, output_tokens),
        )
        return content
