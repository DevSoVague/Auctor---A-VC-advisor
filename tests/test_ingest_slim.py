"""Slimming the store (person 1): archive first; keep the 50 papers, the drugs and the
trials linked to them; stop if a paper is missing or there are no drugs."""

import gzip
import json

import psycopg
import pytest
from psycopg.types.json import Jsonb

from frontier.core.config import Settings
from frontier.ingest import collections, pubmed, shelved, slim

PAPERS = next(s for s in collections.load_sets() if s["name"] == slim.KEEP)["ids"]


def test_pubmed_keeps_only_curated_papers_by_default():
    ids = pubmed.curated_ids()
    assert len(ids) == 50 and set(PAPERS) == ids


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


def _trial(conn, nct: str, intervention: str) -> None:
    fields = {"nct_id": nct, "interventions": [{"type": "DRUG", "name": intervention}]}
    conn.execute(
        "INSERT INTO documents (id, source, source_date, version_label, title, text_hash, fields)"
        " VALUES (%s, 'ctgov', '2024-01-01', 'ctgov-live', 't', 'h', %s)",
        (f"nct:{nct}", Jsonb(fields)),
    )


@pytest.mark.skipif(not _documents_versioned(), reason="needs Postgres with the versioned schema")
def test_slim_archives_then_deletes_rolled_back(tmp_path, monkeypatch):
    monkeypatch.setattr(slim, "ARCHIVE", tmp_path)
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute("DELETE FROM documents WHERE id = ANY(%s)", (PAPERS,))
            conn.execute(
                "INSERT INTO documents (id, source, source_date, version_label, text_hash, fields)"
                " SELECT i, 'pubmed', '2024-01-01', 'pubmed-live', 'h',"
                ' \'{"collections": ["copd-drug-trials-2023-2024"]}\'::jsonb'
                " FROM unnest(%s::text[]) AS i",
                (PAPERS,),
            )
            conn.execute(
                "INSERT INTO documents (id, source, source_date, version_label, text_hash) VALUES"
                " ('pmid:FIXTURE-0701', 'pubmed', '2024-01-01', 'pubmed-live', 'h'),"
                " ('news:FIXTURE0701', 'news', '2024-01-01', 'news-google-news', 'h')"
            )
            shelved.upsert(conn, shelved.rows())
            _trial(conn, "FIXTURE-0701", "Astegolimab 490 mg")  # linked to a shelved drug
            _trial(conn, "FIXTURE-0702", "Fixture inhaler")  # linked to nothing
            assert slim.check(conn) == []
            archived = slim.archive(conn)
            assert archived["pubmed"] >= 1 and archived["news"] >= 1 and archived["ctgov"] >= 1
            saved = json.loads(gzip.decompress((tmp_path / "ctgov-2023-2025.json.gz").read_bytes()))
            assert "nct:FIXTURE-0702" in {r["id"] for r in saved}
            slim.delete(conn)
            left = {
                r[0]
                for r in conn.execute(
                    "SELECT id FROM documents WHERE source IN ('pubmed', 'news')"
                    " OR id LIKE 'nct:FIXTURE-07%'"
                )
            }
            assert left == set(PAPERS) | {"nct:FIXTURE-0701"}
        finally:
            conn.rollback()  # leave the real store as it was


@pytest.mark.skipif(not _documents_versioned(), reason="needs Postgres with the versioned schema")
def test_slim_stops_if_a_paper_is_missing():
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute("DELETE FROM documents WHERE id = %s", (PAPERS[0],))
            assert slim.check(conn)
        finally:
            conn.rollback()


@pytest.mark.skipif(not _documents_versioned(), reason="needs Postgres with the versioned schema")
def test_slim_stops_if_there_are_no_drugs():
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute("DELETE FROM documents WHERE source IN ('fda', 'curated')")
            assert any("no drugs" in p for p in slim.check(conn))
        finally:
            conn.rollback()
