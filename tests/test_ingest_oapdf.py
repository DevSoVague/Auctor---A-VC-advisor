"""Full text from Unpaywall free PDFs (person 1): checks, sections, download, storage."""

from datetime import date

import httpx
import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import oapdf

PDF = (oapdf.REPO_ROOT / "tests/fixtures/ingest/oa_sample.pdf").read_bytes()
TITLE = "Made-up trial of FIX-101 in chronic obstructive pulmonary disease."


def test_sections_split_at_headings_and_stop_at_references():
    parts = oapdf.sections(oapdf.pages_of(PDF))
    assert [s for s, _ in parts] == [
        "Front matter",
        "Introduction",
        "Methods",
        "Results",
        "Discussion",
    ]
    assert dict(parts)["Results"] == "FEV1 improved by 100 mL."
    assert "reference" not in " ".join(t for _, t in parts).lower()


def test_sections_fall_back_to_pages_and_join_hyphenated_words():
    parts = oapdf.sections(["Some text with a hyphen-\nated word.", "More text."])
    assert parts == [("Page 1", "Some text with a hyphenated word."), ("Page 2", "More text.")]


def test_is_article_rejects_supplements_short_files_and_other_papers():
    pages = oapdf.pages_of(PDF)
    assert oapdf.is_article(pages, TITLE) is None
    assert oapdf.is_article(pages[:2], TITLE) == "only 2 pages"
    assert oapdf.is_article(["Supplementary Online Content", "x", "y"], TITLE).startswith("suppl")
    assert oapdf.is_article(pages, "Another made-up paper about asthma inhalers").startswith(
        "title"
    )


def test_pdf_link_reads_either_attribute_order():
    a = '<meta name="citation_pdf_url" content="https://example.org/a.pdf">'
    b = "<meta content='https://example.org/b.pdf' name='citation_pdf_url' />"
    assert oapdf.pdf_link(a) == "https://example.org/a.pdf"
    assert oapdf.pdf_link(b) == "https://example.org/b.pdf"
    assert oapdf.pdf_link("<html></html>") is None


def _client(handler) -> oapdf.Client:
    client = oapdf.Client("")
    client.http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return client


def test_download_follows_the_page_pdf_link_and_reports_refusals(tmp_path, monkeypatch):
    monkeypatch.setattr(oapdf, "RAW_DIR", tmp_path)
    monkeypatch.setattr(oapdf.time, "sleep", lambda s: None)
    page = '<meta name="citation_pdf_url" content="/paper.pdf">'

    def site(request):
        if request.url.path == "/paper.pdf":
            return httpx.Response(200, content=PDF)
        return httpx.Response(200, text=page)

    pdf, note = _client(site).pdf("FIXTURE-0801", "https://example.org/article")
    assert pdf == PDF and note == "downloaded" and (tmp_path / "FIXTURE-0801.pdf").exists()
    pdf, note = _client(lambda r: httpx.Response(403)).pdf("FIXTURE-0802", "https://example.org/x")
    assert pdf is None and note == "refused by the site (HTTP 403)"
    pdf, note = _client(lambda r: httpx.Response(200, text="<html/>")).pdf(
        "FIXTURE-0803", "https://example.org/y"
    )
    assert pdf is None and note == "web page without a PDF link"


def _parent_chunks_exist() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            return conn.execute("SELECT to_regclass('parent_chunks')").fetchone()[0] is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _parent_chunks_exist(), reason="needs Postgres with parent_chunks")
def test_pdf_sections_are_stored_like_pmc_full_text_rolled_back():
    with psycopg.connect(Settings().database_url) as conn:
        try:
            conn.execute(
                "INSERT INTO documents (id, source, source_date, version_label, text_hash)"
                " VALUES ('pmid:FIXTURE-0801', 'pubmed', '2024-02-01', 'pubmed-live', 'h')"
            )
            parts = oapdf.sections(oapdf.pages_of(PDF))
            fields = {"full_text_from": "unpaywall-pdf", "oa_url": "https://example.org/a.pdf"}
            assert oapdf.pmc.store_parts(conn, "FIXTURE-0801", parts, fields) == 5
            row = conn.execute(
                "SELECT fields->>'full_text_from', fields->>'full_text_date', full_text"
                " FROM documents WHERE id = 'pmid:FIXTURE-0801'"
            ).fetchone()
            assert row[0] == "unpaywall-pdf" and row[1] == "2024-02-01"
            assert row[2].startswith("## Front matter")
            dates = conn.execute(
                "SELECT DISTINCT published_date FROM parent_chunks"
                " WHERE doc_id = 'pmid:FIXTURE-0801'"
            ).fetchall()
            assert dates == [(date(2024, 2, 1),)]
        finally:
            conn.rollback()
