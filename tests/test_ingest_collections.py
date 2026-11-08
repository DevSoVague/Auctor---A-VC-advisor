"""Curated collections (person 1): the 50 COPD drug-trial papers, and tagging."""

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import collections


def test_drug_trial_set_is_50_unique_pubmed_ids_from_2023_2024():
    (drug_trials,) = [
        s for s in collections.load_sets() if s["name"] == "copd-drug-trials-2023-2024"
    ]
    ids = drug_trials["ids"]
    assert len(ids) == 50 and len(set(ids)) == 50
    assert all(i.startswith("pmid:") for i in ids)
    papers = __import__("json").loads(
        (collections.CURATED / "copd_drug_trials_2023_2024.json").read_text()
    )["papers"]
    assert all("2023-01-01" <= p["source_date"] <= "2024-12-31" for p in papers)


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
def test_tag_is_idempotent_and_rolled_back():
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute(
                "INSERT INTO documents (id, source, source_date, version_label, text_hash)"
                " VALUES ('pmid:FIXTURE-0301', 'pubmed', '2023-03-01', 'pubmed-live', 'h')"
            )
            ids = ["pmid:FIXTURE-0301", "pmid:FIXTURE-0399"]
            assert collections.tag(conn, "fixture-set", ids) == (1, ["pmid:FIXTURE-0399"])
            assert collections.tag(conn, "fixture-set", ids) == (0, ["pmid:FIXTURE-0399"])
            tags = conn.execute(
                "SELECT fields->'collections' FROM documents WHERE id = 'pmid:FIXTURE-0301'"
            ).fetchone()[0]
            assert tags == ["fixture-set"]
        finally:
            conn.rollback()  # never leave fixture rows in a real store
