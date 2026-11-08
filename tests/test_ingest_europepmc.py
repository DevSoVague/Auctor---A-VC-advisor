"""Europe PMC full text and Unpaywall links for curated papers (person 1)."""

import json
import xml.etree.ElementTree as ET

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import europepmc, pmc

FIXTURES = pmc.REPO_ROOT / "tests/fixtures/ingest"


def test_record_reads_pmcid_doi_and_flags():
    rec = europepmc.record(json.loads((FIXTURES / "europepmc_search.json").read_text()))
    assert rec == {
        "pmcid": "PMCFIXTURE0601",
        "doi": "10.0000/fixture.0601",
        "is_open_access": True,
        "in_epmc": True,
    }
    assert europepmc.record({"resultList": {"result": []}}) == {}


def test_oa_link_prefers_pdf_and_skips_closed():
    link = europepmc.oa_link(json.loads((FIXTURES / "unpaywall.json").read_text()))
    assert link == {
        "url": "https://example.org/fixture-0602.pdf",
        "host_type": "publisher",
        "version": "publishedVersion",
        "license": "cc-by",
    }
    assert europepmc.oa_link({"is_oa": False, "best_oa_location": None}) is None


def _parent_chunks_exist() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            return conn.execute("SELECT to_regclass('parent_chunks')").fetchone()[0] is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _parent_chunks_exist(), reason="needs Postgres with parent_chunks")
def test_missing_lists_papers_without_full_text_rolled_back():
    article = ET.fromstring((FIXTURES / "pmc_sample.xml").read_bytes()).find(".//article")
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute(
                "INSERT INTO documents (id, source, source_date, version_label, text_hash) VALUES"
                " ('pmid:FIXTURE-0601', 'pubmed', '2023-03-10', 'pubmed-live', 'h'),"
                " ('pmid:FIXTURE-0602', 'pubmed', '2023-03-10', 'pubmed-live', 'h')"
            )
            ids = ["FIXTURE-0601", "FIXTURE-0602"]
            assert europepmc.missing(conn, ids) == ids
            assert pmc.store(conn, "FIXTURE-0601", "PMCFIXTURE0601", article) == 4
            assert europepmc.missing(conn, ids) == ["FIXTURE-0602"]
        finally:
            conn.rollback()
