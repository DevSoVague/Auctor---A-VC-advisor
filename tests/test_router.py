"""Router tests (PRD: cache hit on a repeated call, fallback on a simulated 429,
BudgetExceeded at the cap), plus the edge cases around them. No network calls."""

import asyncio
import os
from types import SimpleNamespace

import psycopg
import pytest

from frontier.agents.schemas import AGENTS, ProductProfile
from frontier.core.cache import MemoryCache, PostgresCache, cache_key
from frontier.core.config import Settings, load_yaml
from frontier.core.costs import MemoryCostLog, UnpricedModel, load_prices
from frontier.core.router import (
    AllModelsFailed,
    BudgetExceeded,
    ModelNotConfigured,
    Router,
    is_retryable,
)

GOOD = open(os.path.join(os.path.dirname(__file__), "fixtures", "a3_profile.output.json")).read()
RUN = "00000000-0000-0000-0000-000000000001"
MESSAGES = [{"role": "user", "content": "profile for h1"}]


class StatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FakeLLM:
    """Plays a script of outcomes: an exception to raise or JSON text to return."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.script.pop(0) if self.script else GOOD
        if isinstance(outcome, Exception):
            raise outcome
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=500,
            prompt_tokens_details=SimpleNamespace(cached_tokens=200),
        )
        message = SimpleNamespace(content=outcome)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


async def no_sleep(_seconds):
    return None


PRICES = {
    "model-a": {"input": 1.0, "cached_input": 0.5, "output": 2.0},
    "fb-1": {"input": 1.0, "output": 2.0},
}


def make_router(llm=None, prices=PRICES, aliases=None, **settings):
    settings = Settings(
        _env_file=None,
        nebius_api_key="nebius-test",
        nebius_base_url="https://nebius.invalid/v1",
        openai_api_key="openai-test",
        **settings,
    )
    router = Router(
        settings=settings,
        profiles={
            "dev": {"default": "model-a"},
            "final": {"a3_profile": "model-a"},
            "fallbacks": ["fb-1"],
        },
        prices=load_prices(prices),
        providers={
            "aliases": aliases
            or {"fb-1": {"model": "openai/fb-model", "api_key_env": "OPENAI_API_KEY"}}
        },
        cache=MemoryCache(),
        cost_log=MemoryCostLog(),
        completion=llm or FakeLLM(),
        sleep=no_sleep,
    )
    return router


def call(router, messages=MESSAGES, agent="a3_profile"):
    return asyncio.run(
        router.complete(run_id=RUN, agent=agent, messages=messages, schema=ProductProfile)
    )


# --- the three router tests the PRD asks for ---------------------------------------


def test_repeated_call_is_a_cache_hit():
    llm = FakeLLM()
    router = make_router(llm)
    first, second = call(router), call(router)
    assert first == second == GOOD
    assert len(llm.calls) == 1
    hit, miss = router.cost_log.rows[1], router.cost_log.rows[0]
    assert (miss.cache_hit, hit.cache_hit) == (False, True)
    assert hit.list_price_usd == 0.0


def test_429_retries_once_then_walks_to_the_fallback():
    llm = FakeLLM(StatusError(429), StatusError(429), GOOD)
    router = make_router(llm)
    assert call(router) == GOOD
    assert [c["model"] for c in llm.calls] == [
        "openai/model-a",
        "openai/model-a",
        "openai/fb-model",
    ]
    assert router.cost_log.rows[-1].model == "fb-1"


def test_budget_exceeded_at_the_call_cap():
    llm = FakeLLM()
    router = make_router(llm, max_calls_per_run=2)
    call(router, [{"role": "user", "content": "one"}])
    call(router, [{"role": "user", "content": "two"}])
    with pytest.raises(BudgetExceeded, match="MAX_CALLS_PER_RUN=2"):
        call(router, [{"role": "user", "content": "three"}])
    assert len(llm.calls) == 2


# --- edge cases ---------------------------------------------------------------------


def test_429_once_then_success_stays_on_the_primary():
    llm = FakeLLM(StatusError(429), GOOD)
    router = make_router(llm)
    assert call(router) == GOOD
    assert [c["model"] for c in llm.calls] == ["openai/model-a", "openai/model-a"]


def test_5xx_is_retryable_but_4xx_is_not():
    assert is_retryable(StatusError(503)) and is_retryable(StatusError(429))
    assert not is_retryable(StatusError(400)) and not is_retryable(StatusError(401))


def test_non_retryable_error_raises_immediately():
    llm = FakeLLM(StatusError(400))
    with pytest.raises(StatusError):
        call(make_router(llm))
    assert len(llm.calls) == 1


def test_every_model_failing_raises_with_the_reasons():
    llm = FakeLLM(StatusError(500), StatusError(500), StatusError(500))
    with pytest.raises(AllModelsFailed, match="fb-1 attempt 1"):
        call(make_router(llm))


def test_unconfigured_fallback_is_skipped_and_reported():
    llm = FakeLLM(StatusError(429), StatusError(429))
    aliases = {"fb-1": {"model": "openai/REPLACE-ME", "api_key_env": "OPENAI_API_KEY"}}
    with pytest.raises(AllModelsFailed, match="model ID not set"):
        call(make_router(llm, aliases=aliases))
    assert len(llm.calls) == 2


def test_budget_exceeded_at_the_usd_cap():
    expensive = {"model-a": {"input": 10_000.0, "output": 10_000.0}}
    router = make_router(FakeLLM(), prices=expensive, max_usd_per_run=5.0)
    call(router, [{"role": "user", "content": "one"}])  # costs $15, crossing the cap
    with pytest.raises(BudgetExceeded, match="MAX_USD_PER_RUN"):
        call(router, [{"role": "user", "content": "two"}])


def test_cost_comes_from_prices_yaml():
    router = make_router(FakeLLM())
    call(router)
    row = router.cost_log.rows[0]
    # 800 uncached input at $1, 200 cached at $0.50, 500 output at $2, per 1M tokens
    assert row.list_price_usd == pytest.approx((800 * 1.0 + 200 * 0.5 + 500 * 2.0) / 1e6)
    assert (row.input_tokens, row.cached_tokens, row.output_tokens) == (1000, 200, 500)


def test_unpriced_model_fails_before_paying():
    llm = FakeLLM()
    with pytest.raises(UnpricedModel, match="model-a"):
        call(make_router(llm, prices={}))
    assert llm.calls == []


def test_invalid_output_is_returned_but_not_cached():
    llm = FakeLLM('{"not": "a profile"}', '{"not": "a profile"}')
    router = make_router(llm)
    assert call(router) == '{"not": "a profile"}'
    call(router)
    assert len(llm.calls) == 2  # no cache hit for the bad answer


def test_request_carries_the_caps_and_structured_output():
    llm = FakeLLM()
    call(make_router(llm, max_output_tokens=1234))
    sent = llm.calls[0]
    assert sent["max_tokens"] == 1234
    assert sent["temperature"] == 0.0
    assert sent["api_base"] == "https://nebius.invalid/v1"
    assert sent["response_format"]["json_schema"]["name"] == "ProductProfile"


def test_agent_model_overrides_the_profile_default():
    router = make_router(profile="final")
    assert router.model_for("a3_profile") == "model-a"
    with pytest.raises(ModelNotConfigured):
        router.model_for("a1_disease")  # final has no default


def test_missing_nebius_settings_are_reported():
    router = make_router()
    router.settings.nebius_api_key = ""
    router.aliases = {}
    router.profiles["fallbacks"] = []
    with pytest.raises(AllModelsFailed, match="NEBIUS_API_KEY"):
        call(router)


def test_cache_key_changes_with_any_input():
    base = cache_key("m", MESSAGES, ProductProfile, 0.0)
    assert base == cache_key("m", MESSAGES, ProductProfile, 0.0)
    assert base != cache_key("other", MESSAGES, ProductProfile, 0.0)
    assert base != cache_key("m", [{"role": "user", "content": "x"}], ProductProfile, 0.0)
    assert base != cache_key("m", MESSAGES, AGENTS["a1_disease"][1], 0.0)
    assert base != cache_key("m", MESSAGES, ProductProfile, 0.7)


@pytest.mark.parametrize("profile", ["dev", "final"])
def test_real_profiles_give_every_agent_a_model(profile):
    router = Router(
        settings=Settings(_env_file=None, profile=profile),
        profiles=load_yaml("profiles"),
        prices={},
        providers=load_yaml("providers"),
        cache=MemoryCache(),
        cost_log=MemoryCostLog(),
    )
    for agent in AGENTS:
        assert router.model_for(agent)


# --- Postgres (runs only when the database and the llm_cache table exist) ------------


def _llm_cache_table_exists() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            return conn.execute("SELECT to_regclass('llm_cache')").fetchone()[0] is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _llm_cache_table_exists(), reason="needs Postgres with llm_cache")
def test_postgres_cache_round_trip():
    from frontier.core.cache import CachedResponse

    cache = PostgresCache(Settings().database_url)
    key = cache_key("pytest-model", MESSAGES, ProductProfile, 0.0)
    asyncio.run(cache.put(key, CachedResponse("pytest-model", GOOD, 1, 2)))
    assert asyncio.run(cache.get(key)).response == GOOD
