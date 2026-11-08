"""Contract tests: every fixture validates against agents/schemas.py."""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from frontier.agents.schemas import AGENTS, Claim, Decision, JudgeDecision, Memo, ProductProfile
from frontier.core.context import load_fixture
from frontier.core.retrieval import fixtures_dir


@pytest.mark.parametrize("agent", AGENTS)
def test_fixture_input_validates(agent):
    input_model, _ = AGENTS[agent]
    input_model.model_validate(load_fixture(f"{agent}.input"))


@pytest.mark.parametrize("agent", AGENTS)
def test_fixture_output_validates(agent):
    _, output_model = AGENTS[agent]
    output_model.model_validate(load_fixture(f"{agent}.output"))


def test_every_fixture_file_belongs_to_an_agent():
    names = {p.name.split(".")[0] for p in fixtures_dir().glob("*.json")}
    assert names == set(AGENTS)


def test_fixture_source_ids_are_obviously_fake():
    """Fixture citations must never look real, so they can't leak into a memo as evidence."""
    text = "".join(p.read_text() for p in fixtures_dir().rglob("*.json"))
    source_like = re.findall(r'"((?:pmid|nct|fda|sec|pat|ot|chembl|ret):[^"]+)"', text)
    assert source_like, "expected some source IDs in the fixtures"
    real_looking = sorted({s for s in source_like if "FIXTURE" not in s})
    assert not real_looking, f"fixture source IDs must contain FIXTURE: {real_looking}"


def _citations(node):
    """Every value under a source_ids / source_id key, anywhere in a JSON tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "source_ids":
                yield from value
            elif key == "source_id":
                yield value
            else:
                yield from _citations(value)
    elif isinstance(node, list):
        for item in node:
            yield from _citations(item)


def test_fixture_citations_exist_in_the_stub_store():
    records = json.loads((fixtures_dir() / "retrieval" / "records.json").read_text())
    known = {r["id"] for r in records}
    cited = {
        s for p in fixtures_dir().glob("*.json") for s in _citations(json.loads(p.read_text()))
    }
    assert cited, "expected citations in the fixtures"
    assert cited <= known, f"cited but missing from records.json: {sorted(cited - known)}"


def test_evidence_claim_without_source_is_rejected():
    with pytest.raises(ValidationError, match="needs at least one source_id"):
        Claim(id="x", agent="a1_disease", text="t", kind="evidence", confidence=0.5, source_ids=[])


def test_reasoning_claim_may_have_no_source():
    Claim(id="x", agent="a6_redteam", text="t", kind="reasoning", confidence=0.5, source_ids=[])


def test_product_profile_allows_one_or_two_must_haves():
    base = load_fixture("a3_profile.output")
    with pytest.raises(ValidationError):
        ProductProfile.model_validate({**base, "must_haves": []})
    with pytest.raises(ValidationError):
        ProductProfile.model_validate({**base, "must_haves": ["a", "b", "c"]})


def test_memo_needs_two_or_three_pursuits():
    base = load_fixture("a8_orchestrator.output")
    with pytest.raises(ValidationError):
        Memo.model_validate({**base, "pursuits": base["pursuits"][:1]})


P3_JUDGE = (
    Path(__file__).resolve().parents[1] / "person3/1to6merged/tests/fixtures/a7_judge_out.json"
)


def test_person3_judge_output_validates():
    """Person 3's A7 Judge fixture (one decision per hypothesis) fits JudgeDecision."""
    for decision in json.loads(P3_JUDGE.read_text()).values():
        JudgeDecision.model_validate(decision)


def test_decision_accepts_what_the_pipeline_logs():
    Decision(actor="judge", action="revise", target="h1", reason="red team partly_right")
    Decision(actor="reviewer", action="approve_all", target="a3_profile", reason="")
