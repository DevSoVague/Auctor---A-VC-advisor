"""Shelved COPD drugs (person 1): every record complete, sourced, dated, pre-2026."""

from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import shelved


def test_records_are_complete_and_sourced():
    records = shelved.rows()
    assert 6 <= len(records) <= 10
    for r in records:
        f = r["fields"]
        for key in ("pathway", "status", "reason", "side_effects"):
            assert f[key], f"{f['drug']}: {key} missing"
        assert f["sources"], f"{f['drug']}: no source"
        for s in f["sources"]:
            assert s.get("id", "").startswith("pmid:") or s.get("url", "").startswith("https://")
            assert date.fromisoformat(s["date"]) <= date(2025, 12, 31), "no 2026 sources"


def test_row_is_dated_by_its_latest_source():
    benra = next(r for r in shelved.rows() if r["id"] == "curated:shelved:benralizumab-in-copd")
    assert benra["source_date"] == date(2025, 9, 17)  # RESOLUTE update, the latest source
    assert "pmid:31112385 (2019-05-20)" in benra["body"]


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
    records = shelved.rows()
    with psycopg.connect(Settings().database_url) as conn:
        try:
            shelved.upsert(conn, records)
            shelved.upsert(conn, records)
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE id LIKE 'curated:shelved:%'"
            ).fetchone()[0]
            assert count == len(records)
        finally:
            conn.rollback()


def test_every_source_is_typed():
    for r in shelved.rows():
        for s in r["fields"]["sources"]:
            assert s["type"] in {"academic", "company", "regulatory_filing", "news"}
