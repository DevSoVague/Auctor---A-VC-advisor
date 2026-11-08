"""Full text from PubMed Central for curated papers (person 1).

For each paper in a curated set (default: the 50 COPD drug-trial papers) that has a
PMC id in its PubMed record, fetches the article XML from PMC through E-utilities,
keeps the raw XML in data/pmc/ (git-ignored), and stores:

* documents.full_text on the paper's pmid row, with fields.pmcid / pmc_license;
* one parent_chunks row per top-level section and per table, in reading order.

Chunks are dated by the later of the PubMed date and the PMC release date, so an
embargoed full text is never visible before it was public. Some publishers don't
allow the full text to be downloaded from PMC; those papers keep their abstract only
and are reported.

    uv run python -m frontier.ingest.pmc
"""

from __future__ import annotations

import gzip
import json
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from frontier.core.config import REPO_ROOT, Settings
from frontier.ingest import pubmed
from frontier.ingest.window import in_window

CURATED = Path(__file__).resolve().parent / "curated" / "copd_drug_trials_2023_2024.json"
RAW_DIR = REPO_ROOT / "data" / "pmc"
XLINK = "{http://www.w3.org/1999/xlink}"


def pmc_ids(pmids: set[str], raw_dir: Path = pubmed.RAW_DIR) -> dict[str, str]:
    """pmid -> PMC id, read from the downloaded PubMed XML."""
    found = {}
    for path in sorted(raw_dir.glob("*.xml.gz")):
        root = ET.fromstring(gzip.decompress(path.read_bytes()))
        for article in root.iter("PubmedArticle"):
            pmid = (article.findtext("MedlineCitation/PMID") or "").strip()
            if pmid in pmids:
                for aid in article.findall("PubmedData/ArticleIdList/ArticleId"):
                    if aid.get("IdType") == "pmc" and aid.text:
                        found[pmid] = aid.text.strip()
    return found


def _text(node: ET.Element) -> str:
    return " ".join("".join(node.itertext()).split())


def _date(node: ET.Element) -> date | None:
    try:
        return date(
            int(node.findtext("year")),
            int(node.findtext("month") or 12),
            int(node.findtext("day") or 28),
        )
    except (TypeError, ValueError):
        return None


def release_date(article: ET.Element) -> date | None:
    """PMC release date (later than publication for embargoed articles), if given."""
    for pd in article.findall("front/article-meta/pub-date"):
        if "pmc-release" in (pd.get("pub-type"), pd.get("date-type")):
            return _date(pd)
    return None


def license_of(article: ET.Element) -> str | None:
    lic = article.find("front/article-meta/permissions/license")
    if lic is None:
        return None
    ref = next((e.text for e in lic.iter() if e.tag.endswith("license_ref") and e.text), None)
    return lic.get("license-type") or lic.get(f"{XLINK}href") or ref or _text(lic)[:200]


def _table(wrap: ET.Element) -> str:
    head = " ".join(
        p
        for p in (
            wrap.findtext("label"),
            _text(wrap.find("caption")) if wrap.find("caption") is not None else "",
        )
        if p
    )
    rows = [" | ".join(_text(c) for c in tr) for tr in wrap.iter("tr")]
    return "\n".join([head, *rows]).strip()


def _section_text(sec: ET.Element) -> str:
    """Paragraphs and sub-section titles of a section, without its tables and figures."""
    parts = []

    def walk(node: ET.Element) -> None:
        for child in node:
            if child.tag in ("table-wrap", "fig", "table-wrap-group", "fig-group"):
                continue
            if child.tag == "title" and node is not sec:
                parts.append(_text(child))
            elif child.tag == "p":
                parts.append(_text(child))
            elif child.tag in ("sec", "list", "list-item", "boxed-text", "disp-quote"):
                walk(child)

    walk(sec)
    return "\n".join(p for p in parts if p)


def chunks(article: ET.Element) -> list[tuple[str, str]]:
    """(section, text) pairs in reading order: top-level sections, then tables."""
    body = article.find("body")
    if body is None:
        return []
    out = []
    loose = [_text(p) for p in body.findall("p")]
    if any(loose):
        out.append(("Body", "\n".join(t for t in loose if t)))
    for sec in body.findall("sec"):
        title = sec.findtext("title") or "Section"
        text = _section_text(sec)
        if text:
            out.append((" ".join(title.split()), text))
    for i, wrap in enumerate(body.iter("table-wrap"), start=1):
        text = _table(wrap)
        if text:
            out.append((wrap.findtext("label") or f"Table {i}", text))
    return out


def fetch(eutils: pubmed.Eutils, pmcid: str, refresh: bool = False) -> bytes:
    path = RAW_DIR / f"{pmcid}.xml.gz"
    if path.exists() and not refresh:
        return gzip.decompress(path.read_bytes())
    xml = eutils._call(
        "efetch.fcgi", {"db": "pmc", "id": pmcid.removeprefix("PMC"), "retmode": "xml"}
    ).content
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(xml))
    return xml


def store(conn, pmid: str, pmcid: str, article: ET.Element) -> int:
    """Write full text and chunks for one paper; returns the number of chunks."""
    from psycopg.types.json import Jsonb

    parts = chunks(article)
    doc_id = f"pmid:{pmid}"
    with conn.cursor() as cur:
        cur.execute("SELECT max(source_date) FROM documents WHERE id = %s", (doc_id,))
        pubmed_date = cur.fetchone()[0]
        if pubmed_date is None:
            return -1
        released = release_date(article)
        when = max(d for d in (pubmed_date, released) if d)
        if not in_window(when):
            return -2  # full text public outside the 2023-2025 window
        cur.execute("DELETE FROM parent_chunks WHERE doc_id = %s", (doc_id,))
        if not parts:
            return 0
        cur.executemany(
            "INSERT INTO parent_chunks (parent_id, doc_id, position, section, text, published_date)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (f"chunk:{doc_id}:p{i}", doc_id, i, section, text, when)
                for i, (section, text) in enumerate(parts, 1)
            ],
        )
        cur.execute(
            "UPDATE documents SET full_text = %s, fields = fields || %s WHERE id = %s",
            (
                "\n\n".join(f"## {s}\n{t}" for s, t in parts),
                Jsonb(
                    {
                        "pmcid": pmcid,
                        "pmc_license": license_of(article),
                        "full_text_date": when.isoformat(),
                    }
                ),
                doc_id,
            ),
        )
    return len(parts)


def main() -> int:
    import os

    import psycopg
    from dotenv import dotenv_values

    pmids = {p["id"].removeprefix("pmid:") for p in json.loads(CURATED.read_text())["papers"]}
    mapping = pmc_ids(pmids)
    print(f"{len(pmids)} papers, {len(mapping)} with a PMC id")
    env = {**dotenv_values(REPO_ROOT / ".env"), **os.environ}
    eutils = pubmed.Eutils(env.get("NCBI_API_KEY") or "", env.get("NCBI_EMAIL") or "")
    stored = abstract_only = 0
    with psycopg.connect(Settings().database_url) as conn:
        for pmid, pmcid in sorted(mapping.items()):
            root = ET.fromstring(fetch(eutils, pmcid))
            article = root.find(".//article")
            n = store(conn, pmid, pmcid, article) if article is not None else 0
            if n == -2:
                abstract_only += 1
                print(f"pmid:{pmid} {pmcid}: full text released outside 2023-2025, skipped")
            elif n > 0:
                stored += 1
                print(f"pmid:{pmid} {pmcid}: {n} chunks")
            else:
                abstract_only += 1
                print(f"pmid:{pmid} {pmcid}: no full text in PMC XML (abstract only)")
    no_pmc = len(pmids) - len(mapping)
    print(f"done: full text for {stored}, abstract only for {abstract_only + no_pmc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
