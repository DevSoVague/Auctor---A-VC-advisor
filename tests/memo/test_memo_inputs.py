"""MemoInput: the one bundle A8 reads, gathered from every hypothesis branch.

tests/fixtures/memo/p3_stub_state.json is a real stub run of Person 3's pipeline (0 tokens,
fake fx: sources): 5 hypotheses, h5 killed, the rest survive after one revise round.
"""

import copy
import json
from pathlib import Path

import pytest

from frontier.agents.schemas import A8Input, RunConfig
from frontier.core.context import load_fixture
from frontier.memo.inputs import from_a8_input, from_graph_state, from_result_json

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
STATE = json.loads((FIXTURES / "memo" / "p3_stub_state.json").read_text())


@pytest.fixture
def state():
    return copy.deepcopy(STATE)


def test_stub_run_becomes_a_complete_memo_input(state):
    inp = from_graph_state(state)

    assert inp.config.disease == "COPD"
    assert inp.why_now is not None
    assert [h.id for h in inp.hypotheses] == ["h1", "h2", "h3", "h4", "h5"]
    assert [h.id for h in inp.survivors] == ["h1", "h2", "h3", "h4"]
    h1 = inp.hypotheses[0]
    assert h1.profile and h1.inactive and h1.landscape and h1.verdict and h1.judgment
    assert h1.revised is True
    assert inp.gaps == []


def test_killed_hypothesis_keeps_its_reason_and_kill_criterion(state):
    inp = from_graph_state(state)

    h5 = next(h for h in inp.hypotheses if h.id == "h5")
    assert h5.status == "killed"
    assert [k.hypothesis_id for k in inp.killed] == ["h5"]
    assert inp.killed[0].kill_criterion


def test_hypotheses_follow_a2_rank_however_branches_finished(state):
    state["cards"].reverse()  # parallel branches can join in any order
    inp = from_graph_state(state)
    assert [h.rank for h in inp.hypotheses] == sorted(h.rank for h in inp.hypotheses)
    assert inp.hypotheses[0].id == "h1"


def test_decision_log_covers_every_branch_and_tags_each_row(state):
    inp = from_graph_state(state)

    assert len(inp.decisions) == len(state["decisions"])
    assert {d.hypothesis_id for d in inp.decisions} >= {"h1", "h5"}
    assert any(d.actor == "judge" for d in inp.decisions)  # not allowed by schemas.Decision
    assert all(d.hypothesis_id == "h1" for d in inp.decisions_for("h1"))


def test_ledger_is_tagged_with_agent_and_hypothesis(state):
    inp = from_graph_state(state)

    agents = {r.agent for r in inp.ledger}
    assert {"a1_disease", "a3_profile", "a6_redteam"} <= agents
    assert {r.hypothesis_id for r in inp.ledger if r.agent == "a1_disease"} == {None}
    assert "h1" in {r.hypothesis_id for r in inp.ledger if r.agent == "a3_profile"}


def test_missing_agents_become_gaps_not_errors(state):
    del state["assets"], state["judgments"], state["profiles"]["h2"]
    state["signals"] = {k: v for k, v in state["signals"].items() if not k.startswith("cit")}

    inp = from_graph_state(state)

    assert inp.hypotheses[0].landscape is None
    assert any("A4/A5" in g for g in inp.gaps)
    assert any("A7 Judge" in g for g in inp.gaps)
    assert any(g == "h2: missing A3 profile" for g in inp.gaps)
    assert any("Citation Checker" in g for g in inp.gaps)


def test_invalid_output_is_left_out_with_a_gap(state):
    state["verdicts"]["h3"]["verdict"] = "maybe"

    inp = from_graph_state(state)

    h3 = next(h for h in inp.hypotheses if h.id == "h3")
    assert h3.verdict is None and h3.status == "incomplete"
    assert any(g.startswith("A6 verdict h3: invalid output") for g in inp.gaps)
    assert "h3" not in [h.id for h in inp.survivors]


def test_result_json_without_assets_reports_the_gap(tmp_path, state):
    # Person 3's result.json leaves out assets and judgments today.
    keep = (
        "run_id",
        "config",
        "why_now",
        "cards",
        "grades",
        "profiles",
        "verdicts",
        "killed",
        "decisions",
        "usage",
        "signals",
    )
    path = tmp_path / "result.json"
    path.write_text(json.dumps({k: state[k] for k in keep}))

    inp = from_result_json(path)

    assert len(inp.survivors) == 4
    assert any("A4/A5" in g for g in inp.gaps)


def test_team_contract_a8_input_converts():
    config = RunConfig.model_validate(load_fixture("a1_disease.input"))
    inp = from_a8_input(A8Input.model_validate(load_fixture("a8_orchestrator.input")), config)

    assert inp.survivors and all(h.landscape for h in inp.survivors)
    assert {k.hypothesis_id for k in inp.killed} == {
        h.id for h in inp.hypotheses if h.status == "killed"
    }


def test_live_stub_pipeline_feeds_memo_input():
    """Guards against drift: run Person 3's pipeline now (stub, 0 tokens) and adapt its state."""
    import asyncio

    from langgraph.checkpoint.memory import InMemorySaver

    from frontier.api import runs as R

    async def run() -> dict:
        runner = R.Runner()
        await runner.start(checkpointer=InMemorySaver())
        inp = {
            "config": {**STATE["config"], "mode": "single_shot"},
            "llm": {"mode": "stub", "model": None, "profile": "dev"},
            "retrieval": "stub",
            "search": "corpus",
            "judge": True,
            "max_cards": 2,
        }
        cfg = R.Runner.cfg("memo-input-test")
        await runner.graph.ainvoke(inp, cfg)
        values = (await runner.graph.aget_state(cfg)).values
        await runner.stop()
        return values

    inp = from_graph_state(asyncio.run(run()))

    assert inp.survivors
    assert inp.ledger and inp.decisions
    assert not [g for g in inp.gaps if "invalid output" in g]
