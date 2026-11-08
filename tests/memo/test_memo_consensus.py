"""Consensus screen, citation summary and cost log: the points a judge who knows COPD raises.

1. A mechanism that already has an approved COPD drug is priced in: the memo never offers the
   approved drug as a pursuit, says so, and counts it as a caution bar.
2. The memo states what the Citation Checker removed and proves none of it was cited.
3. The pipeline's token usage reaches the llm_calls cost log.
"""

import asyncio
import copy
import json
from pathlib import Path

import psycopg
import pytest
from docx import Document

from frontier.core.config import Settings
from frontier.core.mirror import MEMO_AGENT, mirror_usage, rows_for, run_uuid
from frontier.memo.build import write_memo
from frontier.memo.candidates import candidates
from frontier.memo.draft import draft_from_numbers
from frontier.memo.inputs import from_graph_state
from frontier.memo.scoring import approved_for, scorecards

STATE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/memo/p3g_stub_state.json").read_text()
)


@pytest.fixture
def inp():
    return from_graph_state(copy.deepcopy(STATE))


# ---- 1. already-approved mechanisms ----------------------------------------------------------
def test_approved_for_the_disease_only_counts_as_priced_in():
    assert approved_for("Approved", "COPD")
    assert approved_for("Approved (COPD)", "COPD")
    assert not approved_for("Approved (plaque psoriasis)", "COPD")  # a repurposing chance
    assert not approved_for("Phase 3", "COPD") and not approved_for(None, "COPD")


def test_an_approved_program_is_never_offered_as_a_pursuit(inp):
    cards = {s.hypothesis_id: s for s in scorecards(inp)}
    assert cards["h1"].approved and "fixomilast" in cards["h1"].approved[0]
    for cands in candidates(inp).values():
        assert all("fixomilast" not in c.name for c in cands)
    assert all(c.follow_on_to for c in candidates(inp)["h1"])  # what is left is follow-on


def test_the_approved_field_is_not_credited_as_open(inp):
    card = next(s for s in scorecards(inp) if s.hypothesis_id == "h1")
    assert any("priced in" in d for d in card.upside_drivers)
    assert not any("uncrowded" in d for d in card.upside_drivers)


def test_the_memo_says_so_when_it_recommends_a_follow_on(inp):
    draft = draft_from_numbers(inp)
    mine = [p for p in draft.pursuits if p.hypothesis_id == "h1"]
    assert mine, "h1 should still yield a follow-on pursuit"
    for p in mine:
        assert any("Consensus risk" in s.text for s in p.what_could_go_wrong)


# ---- 2. citation summary ---------------------------------------------------------------------
def test_the_memo_reports_what_the_checker_removed_and_that_none_was_cited(inp, tmp_path):
    victim = inp.ledger[0].claim_id  # make the checker remove one claim's every source
    for row in inp.ledger:
        if row.claim_id == victim:
            row.supports, row.action = "no", "remove"
    assert inp.claim_status()[victim] == "removed"
    doc = Document(write_memo(inp, tmp_path / "m", pdf=False)["docx"])
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "The checker examined" in text and "1 removed because no source" in text
    assert "of which 0 were removed" in text


# ---- 3. cost log -----------------------------------------------------------------------------
USAGE = {
    "a1_disease": {
        "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "calls": 3,
        "input_tokens": 33000,
        "cached_tokens": 15000,
        "output_tokens": 8000,
        "latency_ms": 29000,
    },
    "a7_citations": {
        "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "calls": 0,
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "latency_ms": 0,
    },
    MEMO_AGENT: {
        "model": "x",
        "calls": 1,
        "input_tokens": 1,
        "cached_tokens": 0,
        "output_tokens": 1,
        "latency_ms": 1,
    },
}


def test_usage_rows_skip_idle_agents_and_the_memo_agent_and_price_the_rest():
    rows = rows_for("99c6c721", USAGE)
    assert [r[1] for r in rows] == ["a1_disease"]
    assert rows[0][0] == run_uuid("99c6c721") == run_uuid("99c6c721")
    assert rows[0][6] and rows[0][6] > 0  # list price in USD


def _db():
    url = Settings().database_url
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError:
        pytest.skip("no local Postgres")
    return url


def test_mirroring_is_written_once_per_agent_and_replaced_on_a_rerun():
    url = _db()
    run_id = "test-mirror-run"
    try:
        assert asyncio.run(mirror_usage(url, run_id, USAGE)) == 1
        assert asyncio.run(mirror_usage(url, run_id, USAGE)) == 1  # replaces, never doubles
        with psycopg.connect(url) as conn:
            n, usd = conn.execute(
                "SELECT count(*), sum(list_price_usd) FROM llm_calls WHERE run_id = %s::uuid",
                (run_uuid(run_id),),
            ).fetchone()
        assert n == 1 and usd > 0
    finally:
        with psycopg.connect(url) as conn:
            conn.execute("DELETE FROM llm_calls WHERE run_id = %s::uuid", (run_uuid(run_id),))
