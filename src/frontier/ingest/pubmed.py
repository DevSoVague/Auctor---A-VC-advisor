"""PubMed ingestion for the COPD store (person 1).

Fetches PubMed records through NCBI E-utilities, keeps the raw XML in data/pubmed/
(git-ignored), and upserts one row per article into the documents table.

    uv run python -m frontier.ingest.pubmed                     # 2016-01 to this month
    uv run python -m frontier.ingest.pubmed --start 2020-01 --end 2020-03

Safe to rerun: months already downloaded are read from data/pubmed/ instead of
NCBI (pass --refresh to download again), and rows are upserted on (id, source_date).

Dates. source_date is the first date the article was public:
  1. the earliest exact date among the electronic ArticleDate and the PubMed
     "pubmed"/"entrez" history dates, else
  2. the journal PubDate, read conservatively as the LAST day it could mean
     ("2020" -> 2020-12-31, "2020 Dec-2021 Jan" -> 2021-01-31),
so a record is never dated earlier than it became public and the backtest cutoff
cannot let a later paper through.
"""

from __future__ import annotations

import argparse
import calendar
import gzip
import hashlib
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
from dotenv import dotenv_values

from frontier.core.config import REPO_ROOT, Settings
from frontier.ingest.window import END, START, in_window

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
QUERY = '"Pulmonary Disease, Chronic Obstructive"[MeSH Terms]'  # PRD: Ingestion table
RAW_DIR = REPO_ROOT / "data" / "pubmed"
VERSION_LABEL = "pubmed-live"
BATCH = 200  # records per efetch request
MAX_PER_QUERY = 9999  # E-utilities returns at most 10,000 records per search

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
SEASONS = {"winter": 12, "spring": 5, "summer": 8, "fall": 11, "autumn": 11}


# --- dates ---------------------------------------------------------------------------


def _month(text: str | None) -> int | None:
    if not text:
        return None
    text = text.strip().lower()
    if text.isdigit():
        return int(text) if 1 <= int(text) <= 12 else None
    return MONTHS.get(text[:3]) or SEASONS.get(text)


def _end_of(year: int, month: int | None = None, day: int | None = None) -> date:
    """The last date a partial date could mean."""
    if month is None:
        return date(year, 12, 31)
    if day is None:
        return date(year, month, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def exact_date(node: ET.Element | None) -> date | None:
    """A <Year><Month><Day> element with all three parts, else None."""
    if node is None:
        return None
    year, month, day = (node.findtext(t) for t in ("Year", "Month", "Day"))
    month_num = _month(month)
    if not (year and month_num and day and day.strip().isdigit()):
        return None
    try:
        return date(int(year), month_num, int(day))
    except ValueError:
        return None


def latest_pubdate(pubdate: ET.Element | None) -> date | None:
    """Journal PubDate read as the last day it could mean."""
    if pubdate is None:
        return None
    year = pubdate.findtext("Year")
    if year and year.strip().isdigit():
        month = _month(pubdate.findtext("Month")) or _month(pubdate.findtext("Season"))
        day = pubdate.findtext("Day")
        day_num = int(day) if day and day.strip().isdigit() and month else None
        try:
            return _end_of(int(year), month, day_num)
        except ValueError:
            return _end_of(int(year), month)
    medline = pubdate.findtext("MedlineDate") or ""
    # e.g. "2020 Dec-2021 Jan", "2019 Nov-Dec", "2018-2019", "2020 Winter"
    years = [int(y) for y in re.findall(r"\b(\d{4})\b", medline)]
    if not years:
        return None
    last_year = max(years)
    tail = medline.rsplit(str(last_year), 1)[-1]  # the part after the last year
    month = None
    for token in re.findall(r"[A-Za-z]+", tail):
        month = _month(token) or month  # the last month named, if any
    return _end_of(last_year, month)


def source_date(article: ET.Element) -> date | None:
    citation = article.find("MedlineCitation")
    exact = [
        exact_date(d)
        for d in citation.findall("Article/ArticleDate")
        if d.get("DateType", "Electronic") == "Electronic"
    ]
    exact += [
        exact_date(d)
        for d in article.findall("PubmedData/History/PubMedPubDate")
        if d.get("PubStatus") in ("pubmed", "entrez")
    ]
    exact = [d for d in exact if d]
    if exact:
        return min(exact)
    return latest_pubdate(citation.find("Article/Journal/JournalIssue/PubDate"))


# --- parsing -------------------------------------------------------------------------


@dataclass
class PubmedRecord:
    pmid: str
    source_date: date
    title: str
    body: str
    fields: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"pmid:{self.pmid}"

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.body}".encode()).hexdigest()


def _text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def parse_article(article: ET.Element) -> PubmedRecord | None:
    """One <PubmedArticle>, or None if it has no PMID or no usable date."""
    citation = article.find("MedlineCitation")
    if citation is None:
        return None
    pmid = (citation.findtext("PMID") or "").strip()
    when = source_date(article)
    if not pmid or when is None:
        return None
    art = citation.find("Article")
    parts = []
    for block in art.findall("Abstract/AbstractText") if art is not None else []:
        label = block.get("Label")
        text = _text(block)
        if text:
            parts.append(f"{label}: {text}" if label else text)
    doi = next(
        (
            (i.text or "").strip()
            for i in article.findall("PubmedData/ArticleIdList/ArticleId")
            if i.get("IdType") == "doi"
        ),
        None,
    )
    return PubmedRecord(
        pmid=pmid,
        source_date=when,
        title=_text(art.find("ArticleTitle")) if art is not None else "",
        body="\n".join(parts),
        fields={
            "journal": _text(art.find("Journal/Title")) if art is not None else "",
            "doi": doi,
            "mesh_terms": [
                _text(d) for d in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")
            ],
            "publication_types": [
                _text(p) for p in citation.findall("Article/PublicationTypeList/PublicationType")
            ],
        },
    )


def parse_xml(xml: bytes) -> Iterator[PubmedRecord]:
    root = ET.fromstring(xml)
    for article in root.iter("PubmedArticle"):
        record = parse_article(article)
        if record:
            yield record


# --- fetching ------------------------------------------------------------------------


class Eutils:
    """Minimal E-utilities client with NCBI's rate limit and one retry policy."""

    def __init__(self, api_key: str = "", email: str = "") -> None:
        self.params = {"tool": "frontier-hypothesis-engine"}
        if api_key:
            self.params["api_key"] = api_key
        if email:
            self.params["email"] = email
        self.delay = 0.11 if api_key else 0.34  # 10 requests/s with a key, 3 without
        self.client = httpx.Client(timeout=60)
        self._last = 0.0

    def _call(self, endpoint: str, data: dict) -> httpx.Response:
        for attempt in range(5):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.client.post(f"{EUTILS}/{endpoint}", data={**self.params, **data})
            except httpx.TransportError:
                response = None
            if response is not None and response.status_code == 200:
                return response
            time.sleep(2**attempt)
        status = response.status_code if response is not None else "no response"
        raise RuntimeError(f"E-utilities {endpoint} failed after 5 attempts ({status})")

    def search(self, term: str) -> tuple[int, str, str]:
        result = self._call(
            "esearch.fcgi",
            {"db": "pubmed", "term": term, "usehistory": "y", "retmax": 0, "retmode": "json"},
        ).json()["esearchresult"]
        return int(result["count"]), result.get("webenv", ""), result.get("querykey", "")

    def fetch(self, webenv: str, query_key: str, count: int) -> bytes:
        """All records of one search, as one <PubmedArticleSet> document."""
        chunks = []
        for start in range(0, count, BATCH):
            xml = self._call(
                "efetch.fcgi",
                {
                    "db": "pubmed",
                    "WebEnv": webenv,
                    "query_key": query_key,
                    "retstart": start,
                    "retmax": BATCH,
                    "retmode": "xml",
                },
            ).content
            chunks.extend(ET.fromstring(xml).iter("PubmedArticle"))
        root = ET.Element("PubmedArticleSet")
        root.extend(chunks)
        return ET.tostring(root, encoding="utf-8")


def month_term(year: int, month: int, query: str = QUERY) -> str:
    last = calendar.monthrange(year, month)[1]
    return f'({query}) AND ("{year}/{month:02d}/01"[PDAT] : "{year}/{month:02d}/{last}"[PDAT])'


def months(start: str, end: str) -> Iterator[tuple[int, int]]:
    year, month = map(int, start.split("-"))
    end_year, end_month = map(int, end.split("-"))
    while (year, month) <= (end_year, end_month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def download_month(eutils: Eutils, year: int, month: int, refresh: bool = False) -> Path:
    path = RAW_DIR / f"{year}-{month:02d}.xml.gz"
    if path.exists() and not refresh:
        return path
    count, webenv, query_key = eutils.search(month_term(year, month))
    if count > MAX_PER_QUERY:
        raise RuntimeError(f"{year}-{month:02d}: {count} records, above the E-utilities limit")
    xml = eutils.fetch(webenv, query_key, count) if count else b"<PubmedArticleSet/>"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(xml))
    tmp.replace(path)  # never leave a half-written month behind
    return path


# --- loading -------------------------------------------------------------------------

UPSERT = """
INSERT INTO documents (id, source, source_date, version_label, title, body, url, fields, text_hash)
VALUES (%s, 'pubmed', %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id, source_date) DO UPDATE SET
  version_label = EXCLUDED.version_label, title = EXCLUDED.title, body = EXCLUDED.body,
  url = EXCLUDED.url, fields = EXCLUDED.fields, text_hash = EXCLUDED.text_hash,
  embedding = CASE WHEN documents.text_hash = EXCLUDED.text_hash
                   THEN documents.embedding END  -- re-embed only if the text changed
"""


def upsert(conn, records: Iterable[PubmedRecord]) -> int:
    from psycopg.types.json import Jsonb

    rows = [
        (r.id, r.source_date, VERSION_LABEL, r.title, r.body, r.url, Jsonb(r.fields), r.text_hash)
        for r in records
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT, rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    today = date.today()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--start", default=f"{START:%Y-%m}", help="first month, YYYY-MM")
    parser.add_argument("--end", default=f"{END:%Y-%m}", help="last month, YYYY-MM")
    parser.add_argument("--refresh", action="store_true", help="download months again")
    parser.add_argument("--download-only", action="store_true", help="skip the database")
    args = parser.parse_args(argv)

    import psycopg

    env = {**dotenv_values(REPO_ROOT / ".env"), **os.environ}  # environment wins over .env
    eutils = Eutils(env.get("NCBI_API_KEY") or "", env.get("NCBI_EMAIL") or "")
    settings = Settings()
    total = 0
    conn = None if args.download_only else psycopg.connect(settings.database_url)
    try:
        for year, month in months(args.start, args.end):
            # The current month is never final, so it is always downloaded again.
            refresh = args.refresh or (year, month) == (today.year, today.month)
            path = download_month(eutils, year, month, refresh=refresh)
            records = [
                r for r in parse_xml(gzip.decompress(path.read_bytes())) if in_window(r.source_date)
            ]
            if conn is not None:
                upsert(conn, records)
                conn.commit()
            total += len(records)
            print(f"{year}-{month:02d}: {len(records)} records", flush=True)
    finally:
        if conn is not None:
            conn.close()
    print(f"done: {total} records{' (download only)' if args.download_only else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
