"""More full text for the curated papers from Europe PMC, plus Unpaywall links (person 1).

pmc.py stores full text for papers that NCBI's PMC can serve. For each curated paper still
without full text, this looks the paper up in Europe PMC; if Europe PMC has the full
text XML (same JATS format as PMC), it is stored the same way: documents.full_text and
parent_chunks, through pmc.store. Raw responses go to data/europepmc/ (git-ignored).

Papers still without full text are then looked up in Unpaywall by DOI. Unpaywall only
gives links to legal free copies (often a publisher PDF); these are not parsed, only
listed in data/europepmc/unpaywall.json and the printout. Unpaywall needs a contact
email: UNPAYWALL_EMAIL, or NCBI_EMAIL, from .env. Without one, Unpaywall is skipped.

    uv run python -m frontier.ingest.europepmc
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

import httpx
from dotenv import dotenv_values

from frontier.core.config import REPO_ROOT, Settings
from frontier.ingest import pmc

RAW_DIR = REPO_ROOT / "data" / "europepmc"
SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
FULL_TEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"


def missing(conn, pmids: list[str]) -> list[str]:
    """Curated pmids (without prefix) whose store row has no full text yet."""
    rows = conn.execute(
        "SELECT DISTINCT id FROM documents WHERE id = ANY(%s) AND full_text IS NULL",
        ([f"pmid:{p}" for p in pmids],),
    ).fetchall()
    return sorted(r[0].removeprefix("pmid:") for r in rows)


def record(search_json: dict) -> dict:
    """pmcid, doi and open-access flags from a Europe PMC core search result."""
    hits = search_json.get("resultList", {}).get("result", [])
    if not hits:
        return {}
    hit = hits[0]
    return {
        "pmcid": hit.get("pmcid"),
        "doi": hit.get("doi"),
        "is_open_access": hit.get("isOpenAccess") == "Y",
        "in_epmc": hit.get("inEPMC") == "Y",
    }


def oa_link(unpaywall_json: dict) -> dict | None:
    """Best legal free copy from an Unpaywall response, or None."""
    best = unpaywall_json.get("best_oa_location")
    if not unpaywall_json.get("is_oa") or not best:
        return None
    return {
        "url": best.get("url_for_pdf") or best.get("url"),
        "host_type": best.get("host_type"),
        "version": best.get("version"),
        "license": best.get("license"),
    }


class Client:
    def __init__(self) -> None:
        self.http = httpx.Client(timeout=60, follow_redirects=True)

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(3):
            resp = self.http.get(url, params=params)
            if resp.status_code not in (429, 500, 502, 503, 504):
                return resp
            time.sleep(2**attempt)
        return resp

    def search(self, pmid: str) -> dict:
        path = RAW_DIR / f"search-{pmid}.json"
        if path.exists():
            return json.loads(path.read_text())
        resp = self.get(
            SEARCH,
            {"query": f"EXT_ID:{pmid} AND SRC:MED", "resultType": "core", "format": "json"},
        )
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(resp.text)
        return resp.json()

    def full_text(self, pmcid: str) -> bytes | None:
        path = RAW_DIR / f"{pmcid}.xml.gz"
        if path.exists():
            return gzip.decompress(path.read_bytes())
        resp = self.get(FULL_TEXT.format(pmcid=pmcid))
        if resp.status_code != 200 or not resp.content.strip():
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(resp.content))
        return resp.content

    def unpaywall(self, doi: str, email: str) -> dict:
        resp = self.get(UNPAYWALL.format(doi=doi), {"email": email})
        return resp.json() if resp.status_code == 200 else {}


def main() -> int:
    import psycopg

    env = {**dotenv_values(REPO_ROOT / ".env"), **os.environ}
    email = env.get("UNPAYWALL_EMAIL") or env.get("NCBI_EMAIL") or ""
    pmids = [p["id"].removeprefix("pmid:") for p in json.loads(pmc.CURATED.read_text())["papers"]]
    client = Client()
    stored, links, none = 0, {}, []
    with psycopg.connect(Settings().database_url) as conn:
        todo = missing(conn, pmids)
        print(f"{len(pmids)} papers, {len(todo)} without full text")
        for pmid in todo:
            rec = record(client.search(pmid))
            xml = client.full_text(rec["pmcid"]) if rec.get("pmcid") else None
            article = ET.fromstring(xml) if xml else None
            if article is not None and article.tag != "article":
                article = article.find(".//article")
            n = pmc.store(conn, pmid, rec["pmcid"], article) if article is not None else 0
            if n > 0:
                conn.execute(
                    'UPDATE documents SET fields = fields || \'{"full_text_from": "europepmc"}\''
                    " WHERE id = %s",
                    (f"pmid:{pmid}",),
                )
                stored += 1
                print(f"pmid:{pmid} {rec['pmcid']}: {n} chunks from Europe PMC")
                continue
            if n == -2:
                print(f"pmid:{pmid}: full text released outside 2023-2025, skipped")
            link = (
                oa_link(client.unpaywall(rec["doi"], email)) if email and rec.get("doi") else None
            )
            if link:
                links[f"pmid:{pmid}"] = {"doi": rec["doi"], **link}
                print(f"pmid:{pmid}: free copy at {link['url']} ({link['host_type']})")
            else:
                none.append(f"pmid:{pmid}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "unpaywall.json").write_text(json.dumps(links, indent=2))
    if not email:
        print("Unpaywall skipped: set UNPAYWALL_EMAIL (or NCBI_EMAIL) in .env")
    print(
        f"done: {stored} more full texts stored, {len(links)} free copies listed"
        f" (not parsed), {len(none)} with no free copy found"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
