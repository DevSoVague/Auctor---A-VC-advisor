"""The 2023-2025 window (person 1): bounds, and purging rows outside it."""

from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import window


def test_bounds():
    assert window.in_window(date(2023, 1, 1)) and window.in_window("2025-12-31")
    assert not window.in_window(date(2022, 12, 31)) and not window.in_window("2026-01-01")
    assert not window.in_window(None)


def _tables_exist() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            return conn.execute("SELECT to_regclass('parent_chunks')").fetchone()[0] is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _tables_exist(), reason="needs Postgres with the store tables")
def test_purge_rolled_back():
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute(
                "INSERT INTO documents (id, source, source_date, version_label, text_hash) VALUES"
                " ('pmid:FIXTURE-0601', 'pubmed', '2019-01-01', 'pubmed-live', 'h'),"
                " ('pmid:FIXTURE-0602', 'pubmed', '2024-01-01', 'pubmed-live', 'h'),"
                " ('pmid:FIXTURE-0603', 'pubmed', '2026-02-01', 'pubmed-live', 'h')"
            )
            window.purge(conn)
            left = conn.execute(
                "SELECT id FROM documents WHERE id LIKE 'pmid:FIXTURE-06%'"
            ).fetchall()
            assert left == [("pmid:FIXTURE-0602",)]
        finally:
            conn.rollback()  # leave the real store as it was
