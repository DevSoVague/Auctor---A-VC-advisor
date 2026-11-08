"""PubMed ingestion (person 1): parsing, conservative dates, idempotent upsert."""

import xml.etree.ElementTree as ET
from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import pubmed

SAMPLE = (pubmed.REPO_ROOT / "tests/fixtures/ingest/pubmed_sample.xml").read_bytes()


def _pubdate(inner: str) -> ET.Element:
    return ET.fromstring(f"<PubDate>{inner}</PubDate>")


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        ("<Year>2020</Year>", date(2020, 12, 31)),
        ("<Year>2020</Year><Month>Feb</Month>", date(2020, 2, 29)),
        ("<Year>2020</Year><Month>02</Month><Day>03</Day>", date(2020, 2, 3)),
        ("<Year>2020</Year><Season>Winter</Season>", date(2020, 12, 31)),
        ("<MedlineDate>2020 Dec-2021 Jan</MedlineDate>", date(2021, 1, 31)),
        ("<MedlineDate>2019 Nov-Dec</MedlineDate>", date(2019, 12, 31)),
        ("<MedlineDate>2018-2019</MedlineDate>", date(2019, 12, 31)),
        ("<MedlineDate>no date here</MedlineDate>", None),
    ],
)
def test_pubdate_is_read_as_its_latest_day(inner, expected):
    assert pubmed.latest_pubdate(_pubdate(inner)) == expected


def test_parse_sample():
    records = {r.pmid: r for r in pubmed.parse_xml(SAMPLE)}
    assert set(records) == {"FIXTURE-0101", "FIXTURE-0102"}  # 0103 has no usable date

    first = records["FIXTURE-0101"]
    assert first.id == "pmid:FIXTURE-0101"
    # Earliest exact date (electronic, 2020-12-18) beats the issue date (Feb 2021).
    assert first.source_date == date(2020, 12, 18)
    assert first.title == "[fixture] PDE3/4 inhibition in moderate COPD"
    assert first.body == (
        "BACKGROUND: Made-up background.\nRESULTS: Made-up result with FEV1 change."
    )
    assert first.fields["doi"] == "10.0000/fixture.0101"
    assert first.fields["mesh_terms"] == ["Pulmonary Disease, Chronic Obstructive"]
    assert first.fields["publication_types"] == ["Randomized Controlled Trial"]
    assert first.url == "https://pubmed.ncbi.nlm.nih.gov/FIXTURE-0101/"

    # Only a range: dated at its end, so a January 2021 paper never slips before the cutoff.
    assert records["FIXTURE-0102"].source_date == date(2021, 1, 31)


def test_month_query_and_range():
    assert pubmed.month_term(2020, 2) == (
        '("Pulmonary Disease, Chronic Obstructive"[MeSH Terms])'
        ' AND ("2020/02/01"[PDAT] : "2020/02/29"[PDAT])'
    )
    expected = [(2019, 11), (2019, 12), (2020, 1), (2020, 2)]
    assert list(pubmed.months("2019-11", "2020-02")) == expected


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
    records = list(pubmed.parse_xml(SAMPLE))
    with psycopg.connect(Settings().database_url) as conn:
        try:
            pubmed.upsert(conn, records)
            pubmed.upsert(conn, records)  # rerun: same rows, no duplicates
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE id LIKE 'pmid:FIXTURE-01%'"
            ).fetchone()[0]
            assert count == 2
        finally:
            conn.rollback()  # never leave fixture rows in a real store
