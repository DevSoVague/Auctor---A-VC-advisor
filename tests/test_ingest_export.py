"""Export of the store for teammates (person 1)."""

import json

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.core.retrieval import Record, StubRetrievalClient
from frontier.ingest import export


def _store_ready() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            return conn.execute("SELECT to_regclass('shelved_programs')").fetchone()[0] is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _store_ready(), reason="needs Postgres with the store tables")
def test_export_loads_in_the_stub_client_rolled_back(tmp_path):
    from datetime import date

    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute(
                "INSERT INTO documents"
                " (id, source, source_date, version_label, title, body, text_hash, full_text)"
                " VALUES ('pmid:FIXTURE-0901', 'pubmed', '2024-02-01', 'pubmed-live',"
                "  'Made-up FIX-101 trial', 'Made-up abstract.', 'h', '## Results\nMade-up.'),"
                " ('curated:shelved:fixture', 'curated', '2024-02-01', 'v', 'Made-up', NULL,"
                "  'h', NULL)"
            )
            counts = export.write(conn, tmp_path)
            records = json.loads((tmp_path / "retrieval/records.json").read_text())
            other = json.loads((tmp_path / "retrieval/records-other-sources.json").read_text())
        finally:
            conn.rollback()
    assert counts["records-other-sources.json"] >= 1
    assert "curated:shelved:fixture" in {r["id"] for r in other}
    mine = next(r for r in records if r["id"] == "pmid:FIXTURE-0901")
    assert mine["fields"]["full_text"].startswith("## Results")
    stub = StubRetrievalClient(date(2025, 1, 1), [Record.model_validate(r) for r in records])
    assert stub._records and all(r.source in export.ACCEPTED for r in stub._records)
    assert (tmp_path / "README.txt").read_text().startswith("Frontier Hypothesis Engine")
    for name in export.TABLES:
        assert (tmp_path / "tables" / f"{name}.json").exists()
