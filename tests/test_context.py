"""RunContext and the stub retrieval client."""

import asyncio
from datetime import date

import pytest

from frontier.agents.schemas import AGENTS, ProductProfile, RunConfig
from frontier.core.context import RunContext, SchemaError, StubLLM, load_fixture, make_context
from frontier.core.retrieval import StubRetrievalClient
from frontier.core.router import Router

BACKTEST = date(2021, 1, 1)


def config(as_of: date = date(2026, 11, 7)) -> RunConfig:
    return RunConfig.model_validate({**load_fixture("a1_disease.input"), "as_of": as_of})


@pytest.mark.parametrize("agent", AGENTS)
def test_stub_llm_returns_each_agents_fixture(agent, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "stub")
    _, output_model = AGENTS[agent]
    ctx = make_context(config())
    out = asyncio.run(ctx.llm(agent=agent, messages=[], schema=output_model))
    assert out == output_model.model_validate(load_fixture(f"{agent}.output"))


def test_live_mode_uses_the_router(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    assert isinstance(make_context(config()).backend, Router)


class FlakyBackend:
    """Returns bad JSON first, then the a3 fixture; records what it was sent."""

    def __init__(self, bad_times: int):
        self.bad_times = bad_times
        self.calls: list[list[dict]] = []

    async def complete(self, *, run_id, agent, messages, schema):
        self.calls.append(messages)
        if len(self.calls) <= self.bad_times:
            return '{"hypothesis_id": "h1"}'
        return await StubLLM().complete(
            run_id=run_id, agent=agent, messages=messages, schema=schema
        )


def test_invalid_output_is_retried_once_with_the_error():
    backend = FlakyBackend(bad_times=1)
    ctx = RunContext(config=config(), retrieval=StubRetrievalClient(BACKTEST), backend=backend)
    out = asyncio.run(ctx.llm(agent="a3_profile", messages=[], schema=ProductProfile))
    assert out.hypothesis_id == "h1"
    assert len(backend.calls) == 2
    assert "failed schema validation" in backend.calls[1][-1]["content"]


def test_invalid_output_twice_fails_loudly():
    backend = FlakyBackend(bad_times=2)
    ctx = RunContext(config=config(), retrieval=StubRetrievalClient(BACKTEST), backend=backend)
    with pytest.raises(SchemaError, match="validation twice"):
        asyncio.run(ctx.llm(agent="a3_profile", messages=[], schema=ProductProfile))


# Leakage: at the backtest cutoff, no endpoint may return a record dated on or after it.

ENDPOINT_CALLS = {
    "search": lambda c: c.search("fixture", k=100),
    "trials": lambda c: c.trials("COPD"),
    "targets": lambda c: c.targets("EFO_0000341"),
    "drugs": lambda c: c.drugs(),
    "approvals": lambda c: c.approvals("COPD"),
    "filings": lambda c: c.filings("Fixture Bio"),
    "retractions": lambda c: c.retractions("10.0000/fixture.0001"),
}


@pytest.mark.parametrize("endpoint", ENDPOINT_CALLS)
def test_no_endpoint_leaks_records_from_after_the_cutoff(endpoint):
    hits = asyncio.run(ENDPOINT_CALLS[endpoint](StubRetrievalClient(BACKTEST)))
    assert hits, f"{endpoint} returned nothing, so the leakage check would be vacuous"
    assert all(h.source_date < BACKTEST for h in hits)


@pytest.mark.parametrize("endpoint", [e for e in ENDPOINT_CALLS if e != "retractions"])
def test_live_date_sees_later_records(endpoint):
    """The same calls at the live date return more, so the cutoff is what hides them."""
    early = asyncio.run(ENDPOINT_CALLS[endpoint](StubRetrievalClient(BACKTEST)))
    live = asyncio.run(ENDPOINT_CALLS[endpoint](StubRetrievalClient(date(2026, 11, 7))))
    assert len(live) > len(early)


def test_retraction_after_the_cutoff_is_hidden():
    later_doi = "10.0000/fixture.0002"  # retracted 2022-09-01
    assert not asyncio.run(StubRetrievalClient(BACKTEST).retractions(later_doi))
    assert asyncio.run(StubRetrievalClient(date(2026, 11, 7)).retractions(later_doi))


def test_record_dated_exactly_on_the_cutoff_is_hidden():
    client = StubRetrievalClient(BACKTEST)
    assert asyncio.run(client.doc("pmid:FIXTURE-0002")) is None
    assert asyncio.run(client.doc("pmid:FIXTURE-0001")) is not None
