"""HOOK IN (person 1): retrieval bound to as_of.

Two clients with the same async interface:
    StubRetrieval  - serves tests/fixtures/retrieval/corpus.json (no DB needed)
    HttpRetrieval  - calls person 1/2's FastAPI retrieval endpoints (PRD api/retrieval.py)

Every returned record is a dict with at least: id, source, source_date (ISO), title, body.
Both clients drop anything dated on or after as_of. The server also does this; we
re-check client-side as a second guard (leakage control).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from agents.analysis.context import FIXTURES


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s[:10]) if s else None


def before(rec: dict, as_of: date) -> bool:
    d = _d(rec.get("source_date"))
    return d is not None and d < as_of


class StubRetrieval:
    def __init__(self, as_of: date, path: Path = FIXTURES / "retrieval" / "corpus.json"):
        self.as_of = as_of
        raw = json.loads(path.read_text())
        self.docs: list[dict] = raw["documents"]
        self.trials_raw: list[dict] = raw["trials"]
        self.target_releases: list[dict] = raw["opentargets_releases"]
        self.drugs_raw: list[dict] = raw["drugs"]
        self.retractions_raw: list[dict] = raw["retractions"]

    async def search(self, q: str, sources: list[str] | None = None, k: int = 10) -> list[dict]:
        terms = [t.lower() for t in q.split() if len(t) > 2]
        hits = []
        for d in self.docs:
            if not before(d, self.as_of) or (sources and d["source"] not in sources):
                continue
            hay = f"{d['title']} {d['body']} {' '.join(d.get('tags', []))}".lower()
            score = sum(t in hay for t in terms)
            if score:
                hits.append((score, d["source_date"], d))
        hits.sort(key=lambda h: (-h[0], h[1]))
        return [h[2] for h in hits[:k]]

    async def doc(self, doc_id: str) -> dict | None:
        return next((d for d in self.docs if d["id"] == doc_id and before(d, self.as_of)), None)

    async def trials(self, condition: str = "COPD", phase: str | None = None,
                     status: str | None = None) -> list[dict]:
        out = []
        for t in self.trials_raw:
            if not before(t, self.as_of):
                continue
            hist = [h for h in t["status_history"] if _d(h["on"]) < self.as_of]
            rec = {k: v for k, v in t.items() if k != "status_history"}
            rec["status"] = hist[-1]["status"] if hist else "unknown"
            rec["why_stopped"] = hist[-1].get("why_stopped") if hist else None
            if phase and phase not in rec["phase"]:
                continue
            if status and rec["status"] != status:
                continue
            out.append(rec)
        return out

    async def targets(self, disease_id: str) -> list[dict]:
        rels = [r for r in self.target_releases if before(r, self.as_of)]
        if not rels:
            return []
        rel = max(rels, key=lambda r: r["source_date"])
        return [{**a, "id": rel["id"], "source_date": rel["source_date"]}
                for a in rel["associations"]]

    async def drugs(self, mechanism: str | None = None, indication: str | None = None) -> list[dict]:
        out = []
        for d in self.drugs_raw:
            if not before(d, self.as_of):
                continue
            if mechanism and mechanism.lower() not in d["mechanism"].lower():
                continue
            if indication and indication.lower() not in " ".join(d["indications"]).lower():
                continue
            out.append(d)
        return out

    async def approvals(self, indication: str = "COPD") -> list[dict]:
        return [d for d in self.docs if d["source"] == "fda" and before(d, self.as_of)
                and indication.lower() in d["body"].lower()]

    async def retractions(self, doi: str | None = None) -> list[dict]:
        return [r for r in self.retractions_raw if before(r, self.as_of)
                and (doi is None or r["doi"] == doi)]


class HttpRetrieval:
    """Same interface over person 1/2's FastAPI service. Endpoints per PRD."""

    def __init__(self, as_of: date, base_url: str = "http://localhost:8000"):
        import httpx

        self.as_of = as_of
        self.http = httpx.AsyncClient(base_url=base_url, timeout=30)

    async def _get(self, path: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        params["as_of"] = self.as_of.isoformat()
        r = await self.http.get(path, params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):  # client-side leakage guard
            data = [x for x in data if "source_date" not in x or before(x, self.as_of)]
        return data

    async def search(self, q, sources=None, k=10):
        return await self._get("/search", q=q, sources=",".join(sources) if sources else None, k=k)

    async def doc(self, doc_id):
        return await self._get(f"/doc/{doc_id}")

    async def trials(self, condition="COPD", phase=None, status=None):
        return await self._get("/trials", condition=condition, phase=phase, status=status)

    async def targets(self, disease_id):
        return await self._get(f"/targets/{disease_id}") or []

    async def drugs(self, mechanism=None, indication=None):
        return await self._get("/drugs", mechanism=mechanism, indication=indication)

    async def approvals(self, indication="COPD"):
        return await self._get("/approvals", indication=indication)

    async def retractions(self, doi=None):
        return await self._get("/retractions", doi=doi)
