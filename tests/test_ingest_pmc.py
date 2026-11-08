"""PMC full text (person 1): sections, tables, license, release date, storage."""

import xml.etree.ElementTree as ET
from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import pmc

ARTICLE = ET.fromstring(
    (pmc.REPO_ROOT / "tests/fixtures/ingest/pmc_sample.xml").read_bytes()
).find(".//article")


def test_chunks_follow_sections_then_tables():
    parts = pmc.chunks(ARTICLE)
    assert [s for s, _ in parts] == ["Introduction", "Methods", "Results", "Table 2"]
    sections = dict(parts)
    assert sections["Introduction"] == "Made-up background on FIX-101."
    assert sections["Methods"] == "Design\nMade-up randomized design."
    assert sections["Results"] == "Made-up: FEV1 improved by 100 mL."  # table kept out of the text
    assert sections["Table 2"] == "Table 2 Adverse events\nEvent | FIX-101\nCough | 5%"


def test_license_and_release_date():
    assert pmc.license_of(ARTICLE) == "open-access"
    assert pmc.release_date(ARTICLE) == date(2024, 3, 10)


def test_article_without_body_has_no_chunks():
    assert pmc.chunks(ET.fromstring("<article><front/></article>")) == []


def _parent_chunks_exist() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            return conn.execute("SELECT to_regclass('parent_chunks')").fetchone()[0] is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _parent_chunks_exist(), reason="needs Postgres with parent_chunks")
def test_store_dates_chunks_by_pmc_release_and_is_rerunnable():
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute(
                "INSERT INTO documents (id, source, source_date, version_label, text_hash)"
                " VALUES ('pmid:FIXTURE-0501', 'pubmed', '2023-03-10', 'pubmed-live', 'h')"
            )
            assert pmc.store(conn, "FIXTURE-0501", "PMCFIXTURE", ARTICLE) == 4
            assert pmc.store(conn, "FIXTURE-0501", "PMCFIXTURE", ARTICLE) == 4
            rows = conn.execute(
                "SELECT parent_id, published_date FROM parent_chunks"
                " WHERE doc_id = 'pmid:FIXTURE-0501' ORDER BY position"
            ).fetchall()
            assert [r[0] for r in rows][0] == "chunk:pmid:FIXTURE-0501:p1" and len(rows) == 4
            assert {r[1] for r in rows} == {date(2024, 3, 10)}  # embargo: release, not epub
            full, pmcid = conn.execute(
                "SELECT full_text, fields->>'pmcid' FROM documents WHERE id = 'pmid:FIXTURE-0501'"
            ).fetchone()
            assert full.startswith("## Introduction") and pmcid == "PMCFIXTURE"
            assert pmc.store(conn, "FIXTURE-0599", "PMCX", ARTICLE) == -1  # not in the store
        finally:
            conn.rollback()  # never leave fixture rows in a real store
