"""News ingestion (person 1): parsing, dates in UTC, COPD filter, dedupe, upsert."""

from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import news

FIXTURES = news.REPO_ROOT / "tests/fixtures/ingest"
GOOGLE = (FIXTURES / "news_google.xml").read_bytes()
PUBLISHER = (FIXTURES / "news_publisher.xml").read_bytes()


def test_parse_google_item():
    items = list(news.parse_rss(GOOGLE, "google-news"))
    assert len(items) == 3  # the undated item is skipped
    first = items[0]
    assert first.title == "[fixture] Fixture Bio starts Phase 3 of FIX-101 in COPD"
    assert first.publisher == "Fixture Wire"
    assert first.summary == ""  # Google's description only repeats title and publisher
    assert first.link == "https://news.google.com/rss/articles/FIXTURE-0001"
    # 23:30 on 31 Dec in UTC-5 is 1 Jan 2021 in UTC: after the backtest cutoff.
    assert first.published == date(2021, 1, 1)
    assert first.id.startswith("news:")


def test_dedupe_keeps_earliest_report():
    items = news.dedupe(news.parse_rss(GOOGLE, "google-news"))
    same = [i for i in items if "Same story" in i.title]
    assert len(same) == 1
    assert same[0].published == date(2020, 12, 14)
    assert same[0].publisher == "Other Fixture News"


def test_publisher_feed_keeps_only_copd():
    items = [i for i in news.parse_rss(PUBLISHER, "biospace") if news.mentions_copd(i)]
    assert [i.title for i in items] == ["[fixture] Fixture Pharma trims pipeline"]
    assert items[0].publisher == "biospace"


def test_google_url_uses_date_operators():
    url = news.google_url(date(2020, 12, 1), date(2021, 1, 1))
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "after%3A2020-12-01+before%3A2021-01-01" in url


class FakeFetcher:
    """Returns the item cap for the first window only, so it must split once."""

    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        n = news.GOOGLE_CAP if len(self.urls) == 1 else 5
        items = "".join(
            f"<item><title>t{len(self.urls)}-{i}</title>"
            "<pubDate>Mon, 14 Dec 2020 10:00:00 GMT</pubDate></item>"
            for i in range(n)
        )
        return f"<rss><channel>{items}</channel></rss>".encode()


def test_window_splits_when_capped():
    fetcher = FakeFetcher()
    chunks = news.google_window(fetcher, date(2020, 12, 1), date(2021, 1, 1))
    assert len(fetcher.urls) == 3  # the full month, then its two halves
    assert len(chunks) == 2


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
    items = news.dedupe(news.parse_rss(GOOGLE, "google-news"))
    with psycopg.connect(Settings().database_url) as conn:
        try:
            news.upsert(conn, items)
            news.upsert(conn, items)
            rows = conn.execute(
                "SELECT version_label, count(*) FROM documents"
                " WHERE source = 'news' AND title LIKE '[fixture]%' GROUP BY 1"
            ).fetchall()
            assert rows == [("news-google-news", 2)]
        finally:
            conn.rollback()  # never leave fixture rows in a real store
