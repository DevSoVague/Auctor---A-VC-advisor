"""The memo on Person 3's grounded1to8 pipeline: citation events, pitch intake, run API wiring.

tests/fixtures/memo/p3g_stub_state.json is a real stub run of person3/grounded1to8 (0 tokens,
fake fx: sources) with cite_mode 'node': 38 citation checks recorded as events.
"""

import copy
import json
from pathlib import Path

import pytest
from docx import Document

from frontier.memo.build import write_memo
from frontier.memo.draft import draft_from_numbers
from frontier.memo.inputs import from_graph_state, from_result_json

STATE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/memo/p3g_stub_state.json").read_text()
)
INTAKE = {  # shape of person3/grounded1to8 agents/analysis/a0_intake.IntakeReport
    "accepted": True,
    "reasons": [],
    "warnings": ["1 cited item is not in the store yet"],
    "source": "pitch.pdf",
    "kind": "pdf",
    "submitted": "2026-11-08",
    "cited": [
        {"ref": "NCT05166889", "kind": "trial", "status": "stored", "id": "nct:NCT05166889"},
        {"ref": "PMID: 40651490", "kind": "paper", "status": "missing"},
    ],
    "profile": {
        "company": "AiroBio Inc.",
        "indication": "moderate-to-severe COPD",
        "mechanism": "inhaled PDE4B inhibitor",
        "targets": ["PDE4B"],
        "modality": "small molecule",
        "stage": "Phase 2",
        "key_claims": [{"text": "Exacerbations fell 30%", "cited": ["NCT05166889"]}],
    },
}


@pytest.fixture
def state():
    return copy.deepcopy(STATE)


def test_grounded_run_becomes_a_complete_memo_input(state):
    inp = from_graph_state(state)
    assert [h.id for h in inp.survivors] == ["h1", "h2", "h3", "h4"]
    assert inp.hypotheses[0].landscape and inp.hypotheses[0].judgment
    assert inp.ledger and inp.gaps == []


def test_ledger_keeps_only_the_last_check_per_agent_and_hypothesis(state):
    first = copy.deepcopy(state["citation_checks"][0])
    first["entries"] = [{**e, "supports": "no", "action": "remove"} for e in first["entries"]]
    state["citation_checks"].insert(0, first)  # an earlier check, before a restart fixed it

    rows = [r for r in from_graph_state(state).ledger if r.agent == first["agent"]]
    assert rows and all(r.supports == "yes" for r in rows)


def test_ledger_read_from_live_events_too(state):
    """The run API's live state has 'events', not result.json's 'citation_checks'."""
    state["events"] = [["agent_done", {}]] + [
        ["citation_check", c] for c in state.pop("citation_checks")
    ]
    assert from_graph_state(state).ledger


def test_pitch_is_read_and_shown(state, tmp_path):
    state["intake"] = INTAKE
    inp = from_graph_state(state)
    assert inp.pitch and inp.pitch.profile.company == "AiroBio Inc."

    draft = draft_from_numbers(inp)
    assert "AiroBio Inc." in draft.bottom_line[0].text

    doc = Document(write_memo(inp, tmp_path, pdf=False)["docx"])
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "The pitch we tested" in text and "1 missing, 1 stored" in text


def test_rejected_pitch_is_a_gap_not_a_section(state, tmp_path):
    state["intake"] = {**INTAKE, "accepted": False, "reasons": ["off topic"], "profile": None}
    inp = from_graph_state(state)
    assert any("pitch rejected (off topic)" in g for g in inp.gaps)
    doc = Document(write_memo(inp, tmp_path, pdf=False)["docx"])
    assert "The pitch we tested" not in "\n".join(p.text for p in doc.paragraphs)


def test_result_json_from_grounded_has_everything(tmp_path, state):
    path = tmp_path / "result.json"
    path.write_text(json.dumps(state))
    inp = from_result_json(path)
    assert inp.ledger and inp.hypotheses[0].landscape and inp.gaps == []


def test_run_api_runs_the_grounded_pipeline():
    from frontier.api import runs as R

    assert R.P3_PIPELINE == "grounded1to8"
    assert R.P3_ROOT.parts[-2:] == ("grounded1to8", "langgraph-person3")
    assert {"pitch", "cite_mode"} <= R.RunRequest.model_fields.keys()
