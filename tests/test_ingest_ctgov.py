"""ClinicalTrials.gov ingestion (person 1): versions, leakage, paging, upsert."""

import json
from datetime import date

import httpx
import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import ctgov

SAMPLE = json.loads((ctgov.REPO_ROOT / "tests/fixtures/ingest/ctgov_sample.json").read_text())


def _as_of(rows, cutoff):
    """What retrieval would return per id: the latest version dated before the cutoff."""
    seen = {}
    for row in sorted(rows, key=lambda r: r["source_date"]):
        if row["source_date"] < cutoff:
            seen[row["id"]] = row
    return seen


def test_to_date_is_conservative():
    assert ctgov.to_date("2020-12-18") == date(2020, 12, 18)
    assert ctgov.to_date("2020-02") == date(2020, 2, 29)
    assert ctgov.to_date("2020") == date(2020, 12, 31)
    assert ctgov.to_date(None) is None


def test_only_interventional_studies():
    kept = [s for s in SAMPLE if ctgov.is_interventional(s)]
    assert len(kept) == 3


def test_study_updated_after_cutoff_is_registration_only_in_backtest():
    rows = ctgov.versions(SAMPLE[0])
    assert [r["version_label"] for r in rows] == ["ctgov-registration", "ctgov-live"]
    seen = _as_of(rows, date(2021, 1, 1))["nct:FIXTURE-0201"]
    assert seen["version_label"] == "ctgov-registration"
    assert seen["source_date"] == date(2019, 2, 1)
    for leaked in ("status", "enrollment", "primary_completion", "has_results", "why_stopped"):
        assert leaked not in seen["fields"]
    assert "COMPLETED" not in seen["body"] and "improved" not in seen["body"]
    # Live: the full current record.
    live = _as_of(rows, date(2026, 11, 7))["nct:FIXTURE-0201"]
    assert live["fields"]["status"] == "COMPLETED"
    assert live["fields"]["phase"] == "PHASE3"
    assert live["fields"]["conditions"] == ["COPD"]
    assert live["url"] == "https://clinicaltrials.gov/study/FIXTURE-0201"


def test_study_last_updated_before_cutoff_is_seen_in_full():
    seen = _as_of(ctgov.versions(SAMPLE[1]), date(2021, 1, 1))["nct:FIXTURE-0202"]
    assert seen["version_label"] == "ctgov-live"
    assert seen["fields"]["status"] == "TERMINATED"
    assert seen["fields"]["why_stopped"] == "Business decision"
    assert seen["fields"]["phase"] == "PHASE2/PHASE3"


def test_never_updated_study_has_one_version():
    rows = ctgov.versions(SAMPLE[2])
    assert [r["version_label"] for r in rows] == ["ctgov-live"]


def test_download_follows_page_tokens(tmp_path, monkeypatch):
    pages = {
        None: {"studies": [SAMPLE[0]], "nextPageToken": "t2"},
        "t2": {"studies": [SAMPLE[1]]},
    }

    def handler(request):
        assert request.url.params["query.cond"] == "COPD"
        return httpx.Response(200, json=pages[request.url.params.get("pageToken")])

    path = tmp_path / "snapshot.json.gz"
    monkeypatch.setattr(ctgov.time, "sleep", lambda _: None)
    ctgov.download(path, httpx.Client(transport=httpx.MockTransport(handler)))
    assert [s["protocolSection"]["identificationModule"]["nctId"] for s in ctgov.load(path)] == [
        "FIXTURE-0201",
        "FIXTURE-0202",
    ]


def _documents_versioned() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'documents' AND column_name = 'version_label'"
            ).fetchone()
            return row is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _documents_versioned(), reason="needs Postgres with the versioned schema")
def test_upsert_is_idempotent_and_rolled_back():
    rows = [r for s in SAMPLE if ctgov.is_interventional(s) for r in ctgov.versions(s)]
    with psycopg.connect(Settings().database_url) as conn:
        try:
            ctgov.upsert(conn, rows)
            ctgov.upsert(conn, rows)
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE id LIKE 'nct:FIXTURE-02%'"
            ).fetchone()[0]
            assert count == 5  # 0201 and 0202 have two versions each, 0203 one
        finally:
            conn.rollback()  # never leave fixture rows in a real store
