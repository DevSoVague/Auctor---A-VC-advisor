"""Person 2's A7 Citation Checker inside Person 3's LangGraph pipeline (stub mode, 0 tokens).

workflow.yaml splices check_citations in after every agent's commit step. These tests
run the whole pipeline and check that every agent's claims were checked, failures are
flagged in the run state, and no agent output was edited.
"""

import asyncio
import json
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

P3 = Path(__file__).resolve().parents[1] / "person3" / "1to6merged" / "langgraph-person3"
sys.path.insert(0, str(P3))

pytest.importorskip("p3graph")
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from p3graph import engine as E  # noqa: E402, F401  (sets up Person 3's import path)
from p3graph import nodes as N  # noqa: E402
from p3graph.graph import build  # noqa: E402

from frontier.agents.citations import p3_node  # noqa: E402


@pytest.fixture(scope="module")
def finished_run():
    llm = E.StubLLM()
    N.LLM_FACTORY = lambda state: llm
    try:
        graph = build(InMemorySaver())
        config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 400}
        inp = {"config": {"as_of": "2021-01-01", "mode": "single_shot"}, "llm": {"mode": "stub"}}

        async def drive():
            async for _ in graph.astream(inp, config, stream_mode="updates"):
                pass
            return (await graph.aget_state(config)).values

        yield asyncio.run(drive())
    finally:
        N.LLM_FACTORY = None


def test_citation_step_runs_after_every_agent(finished_run):
    steps = [s["node"] for s in finished_run["steps"]]
    for agent in ("a1", "a2", "a3", "a4", "a5", "a6"):
        assert f"a7c_after_{agent}" in steps, f"no citation check after {agent}"
    # each check comes right after its agent's commit
    for i, node in enumerate(steps):
        if node.startswith("a7c_after_"):
            assert steps[i - 1] == f"{node.removeprefix('a7c_after_')}_commit"


def test_every_agent_has_a_citation_ledger_in_the_run(finished_run):
    signals = finished_run["signals"]
    assert "citations:a1_disease" in signals and "citations:a2_mechanism" in signals
    processed = {k.split(":")[2] for k in signals if k.startswith("citations:a3_profile:")}
    assert processed, "no hypothesis went through A3"
    for hid in processed:
        for agent in ("a3_profile", "a4_inactive", "a5_active", "a6_redteam"):
            if f"citations:{agent}:{hid}" not in signals:
                continue  # a killed card may stop early
            report = signals[f"citations:{agent}:{hid}"]
            assert report["checked_pairs"] == len(report["entries"])
    # every agent's claims were actually found and checked somewhere in the run
    for agent in (
        "a1_disease",
        "a2_mechanism",
        "a3_profile",
        "a4_inactive",
        "a5_active",
        "a6_redteam",
    ):
        pairs = sum(
            r["checked_pairs"] for k, r in signals.items() if k.startswith(f"citations:{agent}")
        )
        assert pairs > 0, f"no source checks for {agent}: its claims were not found in the state"
    a1 = signals["citations:a1_disease"]
    assert {e["action"] for e in a1["entries"]} <= {"keep", "downgrade", "remove"}


def test_step_trace_says_what_was_checked(finished_run):
    detail = next(s["detail"] for s in finished_run["steps"] if s["node"] == "a7c_after_a1")
    assert "a1_disease" in detail and "source checks" in detail and "stub" in detail


def test_flagging_never_edits_agent_output(finished_run):
    """Every claim the agents wrote is still in their outputs after the checks."""
    a1_claims = {c["id"] for c in p3_node.claims_in(finished_run["why_now"])}
    checked = {e["claim_id"] for e in finished_run["signals"]["citations:a1_disease"]["entries"]}
    invalid = {
        p["claim_id"] for p in finished_run["signals"]["citations:a1_disease"]["invalid_claims"]
    }
    sourced = {c["id"] for c in p3_node.claims_in(finished_run["why_now"]) if c["source_ids"]}
    assert sourced <= checked | invalid
    assert checked <= a1_claims


# --- the pieces, without the graph ----------------------------------------------------


class DictStore:
    """Shaped like Person 3's search: doc() returns a dict or None."""

    def __init__(self, docs):
        self.docs = {d["id"]: d for d in docs}

    async def doc(self, doc_id):
        return self.docs.get(doc_id)


def test_record_view_reads_person3_dicts():
    view = p3_node.RecordView(
        DictStore([{"id": "pmid:1", "source_date": "2019-05-14", "title": "T", "body": "B"}])
    )
    rec = asyncio.run(view.doc("pmid:1"))
    assert (rec.id, rec.source_date, rec.title, rec.body) == ("pmid:1", date(2019, 5, 14), "T", "B")
    assert asyncio.run(view.doc("pmid:2")) is None
    assert (
        asyncio.run(view.source_date("pmid:2")) is None
    )  # no date-only lookup: reads as not_found


def test_claims_breaking_the_contract_are_flagged_not_dropped_silently():
    output = {
        "shifts": [
            {
                "claims": [
                    {
                        "id": "ok",
                        "agent": "a1_disease",
                        "text": "t",
                        "kind": "evidence",
                        "confidence": 0.5,
                        "source_ids": ["pmid:1"],
                    }
                ]
            },
            {
                "claims": [
                    {
                        "id": "bad",
                        "agent": "a1_disease",
                        "text": "t",
                        "kind": "evidence",
                        "confidence": 0.5,
                        "source_ids": [],
                    }
                ]
            },
        ]
    }
    claims, problems = p3_node._split_claims(output)
    assert [c.id for c in claims] == ["ok"]
    assert problems[0]["claim_id"] == "bad" and "source" in problems[0]["problem"]


def test_stub_judge_answers_every_pair_it_is_asked():
    pairs = [{"claim_id": "c1", "source_id": "pmid:1"}, {"claim_id": "c2", "source_id": "pmid:2"}]
    msg = [{"role": "user", "content": json.dumps({"pairs": pairs})}]
    ledger = asyncio.run(p3_node.StubJudge()(agent="a7_citations", messages=msg, schema=None))
    assert [(e.claim_id, e.supports) for e in ledger.entries] == [("c1", "yes"), ("c2", "yes")]
