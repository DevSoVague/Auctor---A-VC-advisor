"""Web search retrieval (Tavily), as an option next to the corpus retrieval.

    search source   what agents' search() uses
    corpus          the retrieval corpus: stub fixtures, or person 1's API   [default]
    web             Tavily web search only
    both            corpus results first, then Tavily results
    database        the Postgres + Milvus database (RAG + tables), see retrieval_db.py
    database+web    database results first, then Tavily results

Only search() goes to the web. Structured lookups (Open Targets targets, drugs, trials, approvals,
retractions) always come from the corpus side, because a web search cannot return those tables.

Leakage guard (the backtest must never see the future):
  - Tavily is asked for results before the as-of date (end_date = the day before).
  - Every result is dated from Tavily's published_date and dropped if it is on/after as_of.
  - Results with NO date are dropped in a backtest. In a live run (as_of is today or later) they are
    kept, labelled "undated", and dated the day before as_of so the evidence pack accepts them.

Needs TAVILY_API_KEY in frontier-engine/.env (https://app.tavily.com). Optional:
    TAVILY_DOMAINS=pubmed.ncbi.nlm.nih.gov,clinicaltrials.gov,fda.gov   only search these sites
    TAVILY_DEPTH=basic|advanced                                          advanced costs 2 credits per search
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from agents.analysis.retrieval import HttpRetrieval, StubRetrieval, before

SEARCH_SOURCES = ("corpus", "web", "both", "database", "database+web", "evstore", "evstore+web")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    m = _ISO.search(s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    try:  # "Mon, 19 Apr 2021 00:00:00 GMT"
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        return None


class WebSearchError(RuntimeError):
    pass


class TavilySearch:
    URL = "https://api.tavily.com/search"

    def __init__(self, as_of: date, api_key: str | None = None, client: Any = None,
                 domains: list[str] | None = None, depth: str | None = None) -> None:
        self.as_of = as_of
        self.key = (api_key or os.getenv("TAVILY_API_KEY") or "").strip()
        env_domains = [d.strip() for d in os.getenv("TAVILY_DOMAINS", "").split(",") if d.strip()]
        self.domains = domains if domains is not None else env_domains
        self.depth = depth or os.getenv("TAVILY_DEPTH", "basic")
        self.live = as_of >= date.today()  # live run: undated pages are allowed (see module doc)
        self._client = client
        self.cache: dict[str, dict] = {}
        self.calls: list[dict] = []

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def search(self, q: str, sources: list[str] | None = None, k: int = 10) -> list[dict]:
        if not self.key:
            raise WebSearchError("TAVILY_API_KEY is not set. Add it to frontier-engine/.env "
                                 "(key from https://app.tavily.com) or set the search source to 'corpus'.")
        body: dict[str, Any] = {"query": q[:400], "max_results": max(1, min(k, 20)), "search_depth": self.depth,
                                "include_published_date": True,
                                "end_date": (self.as_of - timedelta(days=1)).isoformat()}
        if self.domains:
            body["include_domains"] = self.domains
        resp = None
        for attempt in range(2):  # one retry on rate limit / server error
            resp = await self._http().post(self.URL, json=body, headers={"Authorization": f"Bearer {self.key}"})
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
            await asyncio.sleep(1.5)
        if resp.status_code == 401:
            raise WebSearchError("Tavily rejected the API key (401). Check TAVILY_API_KEY.")
        if resp.status_code == 429:
            raise WebSearchError("Tavily rate limit or credit limit reached (429).")
        resp.raise_for_status()
        out, dropped = [], 0
        for r in resp.json().get("results", []):
            d = _parse_date(r.get("published_date"))
            undated = d is None
            if undated and not self.live:
                dropped += 1  # backtest: an undated page could be from the future
                continue
            if undated:
                d = self.as_of - timedelta(days=1)
            doc = {"id": "web:" + hashlib.sha1(r["url"].encode()).hexdigest()[:10], "source": "web",
                   "source_date": d.isoformat(), "title": r.get("title") or r["url"], "url": r["url"],
                   "body": (r.get("content") or "")[:1200],
                   "tags": ["web"] + (["undated"] if undated else [])}
            if not before(doc, self.as_of):
                dropped += 1
                continue
            self.cache[doc["id"]] = doc
            out.append(doc)
        self.calls.append({"query": q, "returned": len(out), "dropped": dropped})
        return out


class WithWebSearch:
    """Corpus retrieval for everything, plus web search for search() according to `mode`."""

    def __init__(self, base: Any, web: TavilySearch, mode: str) -> None:
        self.base, self.web, self.mode = base, web, mode

    def __getattr__(self, name: str):  # trials, targets, drugs, approvals, retractions, as_of, ...
        return getattr(self.base, name)

    async def search(self, q: str, sources: list[str] | None = None, k: int = 10) -> list[dict]:
        if sources and "web" not in sources:
            return await self.base.search(q, sources=sources, k=k)  # asked for a specific corpus source
        if self.mode == "web":
            return await self.web.search(q, k=k)
        base, web = await asyncio.gather(self.base.search(q, sources=sources, k=k),
                                         self.web.search(q, k=max(3, k // 2)))
        seen = {d["id"] for d in base or []}
        return list(base or []) + [d for d in web if d["id"] not in seen]

    async def doc(self, doc_id: str):
        if doc_id.startswith("web:"):
            return self.web.cache.get(doc_id)
        return await self.base.doc(doc_id)


def make_retrieval(as_of: date, retrieval: str = "stub", search: str = "corpus"):
    """retrieval: 'stub' or person 1's API URL. search: corpus | web | both | database | database+web | evstore | evstore+web."""
    if search not in SEARCH_SOURCES:
        raise ValueError(f"search source must be one of {SEARCH_SOURCES}, got '{search}'")
    base = StubRetrieval(as_of) if (retrieval or "stub") == "stub" else HttpRetrieval(as_of, retrieval)
    if search in ("evstore", "evstore+web"):  # the COPD evidence store (../evidence-store); see db_tools.py
        from agents.analysis.db_tools import EvidenceStoreRetrieval

        st = EvidenceStoreRetrieval(as_of)
        return st if search == "evstore" else WithWebSearch(st, TavilySearch(as_of), "both")
    if search in ("database", "database+web"):  # RAG (Milvus) + tables (Postgres); see retrieval_db.py
        from agents.analysis.retrieval_db import make_db

        db = make_db(as_of)
        return db if search == "database" else WithWebSearch(db, TavilySearch(as_of), "both")
    return base if search == "corpus" else WithWebSearch(base, TavilySearch(as_of), search)
