"""HOOK IN (person 2): RunContext + ctx.llm. Our stand-ins until core/ lands.

Person 2's real RunContext only needs to provide the same attributes:
    run_id, config, retrieval, llm(agent=, messages=, schema=), emit(event, payload),
    feedback (str | None), signals (dict)
Anything with those attributes works with our agents (duck typing, no import needed).

LLM modes (env LLM_MODE):
    stub -> returns tests/fixtures/<agent>_out.json, no network
    live -> Nebius Token Factory (OpenAI-compatible), JSON-schema structured output
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"

# Model per agent, from PRD core/profiles.yaml. Override any entry with env
# NEBIUS_MODEL_<AGENT> (e.g. NEBIUS_MODEL_A2_MECHANISM). Copy exact IDs from the Nebius console.
# Real IDs from the Nebius Token Factory /models list (us-central1), probed 2026-11-07 for
# JSON-schema output: DeepSeek-V4-Flash, GLM-5.1, Nemotron-3.5-Lightning OK first try;
# Qwen3.5-397B OK after 1 retry; Kimi-K2.6 failed schema twice (not used).
PROFILES: dict[str, dict[str, str]] = {
    "dev": {
        "default": "deepseek-ai/DeepSeek-V4-Flash-0731",
    },
    "final": {
        "default": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "a1_disease": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "a2_mechanism": "Qwen/Qwen3.5-397B-A17B",
        "a3_profile": "Qwen/Qwen3.5-397B-A17B",
        "a6_redteam": "zai-org/GLM-5.1",  # different family from the writers -> independent critique
    },
}


def model_for(agent: str, profile: str) -> str:
    env = os.getenv(f"NEBIUS_MODEL_{agent.upper()}")
    if env:
        return env
    p = PROFILES[profile]
    return p.get(agent, p["default"])


class LLMError(RuntimeError):
    pass


# ------------------------------------------------------------------------- events
class EventBus:
    """HOOK OUT: ctx.emit(event, payload). Subscribers: flow status, API stream, workbook."""

    def __init__(self) -> None:
        self._subs: list[Callable[[str, dict], None]] = []
        self.log: list[tuple[float, str, dict]] = []

    def subscribe(self, fn: Callable[[str, dict], None]) -> None:
        self._subs.append(fn)

    def emit(self, event: str, payload: dict) -> None:
        self.log.append((time.time(), event, payload))
        for fn in self._subs:
            fn(event, payload)


# ------------------------------------------------------------------------- usage
@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    model: str = ""


# ------------------------------------------------------------------------- LLMs
def _schema_for(schema: type[BaseModel]) -> dict:
    return {"type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}


class BudgetExceeded(LLMError):
    """Spend cap hit. The run stops; nothing else is called."""


@dataclass
class Budget:
    """Optional per-run spend cap. OFF by default (0 = unlimited); usage is always counted.
    Turn on with env MAX_CALLS_PER_RUN, MAX_OUTPUT_TOKENS_PER_RUN, MAX_TOTAL_TOKENS_PER_RUN,
    MAX_TOKENS_PER_CALL."""

    max_calls: int = int(os.getenv("MAX_CALLS_PER_RUN", 0))
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS_PER_RUN", 0))
    max_total_tokens: int = int(os.getenv("MAX_TOTAL_TOKENS_PER_RUN", 0))
    max_tokens_per_call: int = int(os.getenv("MAX_TOKENS_PER_CALL", 0))
    calls: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def before_call(self, requested: int) -> int:
        if self.max_calls and self.calls >= self.max_calls:
            raise BudgetExceeded(f"call cap reached ({self.max_calls})")
        cap = requested
        if self.max_output_tokens:
            left = self.max_output_tokens - self.output_tokens
            if left <= 0:
                raise BudgetExceeded(f"output-token cap reached ({self.max_output_tokens})")
            cap = min(cap, left)
        if self.max_total_tokens and self.total_tokens >= self.max_total_tokens:
            raise BudgetExceeded(f"total-token cap reached ({self.max_total_tokens})")
        if self.max_tokens_per_call:
            cap = min(cap, self.max_tokens_per_call)
        self.calls += 1
        return max(256, cap)

    def after_call(self, inp: int, out: int) -> None:
        self.output_tokens += out
        self.total_tokens += inp + out

    def summary(self) -> dict:
        return {"calls": f"{self.calls}/{self.max_calls}",
                "output_tokens": f"{self.output_tokens}/{self.max_output_tokens}",
                "total_tokens": f"{self.total_tokens}/{self.max_total_tokens}"}


class NebiusLLM:
    """Live shim: OpenAI SDK pointed at Nebius Token Factory, with a spend cap and a trace."""

    def __init__(self, profile: str = "dev", budget: Budget | None = None,
                 model_override: str | None = None) -> None:
        from openai import AsyncOpenAI

        local = os.getenv("LOCAL_LLM_BASE_URL")
        key = (os.getenv("LOCAL_LLM_API_KEY") or "local") if local else os.getenv("NEBIUS_API_KEY")
        base = local or os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.us-central1.nebius.com/v1/")
        if not key:
            raise LLMError("NEBIUS_API_KEY is not set (put it in frontier-engine/.env)")
        self.client = AsyncOpenAI(api_key=key.strip(), base_url=base, timeout=300, max_retries=1)
        self.profile = profile
        self.budget = budget or Budget()
        self.model_override = model_override  # force one model for every agent (model comparison)
        self.usage: dict[str, Usage] = defaultdict(Usage)
        self.trace: list[dict] = []  # one entry per HTTP call: raw output, tokens, latency, error
        self.on_stream: Callable[[str, dict], None] | None = None
        # json_schema is tried first; models that reject it fall back to json_object.
        self._json_object_only: set[str] = set()

    async def __call__(self, *, agent: str, messages: list[dict], schema: type[BaseModel],
                       temperature: float = 0.2, max_tokens: int = 16000) -> BaseModel:
        model = self.model_override or model_for(agent, self.profile)
        msgs = list(messages)
        last_err: Exception | None = None
        for attempt in range(2):  # PRD: retry once with the error message, then fail loudly
            text = await self._complete(agent, model, msgs, schema, temperature, max_tokens, attempt)
            try:
                out = schema.model_validate_json(_strip_fences(text))
                self.trace[-1]["valid"] = True
                return out
            except ValidationError as e:
                last_err = e
                self.trace[-1]["valid"] = False
                self.trace[-1]["validation_error"] = str(e)[:2000]
                msgs = msgs + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Your JSON failed validation. Fix it and return "
                     f"only the corrected JSON object.\nErrors:\n{e}"},
                ]
        raise LLMError(f"{agent}: output failed schema validation twice: {last_err}")

    async def _complete(self, agent, model, msgs, schema, temperature, max_tokens, attempt) -> str:
        cap = self.budget.before_call(max_tokens)
        t0 = time.time()
        rec: dict[str, Any] = {"agent": agent, "model": model, "attempt": attempt,
                               "max_tokens": cap, "messages": msgs}
        self.trace.append(rec)
        kw: dict[str, Any] = dict(model=model, messages=msgs, temperature=temperature,
                                  max_tokens=cap)
        try:
            resp = None
            if model not in self._json_object_only:
                try:
                    resp = await self._request(
                        **kw, response_format=_schema_for(schema))
                except Exception as e:  # noqa: BLE001 - provider-specific 400s
                    if "response_format" not in str(e) and "json_schema" not in str(e):
                        raise
                    self._json_object_only.add(model)
            if resp is None:
                rec["mode"] = "json_object"
                kw["messages"] = msgs + [{"role": "user", "content":
                    "Return only a JSON object matching this JSON schema:\n"
                    + json.dumps(schema.model_json_schema())}]
                resp = await self._request(
                    **kw, response_format={"type": "json_object"})
            else:
                rec["mode"] = "json_schema"
        except Exception as e:
            rec.update(error=f"{type(e).__name__}: {str(e)[:500]}",
                       latency_ms=int((time.time() - t0) * 1000))
            raise
        inp = (resp.usage.prompt_tokens or 0) if resp.usage else 0
        out = (resp.usage.completion_tokens or 0) if resp.usage else 0
        det = getattr(resp.usage, "prompt_tokens_details", None) if resp.usage else None
        cached = (getattr(det, "cached_tokens", 0) or 0) if det else 0
        self.budget.after_call(inp, out)
        msg = resp.choices[0].message
        text = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", None) or ""
        rec.update(raw=text, reasoning=reasoning, reasoning_chars=len(reasoning),
                   finish_reason=resp.choices[0].finish_reason, input_tokens=inp,
                   output_tokens=out, cached_tokens=cached,
                   latency_ms=int((time.time() - t0) * 1000))
        u = self.usage[agent]
        u.calls += 1
        u.model = model
        u.latency_ms += rec["latency_ms"]
        u.input_tokens += inp
        u.output_tokens += out
        u.cached_tokens += cached
        return text

    async def _request(self, **kw):
        """Keep ordinary callers unchanged; observed runs consume real SDK deltas."""
        if self.on_stream is None:
            return await self.client.chat.completions.create(**kw)
        from types import SimpleNamespace

        self.on_stream("request", {"messages": kw["messages"], "model": kw["model"],
                                    "response_format": kw["response_format"]})
        stream = await self.client.chat.completions.create(
            **kw, stream=True, stream_options={"include_usage": True})
        text, reasoning, usage, finish = [], [], None, None
        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = chunk.usage
                for choice in chunk.choices:
                    if choice.index != 0:
                        continue
                    delta = choice.delta
                    content = delta.content or ""
                    thought = (getattr(delta, "reasoning_content", None)
                               or getattr(delta, "reasoning", None) or "")
                    if isinstance(thought, str) and thought:
                        reasoning.append(thought)
                        self.on_stream("delta", {"channel": "reasoning", "text": thought})
                    if content:
                        text.append(content)
                        self.on_stream("delta", {"channel": "output", "text": content})
                    if choice.finish_reason:
                        finish = choice.finish_reason
            if finish is None:
                raise LLMError("Model stream ended without a finish reason")
        finally:
            await stream.close()
        self.on_stream("response", {"finish_reason": finish,
                                    "usage": usage.model_dump() if usage else None})
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(
            message=SimpleNamespace(content="".join(text), reasoning_content="".join(reasoning)),
            finish_reason=finish)])


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


class StubLLM:
    """LLM_MODE=stub: returns the fixture output for the agent.

    Fixture files are tests/fixtures/<agent>_out.json. For per-hypothesis agents (a3, a6)
    the file is {hypothesis_id: output}; we pick the id mentioned in the last message.
    """

    def __init__(self, fixtures: Path = FIXTURES) -> None:
        self.fixtures = fixtures
        self.usage: dict[str, Usage] = defaultdict(Usage)
        self.calls: list[dict] = []  # for tests: what prompts were sent

    async def __call__(self, *, agent: str, messages: list[dict], schema: type[BaseModel],
                       **_: Any) -> BaseModel:
        self.calls.append({"agent": agent, "messages": messages})
        self.usage[agent].calls += 1
        self.usage[agent].model = "stub"
        data = json.loads((self.fixtures / f"{agent}_out.json").read_text())
        if agent in ("a3_profile", "a6_redteam"):
            last = messages[-1]["content"]
            key = next((k for k in data if f'"{k}"' in last), None)
            if key is None:
                raise LLMError(f"stub: no {agent} fixture for any hypothesis in the prompt")
            data = data[key]
        if agent == "a2_mechanism":
            data = {"cards": data}
        return schema.model_validate(data)


async def complete_raw(llm, *, agent: str, messages: list[dict], schema: type[BaseModel],
                       temperature: float = 0.2, max_tokens: int = 16000) -> dict:
    """One model call WITHOUT parsing, for step-by-step runners (Langflow/LangGraph nodes).

    Returns {text, reasoning, model, mode, finish_reason, input_tokens, output_tokens,
    cached_tokens, latency_ms}. Parsing/validation is a separate step (parse_output).
    """
    if isinstance(llm, StubLLM):
        t0 = time.time()
        out = await llm(agent=agent, messages=messages, schema=schema)
        return {"text": out.model_dump_json(indent=1), "reasoning": "", "model": "stub",
                "mode": "fixture", "finish_reason": "stop", "input_tokens": 0, "output_tokens": 0,
                "cached_tokens": 0, "latency_ms": int((time.time() - t0) * 1000)}
    model = getattr(llm, "model_override", None) or model_for(agent, llm.profile)
    text = await llm._complete(agent, model, list(messages), schema, temperature, max_tokens, 0)
    rec = llm.trace[-1]
    return {"text": text, "reasoning": rec.get("reasoning", ""), "model": model,
            "mode": rec.get("mode"), "finish_reason": rec.get("finish_reason"),
            **{k: rec.get(k, 0) for k in ("input_tokens", "output_tokens", "cached_tokens", "latency_ms")}}


def parse_output(text: str, schema: type[BaseModel]) -> BaseModel:
    """Schema validation step (raises pydantic.ValidationError)."""
    return schema.model_validate_json(_strip_fences(text))


def fix_messages(messages: list[dict], bad_text: str, error: str) -> list[dict]:
    """The single schema-retry turn (PRD: retry once with the error, then fail loudly)."""
    return list(messages) + [
        {"role": "assistant", "content": bad_text},
        {"role": "user", "content": "Your JSON failed validation. Fix it and return only the "
                                    f"corrected JSON object.\nErrors:\n{error}"}]


def make_llm(profile: str = "dev"):
    return StubLLM() if os.getenv("LLM_MODE", "stub") == "stub" else NebiusLLM(profile)


# ------------------------------------------------------------------------- context
@dataclass
class RunContext:
    config: Any  # RunConfig
    retrieval: Any  # bound to config.as_of
    llm: Callable  # async (agent=, messages=, schema=) -> BaseModel
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    bus: EventBus = field(default_factory=EventBus)
    feedback: str | None = None  # HOOK OUT: reviewer comment for rerun (checkpoint)
    signals: dict = field(default_factory=dict)  # HOOK OUT: side outputs (pub_counts, ...)

    def emit(self, event: str, payload: dict) -> None:
        self.bus.emit(event, {"run_id": self.run_id, **payload})
