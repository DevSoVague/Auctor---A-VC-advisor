"""Industry news for the COPD store (person 1).

Downloads to data/news/ (git-ignored) and writes the items into the documents table
(Person 3 asked for news in the store, 2026-11-08; --archive-only skips the database).
Two kinds of feed:

1. Google News search RSS for COPD, one window per month from 2016 on. The
   after:/before: operators make history available, so the 2021 backtest sees
   the news of its time and nothing later. A window that returns the 100-item
   cap is split in half until it doesn't (down to one day).
2. Publisher RSS feeds (BioSpace, Fierce Biotech). These only list the last few
   days, so they build up history by being run regularly; items are kept only if
   they mention COPD.

Only headline, publisher, short summary and link are stored, never article text.

    uv run python -m frontier.ingest.news                  # 2016-01 to this month + feeds
    uv run python -m frontier.ingest.news --feeds-only     # just the publisher feeds

Safe to rerun: past months are read from data/news/ (git-ignored) instead of the
network; the current month and the publisher feeds are fetched again each run.
"""

from __future__ import annotations

import argparse
import calendar
import gzip
import hashlib
import html
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from frontier.core.config import REPO_ROOT, Settings
from frontier.ingest.window import END, START, in_window

RAW_DIR = REPO_ROOT / "data" / "news"
QUERY = '(COPD OR "chronic obstructive pulmonary disease")'
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
GOOGLE_CAP = 100  # Google News search RSS returns at most 100 items
FEEDS = {
    "biospace": "https://www.biospace.com/articlesrss/",
    "fiercebiotech": "https://www.fiercebiotech.com/rss/xml",
}
COPD_PATTERN = re.compile(r"\bCOPD\b|chronic obstructive|emphysema", re.IGNORECASE)
USER_AGENT = "frontier-hypothesis-engine/0.1"
DELAY = 3.0  # seconds between requests; Google throttles fast polling


@dataclass
class NewsItem:
    title: str
    link: str
    published: date
    publisher: str = ""
    summary: str = ""
    fields: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        # Keyed on the headline, so the same story from two feeds is one row.
        key = re.sub(r"\W+", " ", self.title.lower()).strip()
        return f"news:{hashlib.sha256(key.encode()).hexdigest()[:16]}"

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.summary}".encode()).hexdigest()


def _clean(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return " ".join(html.unescape(text).split())


def parse_rss(xml: bytes, feed: str) -> Iterator[NewsItem]:
    root = ET.fromstring(xml)
    for item in root.iter("item"):
        raw_date = item.findtext("pubDate")
        title = _clean(item.findtext("title"))
        if not (raw_date and title):
            continue
        try:
            published = parsedate_to_datetime(raw_date).astimezone(UTC).date()
        except (TypeError, ValueError):
            continue
        source = item.find("source")
        publisher = _clean(source.text) if source is not None else ""
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(f" - {publisher}")]  # Google appends " - Publisher"
        summary = _clean(item.findtext("description"))
        if summary.startswith(title):
            summary = summary[len(title) :].strip(" -")
        if publisher and summary == publisher:
            summary = ""
        yield NewsItem(
            title=title,
            link=(item.findtext("link") or "").strip(),
            published=published,
            publisher=publisher or feed,
            summary=summary,
            fields={"feed": feed},
        )


def mentions_copd(item: NewsItem) -> bool:
    return bool(COPD_PATTERN.search(f"{item.title} {item.summary}"))


# --- fetching ------------------------------------------------------------------------


class Fetcher:
    def __init__(self) -> None:
        self.client = httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self._last = 0.0

    def get(self, url: str) -> bytes:
        response = None
        for attempt in range(4):
            wait = DELAY - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.client.get(url)
            except httpx.TransportError:
                response = None
            if response is not None and response.status_code == 200:
                return response.content
            time.sleep(10 * (attempt + 1))
        status = response.status_code if response is not None else "no response"
        raise RuntimeError(f"GET {url} failed after 4 attempts ({status})")


def google_url(start: date, end: date, query: str = QUERY) -> str:
    """Search for items published from start up to (not including) end."""
    q = f"{query} after:{start.isoformat()} before:{end.isoformat()}"
    return GOOGLE_NEWS.format(q=quote_plus(q))


def google_window(fetcher: Fetcher, start: date, end: date) -> list[bytes]:
    """Raw feeds covering [start, end), split while a window hits the item cap."""
    xml = fetcher.get(google_url(start, end))
    count = sum(1 for _ in ET.fromstring(xml).iter("item"))
    if count < GOOGLE_CAP or (end - start).days <= 1:
        return [xml]
    middle = start + (end - start) // 2
    return google_window(fetcher, start, middle) + google_window(fetcher, middle, end)


def months(start: str, end: str) -> Iterator[tuple[int, int]]:
    year, month = map(int, start.split("-"))
    end_year, end_month = map(int, end.split("-"))
    while (year, month) <= (end_year, end_month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _save(path: Path, chunks: list[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(b"\n<!--split-->\n".join(chunks)))
    tmp.replace(path)


def _load(path: Path) -> list[bytes]:
    return gzip.decompress(path.read_bytes()).split(b"\n<!--split-->\n")


def google_month(fetcher: Fetcher, year: int, month: int, refresh: bool) -> list[NewsItem]:
    path = RAW_DIR / "google" / f"{year}-{month:02d}.xml.gz"
    if refresh or not path.exists():
        start = date(year, month, 1)
        end = start + timedelta(days=calendar.monthrange(year, month)[1])
        _save(path, google_window(fetcher, start, end))
    items = [item for xml in _load(path) for item in parse_rss(xml, "google-news")]
    for item in items:
        item.fields["query"] = QUERY
    return items


def publisher_feed(fetcher: Fetcher, name: str, url: str, today: date) -> list[NewsItem]:
    path = RAW_DIR / name / f"{today.isoformat()}.xml.gz"  # one snapshot per day
    _save(path, [fetcher.get(url)])
    return [item for item in parse_rss(_load(path)[0], name) if mentions_copd(item)]


# --- loading -------------------------------------------------------------------------

UPSERT = """
INSERT INTO documents (id, source, source_date, version_label, title, body, url, fields, text_hash)
VALUES (%s, 'news', %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id, source_date) DO UPDATE SET
  version_label = EXCLUDED.version_label, title = EXCLUDED.title, body = EXCLUDED.body,
  url = EXCLUDED.url, fields = EXCLUDED.fields, text_hash = EXCLUDED.text_hash,
  embedding = CASE WHEN documents.text_hash = EXCLUDED.text_hash
                   THEN documents.embedding END
"""


def dedupe(items: Iterable[NewsItem]) -> list[NewsItem]:
    """One row per story: the earliest report wins."""
    best: dict[str, NewsItem] = {}
    for item in items:
        if item.id not in best or item.published < best[item.id].published:
            best[item.id] = item
    return list(best.values())


def upsert(conn, items: Iterable[NewsItem]) -> int:
    from psycopg.types.json import Jsonb

    rows = [
        (
            i.id,
            i.published,
            f"news-{i.fields.get('feed', 'unknown')}",
            i.title,
            "\n".join(p for p in (i.publisher, i.summary) if p),
            i.link,
            Jsonb({**i.fields, "publisher": i.publisher}),
            i.text_hash,
        )
        for i in items
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT, rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--start", default=f"{START:%Y-%m}", help="first month, YYYY-MM")
    parser.add_argument("--end", default=f"{END:%Y-%m}", help="last month, YYYY-MM")
    parser.add_argument("--refresh", action="store_true", help="download past months again")
    parser.add_argument("--feeds-only", action="store_true", help="skip the Google News history")
    parser.add_argument(
        "--archive-only", action="store_true", help="download to data/news/ only, no database"
    )
    args = parser.parse_args(argv)

    import psycopg

    fetcher = Fetcher()
    conn = None if args.archive_only else psycopg.connect(Settings().database_url)
    total = 0
    try:
        batches: list[tuple[str, list[NewsItem]]] = []
        if not args.feeds_only:
            for year, month in months(args.start, args.end):
                refresh = args.refresh or (year, month) == (today.year, today.month)
                batches.append(
                    (
                        f"{year}-{month:02d}",
                        [
                            i
                            for i in google_month(fetcher, year, month, refresh)
                            if in_window(i.published)
                        ],
                    )
                )
                if conn is not None:
                    total += upsert(conn, dedupe(batches[-1][1]))
                    conn.commit()
                print(f"{batches[-1][0]}: {len(batches[-1][1])} items", flush=True)
        if not in_window(today):
            print(f"publisher feeds skipped: today ({today}) is outside {START} to {END}")
        for name, url in FEEDS.items() if in_window(today) else []:
            try:
                items = [
                    i for i in publisher_feed(fetcher, name, url, today) if in_window(i.published)
                ]
            except RuntimeError as error:  # one feed down must not stop the rest
                print(f"{name}: skipped ({error})", flush=True)
                continue
            if conn is not None:
                total += upsert(conn, dedupe(items))
                conn.commit()
            print(f"{name}: {len(items)} COPD items", flush=True)
    finally:
        if conn is not None:
            conn.close()
    print(
        "done: downloaded to data/news/ only"
        if args.archive_only
        else f"done: {total} rows written"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
