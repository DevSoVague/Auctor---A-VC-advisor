"""Agent 1's write path into the evidence store: check -> fetch -> ingest.

Rules (user, 2026-11-08):
  * The publication date is mandatory and comes from the official record when one exists
    (PubMed E-utilities, ClinicalTrials.gov v2, Crossref), else from page metadata. No reliable
    date (or year-only) -> rejected. Outside FLOOR <= date < as_of -> rejected.
  * What is stored must be identical to the source: `body` / `full_text` are the fetched text,
    verbatim. The model never writes record text; ingest_document takes only a fetch_id.
  * Same item already in the store -> discarded (duplicate): same id+version, same DOI, same URL,
    same text hash, or same normalized title in the same year. A newer ClinicalTrials.gov version
    of a stored trial is a new version (the store is versioned), not a duplicate.
  * After a write, the new record is chunked and embedded at once, so search_evidence finds it.

Tools exposed to the model (A1 only): check_exists, fetch_source, ingest_document.
Env: A1_MAX_INGESTS (default 5 per run), A1_INGEST_DRY_RUN=1 (everything but the commit),
TAVILY_API_KEY (only for plain web URLs; PubMed / trials / DOIs need no key).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

import httpx

from agents.analysis.db_tools import FLOOR, _fn, store_client  # noqa: F401  (sets sys.path for evstore)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CTGOV = "https://clinicaltrials.gov/api/v2/studies"
CROSSREF = "https://api.crossref.org/works"
TAVILY_EXTRACT = "https://api.tavily.com/extract"
UA = {"User-Agent": "frontier-engine-a1/0.1 (evidence ingest)"}
MIN_CHARS = 200
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


class IngestError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- normalizers
def norm_url(u: str | None) -> str:
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("#")[0].split("?")[0].rstrip("/")


def norm_text(t: str | None) -> str:
    return re.sub(r"\W+", " ", (t or "").lower()).strip()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _pubmed_date(node: ET.Element | None) -> tuple[date | None, str]:
    """(date, precision) from a PubMed <PubDate>/<ArticleDate>. precision: day | month | year | none."""
    if node is None:
        return None, "none"
    y = node.findtext("Year")
    if not y:
        md = node.findtext("MedlineDate") or ""
        m = re.match(r"(\d{4})\s+([A-Za-z]{3})", md)
        if m:
            return date(int(m.group(1)), MONTHS[m.group(2).lower()], 1), "month"
        return None, "none"
    mo = node.findtext("Month")
    d = node.findtext("Day")
    if not mo:
        return date(int(y), 1, 1), "year"
    mon = int(mo) if mo.isdigit() else MONTHS.get(mo[:3].lower(), 0)
    if not mon:
        return date(int(y), 1, 1), "year"
    if d and d.isdigit():
        return date(int(y), mon, int(d)), "day"
    return date(int(y), mon, 1), "month"


def _iso_partial(s: str | None) -> tuple[date | None, str]:
    """'2024-05-17' / '2024-05' / '2024' -> (date, precision)."""
    if not s:
        return None, "none"
    m = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s.strip())
    if not m:
        return None, "none"
    y, mo, d = int(m.group(1)), m.group(2), m.group(3)
    if d:
        return date(y, int(mo), int(d)), "day"
    if mo:
        return date(y, int(mo), 1), "month"
    return date(y, 1, 1), "year"


META_DATE = [
    r'<meta[^>]+(?:name|property)=["\'](?:citation_publication_date|citation_date|article:published_time|'
    r'dc\.date|DC\.date\.issued|datePublished|pubdate|publish-date|og:published_time)["\'][^>]+content=["\']([^"\']+)',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'<time[^>]+datetime=["\']([^"\']+)',
]


def page_date(raw_html: str) -> tuple[date | None, str, str]:
    for pat in META_DATE:
        m = re.search(pat, raw_html, re.I)
        if m:
            d, prec = _iso_partial(m.group(1).replace("/", "-"))
            if d:
                return d, prec, "page_meta"
    return None, "none", "none"


def html_text(raw_html: str) -> str:
    t = re.sub(r"(?is)<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", " ", raw_html)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


# ----------------------------------------------------------------------------- the ingestor
class Ingestor:
    """One per run. Holds fetched items (by fetch_id) until ingest_document stores or discards them."""

    def __init__(self, as_of: date, floor: date = FLOOR, dsn: str | None = None,
                 max_ingests: int | None = None, dry_run: bool | None = None, http: Any = None) -> None:
        self.as_of, self.floor, self.dsn = as_of, floor, dsn
        self.max_ingests = int(os.getenv("A1_MAX_INGESTS", 5)) if max_ingests is None else max_ingests
        self.dry_run = (os.getenv("A1_INGEST_DRY_RUN", "0") == "1") if dry_run is None else dry_run
        self._http = http
        self.fetched: dict[str, dict] = {}
        self.outcomes: list[dict] = []
        self._conn = None
        self.pack = None  # EvidencePack: inserted records are added to it at once (citable by A1)

    # -- plumbing
    def http(self):
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30, headers=UA, follow_redirects=True)
        return self._http

    def conn(self):
        if self._conn is None:
            from evstore.db import connect

            self._conn = connect(self.dsn)
        return self._conn

    def _q(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.conn().cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    async def q(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        return await asyncio.to_thread(self._q, sql, params)

    @property
    def inserted(self) -> list[str]:
        return [o["id"] for o in self.outcomes if o["status"] in ("inserted", "new_version")]

    def tools(self) -> tuple[dict, list[dict]]:
        """(name -> async fn, OpenAI tool schemas) for ToolExecutor(extra=...) / tools_for(extra_tools=...)."""
        fns = {"pubmed_search": self.pubmed_search, "trials_search": self.trials_search,
               "check_exists": self.check_exists, "fetch_source": self.fetch_source,
               "ingest_document": self.ingest_document}
        return fns, INGEST_TOOLS

    # -- 0. discover (free official APIs; web_search covers the rest when TAVILY_API_KEY is set)
    def _range(self, after: str | None, before: str | None) -> tuple[date, date]:
        lo, hi = self.floor, self.as_of
        try:
            lo = max(lo, date.fromisoformat(after[:10])) if after else lo
            hi = min(hi, date.fromisoformat(before[:10])) if before else hi
        except ValueError:
            pass
        return lo, hi

    async def pubmed_search(self, query: str, published_after: str | None = None,
                            published_before: str | None = None, k: int = 10) -> dict:
        lo, hi = self._range(published_after, published_before)
        last = date.fromordinal(hi.toordinal() - 1)
        r = await self._get(f"{EUTILS}/esearch.fcgi", db="pubmed", term=query, retmode="json", sort="pub_date",
                            datetype="pdat", mindate=lo.strftime("%Y/%m/%d"), maxdate=last.strftime("%Y/%m/%d"),
                            retmax=max(1, min(int(k or 10), 20)))
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return {"window": {"from": lo.isoformat(), "before": hi.isoformat()}, "count": 0, "results": []}
        s = (await self._get(f"{EUTILS}/esummary.fcgi", db="pubmed", id=",".join(ids), retmode="json")).json()["result"]
        stored = {r["id"] for r in await self.q("SELECT DISTINCT id FROM documents WHERE id = ANY(%s)",
                                                ([f"pmid:{i}" for i in ids],))}
        out = []
        for i in ids:
            d = s.get(i, {})
            out.append({"pmid": i, "title": d.get("title"), "journal": d.get("source"),
                        "published": d.get("sortpubdate", "")[:10].replace("/", "-"), "pubdate": d.get("pubdate"),
                        "in_store": f"pmid:{i}" in stored})
        return {"window": {"from": lo.isoformat(), "before": hi.isoformat()}, "count": len(out), "results": out}

    async def trials_search(self, query: str = "COPD", updated_after: str | None = None,
                            updated_before: str | None = None, k: int = 10) -> dict:
        lo, hi = self._range(updated_after, updated_before)
        last = date.fromordinal(hi.toordinal() - 1)
        r = await self._get(CTGOV, **{"query.term": query, "pageSize": max(1, min(int(k or 10), 20)),
                                      "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{lo.isoformat()},{last.isoformat()}]",
                                      "fields": "NCTId,BriefTitle,OverallStatus,Phase,LastUpdatePostDate,StudyFirstPostDate"})
        studies = r.json().get("studies", [])
        ids = [x["protocolSection"]["identificationModule"]["nctId"] for x in studies]
        stored = {r["id"] for r in await self.q("SELECT DISTINCT id FROM documents WHERE id = ANY(%s)",
                                                ([f"nct:{i}" for i in ids],))}
        out = []
        for x in studies:
            p = x["protocolSection"]
            st = p.get("statusModule", {})
            nct = p["identificationModule"]["nctId"]
            out.append({"nct": nct, "title": p["identificationModule"].get("briefTitle"),
                        "status": st.get("overallStatus"), "phase": (p.get("designModule") or {}).get("phases"),
                        "published": (st.get("lastUpdatePostDateStruct") or {}).get("date"),
                        "first_posted": (st.get("studyFirstPostDateStruct") or {}).get("date"),
                        "in_store": f"nct:{nct}" in stored})
        return {"window": {"from": lo.isoformat(), "before": hi.isoformat()}, "count": len(out), "results": out}

    # -- 1. check
    async def check_exists(self, pmid: str | None = None, doi: str | None = None, nct: str | None = None,
                           url: str | None = None, title: str | None = None, year: int | None = None) -> dict:
        matches: list[dict] = []

        async def hit(how: str, sql: str, params: tuple):
            for r in await self.q(sql, params):
                matches.append({"id": r["id"], "published": r["source_date"].isoformat(), "how": how})

        base = "SELECT DISTINCT ON (id) id, source_date FROM documents WHERE "
        if pmid:
            await hit("pmid", base + "id = %s ORDER BY id, source_date DESC", (f"pmid:{str(pmid).split(':')[-1]}",))
        if nct:
            n = str(nct).split(":")[-1].upper()
            await hit("nct", base + "id = %s ORDER BY id, source_date DESC", (f"nct:{n}",))
        if doi:
            d = doi.lower().removeprefix("https://doi.org/").strip()
            await hit("doi", base + "lower(fields->>'doi') = %s ORDER BY id, source_date DESC", (d,))
            await hit("doi", "SELECT p.doc_id AS id, p.published_date AS source_date FROM publications p "
                             "WHERE lower(p.doi) = %s", (d,))
        if url:
            nu = norm_url(url)
            rows = await self.q("SELECT id, source_date, url FROM documents WHERE url IS NOT NULL")
            matches += [{"id": r["id"], "published": r["source_date"].isoformat(), "how": "url"}
                        for r in rows if norm_url(r["url"]) == nu]
        if title:
            nt = norm_text(title)
            rows = await self.q("SELECT id, source_date, title FROM documents WHERE title IS NOT NULL")
            matches += [{"id": r["id"], "published": r["source_date"].isoformat(), "how": "title"}
                        for r in rows if norm_text(r["title"]) == nt
                        and (year is None or r["source_date"].year == int(year))]
        seen, uniq = set(), []
        for m in matches:
            if (m["id"], m["how"]) not in seen:
                seen.add((m["id"], m["how"]))
                uniq.append(m)
        return {"exists": bool(uniq), "matches": uniq}

    # -- 2. fetch
    async def fetch_source(self, pmid: str | None = None, nct: str | None = None, doi: str | None = None,
                           url: str | None = None) -> dict:
        try:
            if pmid:
                item = await self._fetch_pubmed(str(pmid).split(":")[-1])
            elif nct:
                item = await self._fetch_ctgov(str(nct).split(":")[-1].upper())
            elif doi:
                item = await self._fetch_doi(doi)
            elif url:
                m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
                n = re.search(r"(NCT\d{8})", url, re.I)
                d = re.search(r"doi\.org/(10\.\S+)", url)
                item = (await self._fetch_pubmed(m.group(1)) if m else
                        await self._fetch_ctgov(n.group(1).upper()) if n else
                        await self._fetch_doi(d.group(1)) if d else await self._fetch_url(url))
            else:
                return {"error": "give one of pmid, nct, doi, url"}
        except IngestError as e:
            return {"error": str(e)}
        except httpx.HTTPError as e:
            return {"error": f"download failed: {type(e).__name__}: {str(e)[:200]}"}
        fid = "f" + sha(item["id"] + str(item["source_date"]))[:10]
        self.fetched[fid] = item
        dup = await self.check_exists(pmid=item["id"][5:] if item["id"].startswith("pmid:") else None,
                                      nct=item["id"][4:] if item["id"].startswith("nct:") else None,
                                      doi=(item["fields"] or {}).get("doi"), url=item.get("url"),
                                      title=item["title"], year=item["source_date"].year if item["source_date"] else None)
        return {"fetch_id": fid, "id": item["id"], "source": item["source"], "title": item["title"],
                "published": item["source_date"].isoformat() if item["source_date"] else None,
                "date_source": item["fields"].get("date_source"), "date_precision": item["fields"].get("date_precision"),
                "in_window": bool(item["source_date"] and self.floor <= item["source_date"] < self.as_of),
                "chars": len(item["body"] or "") + len(item.get("full_text") or ""),
                "has_full_text": bool(item.get("full_text")),
                "preview": (item["body"] or "")[:600], "already_in_store": dup}

    async def _get(self, url: str, **params) -> httpx.Response:
        """GET with NCBI etiquette: <=3 requests/s without a key (NCBI_API_KEY lifts it to 10), retry on 429."""
        if url.startswith(EUTILS):
            if os.getenv("NCBI_API_KEY"):
                params["api_key"] = os.environ["NCBI_API_KEY"].strip()
            await asyncio.sleep(0.12 if os.getenv("NCBI_API_KEY") else 0.4)
        for attempt in range(3):
            r = await self.http().get(url, params=params or None)
            if r.status_code != 429:
                break
            await asyncio.sleep(1.5 * (attempt + 1))
        r.raise_for_status()
        return r

    async def _fetch_pubmed(self, pmid: str) -> dict:
        r = await self._get(f"{EUTILS}/efetch.fcgi", db="pubmed", id=pmid, retmode="xml")
        root = ET.fromstring(r.text)
        art = root.find(".//PubmedArticle")
        if art is None:
            raise IngestError(f"PubMed has no record {pmid}")
        a = art.find(".//Article")
        title = "".join(a.find("ArticleTitle").itertext()).strip() if a is not None and a.find("ArticleTitle") is not None else None
        parts = []
        for t in art.findall(".//Abstract/AbstractText"):
            txt = "".join(t.itertext()).strip()
            parts.append(f"{t.get('Label')}: {txt}" if t.get("Label") else txt)
        body = "\n".join(parts)
        d, prec = _pubmed_date(art.find(".//Article/ArticleDate"))
        src = "pubmed_article_date"
        if d is None:
            d, prec = _pubmed_date(art.find(".//Journal/JournalIssue/PubDate"))
            src = "pubmed_journal_pubdate"
        ids = {i.get("IdType"): (i.text or "").strip() for i in art.findall(".//PubmedData/ArticleIdList/ArticleId")}
        doi = ids.get("doi") or next((e.text for e in art.findall(".//ELocationID") if e.get("EIdType") == "doi"), None)
        ptypes = [p.text for p in art.findall(".//PublicationTypeList/PublicationType") if p.text]
        journal = art.findtext(".//Journal/Title")
        full_text, sections = None, []
        if ids.get("pmc"):
            try:
                full_text, sections = await self._fetch_pmc(ids["pmc"])
            except Exception:  # noqa: BLE001 - abstract alone is still the official text
                full_text, sections = None, []
        return {"id": f"pmid:{pmid}", "source": "pubmed", "source_date": d, "title": title, "body": body,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "full_text": full_text, "sections": sections,
                "version_label": "pubmed-a1",
                "fields": {"doi": doi, "pmcid": ids.get("pmc"), "journal": journal, "publication_types": ptypes,
                           "date_source": src, "date_precision": prec,
                           "full_text_from": "pmc" if full_text else None}}

    async def _fetch_pmc(self, pmcid: str) -> tuple[str | None, list[dict]]:
        r = await self._get(f"{EUTILS}/efetch.fcgi", db="pmc", id=pmcid, retmode="xml")
        root = ET.fromstring(r.text)
        body = root.find(".//body")
        if body is None:
            return None, []
        sections = []
        for sec in body.findall("sec"):
            head = (sec.findtext("title") or "Section").strip()
            text = "\n".join(" ".join("".join(p.itertext()).split()) for p in sec.iter("p"))
            if text.strip():
                sections.append({"section": head, "text": text})
        full = "\n\n".join(f"{s['section']}\n{s['text']}" for s in sections)
        return (full or None), sections

    async def _fetch_ctgov(self, nct: str) -> dict:
        r = await self._get(f"{CTGOV}/{nct}")
        p = r.json().get("protocolSection") or {}
        idm, st, ds = p.get("identificationModule", {}), p.get("statusModule", {}), p.get("designModule", {})
        sp = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
        cond = p.get("conditionsModule", {}).get("conditions", [])
        arms = p.get("armsInterventionsModule", {}).get("interventions", [])
        outs = p.get("outcomesModule", {}).get("primaryOutcomes", [])
        desc = p.get("descriptionModule", {})
        ver_s = (st.get("lastUpdatePostDateStruct") or {}).get("date")
        d, prec = _iso_partial(ver_s)
        enr = ds.get("enrollmentInfo") or {}
        pc = st.get("primaryCompletionDateStruct") or {}
        lines = [f"Conditions: {', '.join(cond)}", f"Phase: {', '.join(ds.get('phases') or []) or 'NA'}",
                 f"Sponsor: {sp.get('name')} ({sp.get('class')})",
                 "Interventions: " + "; ".join(f"{i.get('type')}: {i.get('name')}" for i in arms),
                 f"Status: {st.get('overallStatus')}"]
        if st.get("whyStopped"):
            lines.append(f"Why stopped: {st['whyStopped']}")
        lines += [f"Enrollment: {enr.get('count')} ({enr.get('type')})",
                  f"Primary completion: {pc.get('date')} ({pc.get('type')})",
                  "Primary outcomes: " + " | ".join(o.get("measure", "") for o in outs)]
        if desc.get("briefSummary"):
            lines.append(desc["briefSummary"].strip())
        g = lambda k: (st.get(k) or {}).get("date")  # noqa: E731
        return {"id": f"nct:{nct}", "source": "ctgov", "source_date": d, "title": idm.get("briefTitle"),
                "body": "\n".join(lines), "url": f"https://clinicaltrials.gov/study/{nct}", "full_text": None,
                "sections": [], "version_label": "ctgov-live",
                "fields": {"nct_id": nct, "phase": ", ".join(ds.get("phases") or []), "status": st.get("overallStatus"),
                           "why_stopped": st.get("whyStopped"), "sponsor": sp.get("name"), "sponsor_class": sp.get("class"),
                           "conditions": ", ".join(cond), "enrollment": enr.get("count"),
                           "interventions": "; ".join(f"{i.get('type')}: {i.get('name')}" for i in arms),
                           "primary_outcomes": " | ".join(o.get("measure", "") for o in outs),
                           "first_posted": g("studyFirstPostDateStruct"), "last_update_posted": ver_s,
                           "start": g("startDateStruct"), "primary_completion": pc.get("date"),
                           "completion": g("completionDateStruct"), "results_first_posted": g("resultsFirstPostDateStruct"),
                           "date_source": "ctgov_last_update_posted", "date_precision": prec}}

    async def _fetch_doi(self, doi: str) -> dict:
        doi = doi.lower().removeprefix("https://doi.org/").strip()
        r = await self._get(f"{EUTILS}/esearch.fcgi", db="pubmed", term=f"{doi}[doi]", retmode="json")
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if ids:
            return await self._fetch_pubmed(ids[0])
        m = (await self._get(f"{CROSSREF}/{doi}")).json()["message"]
        d, prec, src = None, "none", "none"
        for key in ("published-online", "published-print", "published", "issued"):
            parts = (m.get(key) or {}).get("date-parts") or [[]]
            if parts[0]:
                y, mo, dd = (parts[0] + [None, None])[:3]
                d = date(y, mo or 1, dd or 1)
                prec, src = ("day" if dd else "month" if mo else "year"), f"crossref_{key}"
                break
        abstract = re.sub(r"<[^>]+>", " ", m.get("abstract") or "")
        abstract = re.sub(r"\s+", " ", html.unescape(abstract)).strip()
        if len(abstract) < MIN_CHARS and os.getenv("TAVILY_API_KEY"):
            ext = await self._fetch_url(f"https://doi.org/{doi}")
            abstract = ext["body"]
        return {"id": f"doi:{doi}", "source": "news", "source_date": d,  # not in PubMed: stored under the store's 'news' source
                "title": (m.get("title") or [None])[0], "body": abstract, "url": f"https://doi.org/{doi}",
                "full_text": None, "sections": [], "version_label": "crossref-a1",
                "fields": {"doi": doi, "journal": (m.get("container-title") or [None])[0], "kind": "journal_article",
                           "date_source": src, "date_precision": prec}}

    async def _fetch_url(self, url: str) -> dict:
        raw = ""
        try:
            raw = (await self._get(url)).text
        except httpx.HTTPError:
            raw = ""
        d, prec, src = page_date(raw) if raw else (None, "none", "none")
        text, how = "", "html"
        key = os.getenv("TAVILY_API_KEY", "").strip()
        if key:
            r = await self.http().post(TAVILY_EXTRACT, json={"urls": [url]}, headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 200 and r.json().get("results"):
                text, how = (r.json()["results"][0].get("raw_content") or "").strip(), "tavily_extract"
        if not text and raw:
            text = html_text(raw)
        if len(text) < MIN_CHARS:
            raise IngestError(f"could not download readable text from {url} ({len(text)} chars)")
        title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        title = html.unescape(title_m.group(1).strip()) if title_m else url
        return {"id": "news:" + sha(norm_url(url))[:12], "source": "news", "source_date": d, "title": title,
                "body": text, "url": url, "full_text": None, "sections": [], "version_label": "web-a1",
                "fields": {"date_source": src, "date_precision": prec, "text_from": how}}

    # -- 3. ingest
    async def ingest_document(self, fetch_id: str) -> dict:
        item = self.fetched.get(fetch_id)
        if item is None:
            return self._out("rejected", None, f"unknown fetch_id {fetch_id}; call fetch_source first")
        did, d = item["id"], item["source_date"]
        prec = item["fields"].get("date_precision")
        if d is None or prec in ("none", "year"):
            return self._out("rejected", did, f"no reliable publication date (precision: {prec})")
        if not (self.floor <= d < self.as_of):
            return self._out("rejected", did, f"published {d} is outside {self.floor} <= date < {self.as_of}")
        if len((item["body"] or "") + (item.get("full_text") or "")) < MIN_CHARS:
            return self._out("rejected", did, "too little source text to store")
        if len(self.inserted) >= self.max_ingests:
            return self._out("rejected", did, f"ingest limit for this run reached ({self.max_ingests})")

        title, body, full = item["title"], item["body"], item.get("full_text")
        text_hash = sha(f"{title}\n{body}\n{full or ''}")
        same_ver = await self.q("SELECT 1 FROM documents WHERE id = %s AND source_date = %s", (did, d))
        if same_ver:
            return self._out("duplicate", did, f"{did} version {d} is already stored")
        same_text = await self.q("SELECT id FROM documents WHERE text_hash = %s LIMIT 1", (text_hash,))
        if same_text:
            return self._out("duplicate", did, f"identical text already stored as {same_text[0]['id']}")
        dup = await self.check_exists(doi=item["fields"].get("doi"), url=item["url"], title=title, year=d.year)
        other = [m for m in dup["matches"] if m["id"] != did]
        if other:
            return self._out("duplicate", did, f"same item already stored: {other[0]}")
        newer_version = bool(await self.q("SELECT 1 FROM documents WHERE id = %s", (did,)))
        if newer_version and item["source"] != "ctgov":
            return self._out("duplicate", did, f"{did} already stored (other date)")

        from psycopg.types.json import Jsonb

        from evstore.db import clean

        fields = {k: v for k, v in item["fields"].items() if v not in (None, "", [])}
        fields.update(ingested_by="a1", fetched_on=date.today().isoformat(), content_sha=sha(body or ""))
        try:
            with self.conn().transaction():
                self._q("""INSERT INTO documents (id, source, source_date, version_label, title, body, url, fields,
                                                  text_hash, full_text, origin)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'a1')""",
                        (did, item["source"], d, item["version_label"], clean(title), clean(body), item["url"],
                         Jsonb(fields), text_hash, clean(full)))
                if item["source"] == "pubmed":
                    f = item["fields"]
                    self._q("""INSERT INTO publications (doc_id, pmid, doi, pmcid, title, journal, published_date,
                                                         publication_types, has_full_text, full_text_from, url)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (did, did[5:], f.get("doi"), f.get("pmcid"), title, f.get("journal"), d,
                             f.get("publication_types") or [], bool(full), f.get("full_text_from"), item["url"]))
                    for i, s in enumerate(item["sections"]):
                        self._q("""INSERT INTO parent_chunks (parent_id, doc_id, position, section, text, published_date)
                                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                (f"parent:{did}:a1s{i}", did, i, s["section"], clean(s["text"]), d))
                if item["source"] == "ctgov":
                    f = item["fields"]
                    self._q("""INSERT INTO trials (trial_id, published_date, version_label, title, phase, status,
                                                   start_date, completion_date, enrollment, primary_outcome,
                                                   why_stopped, sponsor)
                               VALUES (%s,%s,'ctgov-live',%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (did[4:], d, title, f.get("phase"), f.get("status"),
                             _iso_partial(f.get("start"))[0], _iso_partial(f.get("completion"))[0],
                             f.get("enrollment"), f.get("primary_outcomes"), f.get("why_stopped"), f.get("sponsor")))
                n_chunks = self._chunk(item, d)
                self._q("""INSERT INTO ingest_log (source, query, fetched, stored, skipped)
                           VALUES ('a1', %s, 1, 1, 0)""", (did,))
                if self.dry_run:
                    raise _DryRun()
        except _DryRun:
            return self._out("dry_run", did, f"would insert {did} ({d}), {n_chunks} chunks", n_chunks=n_chunks)
        embedded = await self._embed()
        if self.pack is not None:
            ev = {"id": did, "source": item["source"], "source_date": d.isoformat(),
                  "date_kind": item["fields"].get("date_source"), "title": title, "body": (body or "")[:3000],
                  "url": item["url"], "tags": ["new", "ingested_by_a1"]}
            self.pack.add([ev])
        return self._out("new_version" if newer_version else "inserted", did,
                         f"{item['source']} published {d} ({item['fields'].get('date_source')})",
                         n_chunks=n_chunks, embedded=embedded, published=d.isoformat())

    def _chunk(self, item: dict, d: date) -> int:
        from evstore.chunk import _rows_for
        from evstore.config import EMBED_MODEL

        doc = {"id": item["id"], "source": item["source"], "source_date": d, "title": item["title"],
               "body": item["body"], "fields": item["fields"]}
        parents = [{"parent_id": f"parent:{item['id']}:a1s{i}", "position": i, "section": s["section"],
                    "text": s["text"], "published_date": d} for i, s in enumerate(item["sections"])]
        rows = _rows_for(doc, parents)
        for r in rows:
            r["embed_hash"] = sha(r["embed_input"])
            self._q("""INSERT INTO chunks (chunk_id, doc_id, doc_version, parent_id, source, section, position,
                                           text, published_date, embed_hash, embedding)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               (SELECT embedding FROM embedding_cache WHERE embed_hash = %s AND model = %s))
                       ON CONFLICT DO NOTHING""",
                    (r["chunk_id"], r["doc_id"], r["doc_version"], r["parent_id"], r["source"], r["section"],
                     r["position"], r["text"], r["published_date"], r["embed_hash"], r["embed_hash"], EMBED_MODEL))
        self._pending = {r["embed_hash"]: r["embed_input"] for r in rows}
        return len(rows)

    async def _embed(self) -> int:
        """Embed this document's chunks now (one Nebius call), so search finds it."""
        from evstore.config import EMBED_MODEL
        from evstore.embed import embed_texts

        pending = getattr(self, "_pending", {})
        have = {r["embed_hash"] for r in await self.q(
            "SELECT embed_hash FROM embedding_cache WHERE embed_hash = ANY(%s) AND model = %s",
            (list(pending), EMBED_MODEL))}
        todo = sorted(h for h in pending if h not in have)
        if todo:
            vecs, _ = await embed_texts([pending[h] for h in todo])

            def write():
                with self.conn().transaction(), self.conn().cursor() as cur:
                    for h, v in zip(todo, vecs):
                        cur.execute("INSERT INTO embedding_cache (embed_hash, model, embedding) VALUES (%s,%s,%s) "
                                    "ON CONFLICT DO NOTHING", (h, EMBED_MODEL, v))
            await asyncio.to_thread(write)
        await self.q("""UPDATE chunks c SET embedding = e.embedding FROM embedding_cache e
                        WHERE c.embedding IS NULL AND e.embed_hash = c.embed_hash AND e.model = %s
                          AND c.embed_hash = ANY(%s)""", (EMBED_MODEL, list(pending)))
        self.conn().commit()
        return len(todo)

    def _out(self, status: str, did: str | None, reason: str, **extra) -> dict:
        o = {"status": status, "id": did, "reason": reason, **extra}
        self.outcomes.append(o)
        return o


class _DryRun(Exception):
    pass


INGEST_TOOLS = [
    _fn("pubmed_search",
        "Search PubMed (official API) for papers published inside the window, newest first. Each result says "
        "whether it is already in the store (in_store).",
        {"query": {"type": "string", "description": "PubMed query, e.g. COPD[ti] AND randomized controlled trial[pt]"},
         "published_after": {"type": "string"}, "published_before": {"type": "string"}, "k": {"type": "integer"}},
        ["query"]),
    _fn("trials_search",
        "Search ClinicalTrials.gov for trials whose record was updated inside the window. Each result says "
        "whether it is already in the store (in_store).",
        {"query": {"type": "string"}, "updated_after": {"type": "string"}, "updated_before": {"type": "string"},
         "k": {"type": "integer"}}),
    _fn("check_exists",
        "Is this item already in the evidence store? Match by pmid, nct, doi, url, or title (+year). "
        "Returns matches with their stored publication dates.",
        {"pmid": {"type": "string"}, "nct": {"type": "string"}, "doi": {"type": "string"},
         "url": {"type": "string"}, "title": {"type": "string"}, "year": {"type": "integer"}}),
    _fn("fetch_source",
        "Download an item from its official source (PubMed for pmid, ClinicalTrials.gov for nct, Crossref/PubMed "
        "for doi, else the web page). Returns a fetch_id, the official publication date and where it came "
        "from, a preview, and whether it is already in the store. Nothing is stored yet.",
        {"pmid": {"type": "string"}, "nct": {"type": "string"}, "doi": {"type": "string"}, "url": {"type": "string"}}),
    _fn("ingest_document",
        "Store a fetched item in the evidence store, verbatim. Rejected without a reliable publication date "
        "or outside the date window; discarded if the same item is already stored.",
        {"fetch_id": {"type": "string"}}, ["fetch_id"]),
]
