"""Shelved COPD drugs (person 1): sourced, dated, pre-2026, versioned by source date."""

from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import shelved


def _latest(records):
    return [r for r in records if r["version_label"] == "curated-shelved-v2"]


def test_latest_versions_are_complete_and_sourced():
    latest = _latest(shelved.rows())
    assert 6 <= len(latest) <= 10
    for r in latest:
        f = r["fields"]
        for key in ("pathway", "status", "reason", "side_effects"):
            assert f[key], f"{f['drug']}: {key} missing"
        for s in f["sources"]:
            assert s["type"] in {"academic", "company", "regulatory_filing", "news"}
            assert s["fact"]
            assert s.get("id", "").startswith(("pmid:", "news:")) or s["url"].startswith("https://")
            assert date.fromisoformat(s["date"]) <= date(2025, 12, 31), "no 2026 sources"


def test_versions_only_show_what_was_public_then():
    benra = [r for r in shelved.rows() if r["id"] == "curated:shelved:benralizumab-in-copd"]
    assert [r["source_date"] for r in benra] == [
        date(2018, 5, 15),
        date(2019, 5, 20),
        date(2025, 9, 17),
    ]
    as_of_2021 = benra[1]
    assert "rate ratios 0.83-1.04" in as_of_2021["body"]
    assert "RESOLUTE" not in as_of_2021["body"] and "status" not in as_of_2021["fields"]
    assert "RESOLUTE" in benra[2]["body"] and benra[2]["fields"]["status"]


def test_one_latest_version_per_drug():
    records = shelved.rows()
    assert len({r["id"] for r in records}) == len(_latest(records)) == 9
    assert len(records) == 13


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
                " VALUES ('curated:shelved:danirixin', 'curated', '1999-01-01', 'old', 'h')"
                " ON CONFLICT DO NOTHING"
            )
            shelved.upsert(conn, records)
            shelved.upsert(conn, records)
            assert shelved.remove_stale(conn, records) >= 1
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE id LIKE 'curated:shelved:%'"
            ).fetchone()[0]
            assert count == len(records)
        finally:
            conn.rollback()  # leave the real store as it was
