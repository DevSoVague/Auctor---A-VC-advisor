"""Shelved COPD drugs (person 1): one row per drug, 2023-2025 sources only, all sourced."""

from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import shelved


def test_one_complete_row_per_drug_from_2023_2025_sources():
    records = shelved.rows()
    assert len(records) == len({r["id"] for r in records}) == 6
    for r in records:
        f = r["fields"]
        for key in ("pathway", "status", "reason", "side_effects"):
            assert f[key], f"{f['drug']}: {key} missing"
        assert f["sources"]
        for s in f["sources"]:
            assert date(2023, 1, 1) <= date.fromisoformat(s["date"]) <= date(2025, 12, 31)
            assert s["type"] in {"academic", "company", "regulatory_filing", "news"} and s["fact"]
        assert r["source_date"] == max(date.fromisoformat(s["date"]) for s in f["sources"])


def test_pre_2023_only_drugs_are_left_out_and_old_sources_dropped():
    ids = {r["id"] for r in shelved.rows()}
    for gone in ("danirixin", "losmapimod", "nemiralisib"):
        assert f"curated:shelved:{gone}" not in ids
    benra = next(r for r in shelved.rows() if r["id"] == "curated:shelved:benralizumab-in-copd")
    assert [s["date"] for s in benra["fields"]["sources"]] == ["2025-09-17"]
    assert "GALATHEA" not in benra["body"] and "RESOLUTE" in benra["body"]


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
def test_upsert_and_stale_removal_rolled_back():
    records = shelved.rows()
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute(
                "INSERT INTO documents (id, source, source_date, version_label, text_hash)"
                " VALUES ('curated:shelved:danirixin', 'curated', '2020-06-12', 'old', 'h')"
                " ON CONFLICT DO NOTHING"
            )
            shelved.upsert(conn, records)
            shelved.upsert(conn, records)
            shelved.remove_stale(conn, records)
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE id LIKE 'curated:shelved:%'"
            ).fetchone()[0]
            assert count == len(records)
        finally:
            conn.rollback()  # leave the real store as it was
