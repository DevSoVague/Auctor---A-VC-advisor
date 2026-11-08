"""Full text from legal free PDFs found by Unpaywall (person 1).

Reads data/europepmc/unpaywall.json (written by europepmc.py). For each curated paper
still without full text it downloads the free copy; if the link is a web page, it
follows the page's own citation_pdf_url tag, if there is one. A site that refuses the
download (403 and the like) is reported and skipped, never worked around. PDFs are kept
in data/oapdf/ (git-ignored).

A PDF is used only if it looks like the paper itself: at least 3 pages, the title's
words on the first two pages, and no "supplement" at the top of page one. Its text is
split at the usual headings (Introduction, Methods, Results, Discussion, ...) and cut at
References; with fewer than two headings found, it is split by page. It is stored like
PMC full text (documents.full_text, parent_chunks) with fields.full_text_from =
"unpaywall-pdf" and the link and licence. Many free copies are free to read only:
the text is for the team's own analysis, not for republishing.

    uv run python -m frontier.ingest.oapdf
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time

import httpx
from dotenv import dotenv_values

from frontier.core.config import REPO_ROOT, Settings
from frontier.ingest import europepmc, pmc

RAW_DIR = REPO_ROOT / "data" / "oapdf"
HEADINGS = (
    "abstract|introduction|background|methods?|materials and methods|patients and methods"
    "|study design|results|discussion|conclusions?|funding|limitations"
)
HEADING = re.compile(rf"^\s*(?:\d{{1,2}}\.?\s*)?({HEADINGS})\s*:?\s*$", re.IGNORECASE)
END = re.compile(r"^\s*(?:\d{1,2}\.?\s*)?(references|bibliography)\s*$", re.IGNORECASE)
PDF_META = re.compile(
    r"<meta[^>]+name=[\"']citation_pdf_url[\"'][^>]+content=[\"']([^\"']+)[\"']"
    r"|<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']citation_pdf_url[\"']",
    re.IGNORECASE,
)


def pdf_link(html: str) -> str | None:
    """The citation_pdf_url a publisher's article page declares, if any."""
    m = PDF_META.search(html)
    return (m.group(1) or m.group(2)) if m else None


def pages_of(pdf: bytes) -> list[str]:
    from pypdf import PdfReader

    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def is_article(pages: list[str], title: str) -> str | None:
    """None if the PDF looks like the paper; otherwise why not."""
    if len(pages) < 3:
        return f"only {len(pages)} pages"
    if "supplement" in " ".join(pages[0].split())[:400].lower():
        return "supplement, not the paper"
    want = [w for w in _words(title) if len(w) > 3][:8]
    have = set(_words(" ".join(pages[:2])))
    if want and sum(w in have for w in want) < 0.8 * len(want):
        return "title not found on the first pages"
    return None


def _clean(lines: list[str]) -> str:
    text = "\n".join(lines)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # words hyphenated across lines
    return " ".join(text.split())


def sections(pages: list[str]) -> list[tuple[str, str]]:
    """(section, text) pairs: split at headings, cut at References; else one per page."""
    lines = [line for page in pages for line in page.splitlines()]
    out, name, buf = [], "Front matter", []
    for line in lines:
        if END.match(line):
            break
        m = HEADING.match(line)
        if m:
            if _clean(buf):
                out.append((name, _clean(buf)))
            name, buf = m.group(1).title(), []
        else:
            buf.append(line)
    if _clean(buf):
        out.append((name, _clean(buf)))
    if sum(s != "Front matter" for s, _ in out) >= 2:
        return out
    return [(f"Page {i}", _clean(p.splitlines())) for i, p in enumerate(pages, 1) if p.strip()]


class Client:
    def __init__(self, email: str) -> None:
        agent = "frontier-hypothesis-engine/0.1 (research)"
        if email:
            agent += f" mailto:{email}"
        self.http = httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": agent})

    def pdf(self, pmid: str, url: str) -> tuple[bytes | None, str]:
        """(pdf bytes, note). Cached in data/oapdf/."""
        path = RAW_DIR / f"{pmid}.pdf"
        if path.exists():
            return path.read_bytes(), "cached"
        for _ in range(2):  # the link, then the page's citation_pdf_url
            try:
                resp = self.http.get(url)
            except httpx.HTTPError as e:
                return None, f"download failed: {type(e).__name__}"
            if resp.status_code != 200:
                return None, f"refused by the site (HTTP {resp.status_code})"
            if resp.content[:5] == b"%PDF-":
                RAW_DIR.mkdir(parents=True, exist_ok=True)
                path.write_bytes(resp.content)
                return resp.content, "downloaded"
            nxt = pdf_link(resp.text)
            if not nxt:
                return None, "web page without a PDF link"
            url = str(resp.url.join(nxt))
            time.sleep(1)
        return None, "web page without a PDF link"


def main() -> int:
    import psycopg

    env = {**dotenv_values(REPO_ROOT / ".env"), **os.environ}
    email = env.get("UNPAYWALL_EMAIL") or env.get("NCBI_EMAIL") or ""
    links = json.loads((europepmc.RAW_DIR / "unpaywall.json").read_text())
    client = Client(email)
    stored = 0
    with psycopg.connect(Settings().database_url) as conn:
        todo = set(europepmc.missing(conn, [k.removeprefix("pmid:") for k in links]))
        print(f"{len(links)} free copies listed, {len(todo)} for papers without full text")
        for key, link in sorted(links.items()):
            pmid = key.removeprefix("pmid:")
            if pmid not in todo:
                continue
            pdf, note = client.pdf(pmid, link["url"])
            if pdf is None:
                print(f"{key}: {note}")
                continue
            try:
                pages = pages_of(pdf)
            except Exception as e:  # noqa: BLE001 - a broken PDF is reported, not fatal
                print(f"{key}: unreadable PDF ({type(e).__name__})")
                continue
            (title,) = conn.execute(
                "SELECT title FROM documents WHERE id = %s ORDER BY source_date DESC LIMIT 1",
                (key,),
            ).fetchone()
            why = is_article(pages, title or "")
            if why:
                print(f"{key}: skipped, {why}")
                continue
            fields = {
                "full_text_from": "unpaywall-pdf",
                "oa_url": link["url"],
                "oa_host_type": link.get("host_type"),
                "oa_version": link.get("version"),
                "oa_license": link.get("license"),
            }
            n = pmc.store_parts(conn, pmid, sections(pages), fields)
            if n == -2:
                print(f"{key}: outside 2023-2025, skipped")
            elif n > 0:
                stored += 1
                print(f"{key}: {n} chunks from {len(pages)} pages")
            else:
                print(f"{key}: no text extracted from the PDF")
            time.sleep(1)
    print(f"done: full text stored for {stored} more papers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
