"""Step 3: the template draft, the charts and the files (memo.docx, memo.pdf, programs.xlsx)."""

import copy
import json
from pathlib import Path

import pytest
from docx import Document
from openpyxl import load_workbook

from frontier.memo.build import write_memo
from frontier.memo.draft import draft_from_numbers
from frontier.memo.inputs import from_graph_state
from frontier.memo.render_pdf import find_soffice

STATE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/memo/p3_stub_state.json").read_text()
)
FRONT = ["Bottom line", "Recommended pursuits", "Upcoming catalysts"]
APPENDIX = [
    "A. Why now (agent 1)",
    "B. Mechanisms (agent 2)",
    "C. Ideal product profiles (agent 3)",
    "D. Assets and competitors (agents 4 and 5)",
    "E. Red team and judge (agent 6, A7 Judge)",
    "F. Decision log",
    "G. Killed hypotheses",
    "H. Citations (Citation Checker)",
    "I. Method and cost",
]


@pytest.fixture
def inp():
    return from_graph_state(copy.deepcopy(STATE))


def test_draft_has_two_or_three_distinct_pursuits(inp):
    draft = draft_from_numbers(inp)
    assert 2 <= len(draft.pursuits) <= 3
    names = [p.name for p in draft.pursuits if p.form != "build"]
    assert len(names) == len(set(names))  # never the same asset twice
    assert draft.to_memo().pursuits  # fits the team contract (schemas.Memo)


def test_each_pursuit_answers_the_three_investor_questions(inp):
    for p in draft_from_numbers(inp).pursuits:
        assert p.why_good and p.what_could_go_wrong and p.return_vs_risk.text
        assert p.next_step and p.gap_to_profile and p.what_would_change_the_call
        assert all(s.claim_ids for s in p.why_good if s.kind == "evidence")


def test_names_come_only_from_the_candidate_menu(inp):
    from frontier.memo.candidates import candidates

    allowed = {c.id for menu in candidates(inp).values() for c in menu}
    assert {p.candidate_id for p in draft_from_numbers(inp).pursuits} <= allowed


def test_catalysts_are_within_twelve_months(inp):
    for c in draft_from_numbers(inp).catalysts:
        assert 0 <= (c.on - inp.config.as_of).days <= 365


def test_write_memo_makes_every_file_and_section(inp, tmp_path):
    files = write_memo(inp, tmp_path, pdf=False)

    doc = Document(files["docx"])
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    for h in FRONT + APPENDIX:
        assert h in headings, h
    assert len(doc.inline_shapes) >= 3  # opportunity map, return-vs-risk, landscape (+catalysts)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "PROCEED WITH CAUTION" in text  # the stub run is flagged (low verification)
    assert "[1" in text  # numbered citations
    assert {"opportunity_map", "return_risk", "landscape"} <= files["charts"].keys()

    wb = load_workbook(files["xlsx"])
    assert wb.sheetnames == ["Programs", "Pursuit options", "Scorecards"]
    assert wb["Programs"].max_row > 1


def test_memo_without_competitor_data_still_renders(inp, tmp_path):
    for h in inp.hypotheses:
        h.inactive = h.landscape = None
    files = write_memo(inp, tmp_path, pdf=False)
    assert files["docx"].exists() and "landscape" not in files["charts"]
    assert all(p.form == "build" for p in files["draft"].pursuits)


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_pdf_when_libreoffice_is_installed(inp, tmp_path):
    files = write_memo(inp, tmp_path)
    assert files["pdf"] and files["pdf"].stat().st_size > 10_000
