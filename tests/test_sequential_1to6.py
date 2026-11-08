"""All six agents in one sequential stub run: A4 then A5 feed A6 for every card."""

import asyncio
from datetime import date

from agents.analysis.chain import run_chain
from agents.analysis.context import RunContext, StubLLM
from agents.analysis.retrieval import StubRetrieval
from agents.analysis.schemas import RunConfig
from agents.competitor.records import competitor_stub


def _run():
    as_of = date(2021, 1, 1)
    cfg = RunConfig(disease="COPD", disease_id="EFO_0000341", as_of=as_of,
                    mode="single_shot", profile="dev")
    ctx = RunContext(config=cfg, retrieval=StubRetrieval(as_of), llm=StubLLM())
    ctx.competitor_retrieval = competitor_stub(as_of)
    return ctx, asyncio.run(run_chain(ctx))


def test_order_is_a1_a2_then_a3_a4_a5_a6_per_card():
    ctx, res = _run()
    started = [(p["agent"], p["hypothesis_id"]) for _, e, p in ctx.bus.log if e == "agent_started"]
    assert [a for a, _ in started[:2]] == ["a1_disease", "a2_mechanism"]
    for card in res["cards"]:
        seq = [a for a, h in started if h == card.id]
        assert seq[:4] == ["a3_profile", "a4_inactive", "a5_active", "a6_redteam"], (card.id, seq)
    # one card at a time: once a card's A6 has run, that card never appears again
    from itertools import groupby

    runs = [h for h, _ in groupby(h for _, h in started[2:])]
    assert len(runs) == len(set(runs)), runs


def test_a4_a5_outputs_reach_a6_and_crowding_signal():
    ctx, res = _run()
    for r in res["results"].values():
        assert r["inactive"] is not None and r["landscape"] is not None
    h1 = res["results"]["h1"]
    assert h1["landscape"].programs and (h1["inactive"].repurposing or h1["inactive"].shelved)
    assert set(res["signals"]["crowding"]) == {c.id for c in res["cards"]}
    a6_prompts = [c for c in ctx.llm.calls if c["agent"] == "a6_redteam"]
    assert a6_prompts and "crowding_score" in a6_prompts[0]["messages"][-1]["content"]
