"""The evidence store (../evidence-store, Postgres + pgvector) as LLM tool calls, for every agent.

Two things live here:

    EvidenceStoreRetrieval  the fixed retrieve() interface (search, doc, trials, drugs, approvals, ...)
                            over the store, returning the usual evidence dicts. search source 'evstore'.
    ToolExecutor            runs one OpenAI-format tool call for an agent, adds what it found to the
                            agent's EvidencePack, and returns compact JSON for the model.

Dates come first. Every item an agent sees carries:
    published   the record date (source_date): publication / version / label / curated date
    date_kind   what that date means (see DATE_KIND)
    age_days    days before as_of
    + the record's own dated fields (trial first_posted, start, completion, shelved_date, ...)

Window: FLOOR <= date < as_of. FLOOR defaults to 2023-01-01 (the COVID years 2020-22 confound COPD
data; env DATA_FLOOR overrides). as_of is the run's cutoff, enforced by StoreClient in SQL as well.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

EVSTORE = Path(os.getenv("EVSTORE_PATH", Path(__file__).resolve().parents[3] / "evidence-store")).resolve()
if str(EVSTORE) not in sys.path:
    sys.path.insert(0, str(EVSTORE))

FLOOR = date.fromisoformat(os.getenv("DATA_FLOOR", "2023-01-01"))
# Reference tables (Open Targets target-disease associations) are not COVID-era study results, so they
# are exempt from FLOOR: any release dated before REFERENCE_CUTOFF (and before as_of) may be used.
# The store's targets table is empty today: we fall back to the fixture's 2020-11 release (13 COPD
# targets). TODO person 1: ingest Open Targets into the store's targets table (with tractability,
# genetic score, max phase), then this fallback stops being used.
REFERENCE_CUTOFF = date.fromisoformat(os.getenv("REFERENCE_CUTOFF", "2025-01-01"))

DATE_KIND = {
    "pubmed": "pubmed_publication_date",
    "ctgov": "ctgov_record_version_date",
    "fda": "fda_label_published",
    "curated": "curated_as_of",
    "news": "news_published",
    "web": "web_published_date",
    "market": "market_stats_published",
    "shelved": "shelved_report_published",
    "label_event": "fda_label_published",
}
TRIAL_DATES = ("first_posted", "last_update_posted", "start", "primary_completion", "completion",
               "results_first_posted")
SNIPPET = 900  # chars of body returned to the model per item


# ----------------------------------------------------------------------------- small helpers
def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v)[:10]


def _plain(v: Any) -> Any:
    """JSON-safe copy of a DB row value."""
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def in_window(d: str | date | None, as_of: date, floor: date = FLOOR) -> bool:
    if d is None:
        return False
    d = date.fromisoformat(_iso(d))
    return floor <= d < as_of


def _record_to_ev(rec: Any) -> dict:
    """evstore Record (or its dict) -> evidence dict the agents and EvidencePack use."""
    r = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
    f = dict(r.get("fields") or {})
    f.pop("full_text", None)
    src = r["source"]
    ev = {"id": r["id"], "source": src, "source_date": _iso(r["source_date"]),
          "date_kind": DATE_KIND.get(src, "source_date"), "title": r.get("title") or r["id"],
          "body": r.get("body") or "", "url": r.get("url")}
    if src == "ctgov":
        ev["dates"] = {k: f.get(k) for k in TRIAL_DATES if f.get(k)}
        for k in ("phase", "status", "why_stopped", "sponsor"):
            if f.get(k):
                ev[k] = f[k]
        if f.get("interventions"):
            ev["intervention"] = f["interventions"] if isinstance(f["interventions"], str) else json.dumps(f["interventions"])[:300]
        if f.get("primary_outcomes"):
            ev["primary_endpoint"] = str(f["primary_outcomes"])[:300]
    elif src == "pubmed":
        for k in ("doi", "journal"):
            if f.get(k):
                ev[k] = f[k]
    elif src == "fda":
        ev["dates"] = {k: f.get(k) for k in ("label_effective", "cms_release") if f.get(k)}
    elif src == "curated":
        ev["dates"] = {k: f.get(k) for k in ("date",) if f.get(k)}
    # evidence tags the agents' graders use (A2: human lines / failed precedent; A6: checklist)
    tags = ["human"] if src in ("pubmed", "ctgov", "fda", "curated") else []
    if src == "ctgov" and (str(f.get("status", "")).upper() in ("TERMINATED", "WITHDRAWN", "SUSPENDED")
                           or f.get("why_stopped")):
        tags.append("failure")
    if src == "curated" and (str(f.get("collection", "")) == "shelved" or "shelved" in r["id"]):
        tags.append("failure")
    if src == "fda":
        tags.append("safety")
    if tags:
        ev["tags"] = tags
    if f.get("matched_chunks"):
        ev["matched"] = [c["text"] for c in f["matched_chunks"]]
    return ev


def _row_record(kind: str, rid: str, d: Any, title: str, row: dict, dates: dict | None = None,
                source_ids: list | None = None) -> dict:
    """A table row as a citable evidence dict (body = the row as compact JSON)."""
    body = {k: v for k, v in _plain(row).items() if v not in (None, [], "")}
    ev = {"id": rid, "source": kind, "source_date": _iso(d), "date_kind": DATE_KIND.get(kind, "published_date"),
          "title": title, "body": json.dumps(body, default=str)}
    if dates:
        ev["dates"] = {k: _iso(v) for k, v in dates.items() if v}
    if source_ids:
        ev["source_ids"] = list(source_ids)
    return ev


# ----------------------------------------------------------------------------- the store client
_CLIENTS: dict[tuple, Any] = {}


def store_client(as_of: date, dsn: str | None = None, use_vectors: bool | None = None):
    """One StoreClient per (as_of, dsn): reused between nodes and agents."""
    from evstore import StoreClient

    if use_vectors is None:
        use_vectors = os.getenv("EVSTORE_VECTORS", "1") != "0"
    key = (as_of, dsn, use_vectors)
    if key not in _CLIENTS:
        _CLIENTS[key] = StoreClient(as_of, dsn=dsn, use_vectors=use_vectors)
    return _CLIENTS[key]


class EvidenceStoreRetrieval:
    """The agents' retrieval interface over the evidence store. Dicts out, FLOOR <= date < as_of."""

    label = "evstore"

    def __init__(self, as_of: date, floor: date = FLOOR, dsn: str | None = None) -> None:
        self.as_of, self.floor = as_of, floor
        self.store = store_client(as_of, dsn)

    def _keep(self, evs: list[dict]) -> list[dict]:
        return [e for e in evs if in_window(e["source_date"], self.as_of, self.floor)]

    async def search(self, q: str, sources: list[str] | None = None, k: int = 10) -> list[dict]:
        src = [s for s in (sources or []) if s in ("pubmed", "ctgov", "fda", "curated", "news")] or None
        return self._keep([_record_to_ev(r) for r in await self.store.search(q, sources=src, k=k)])

    async def doc(self, doc_id: str) -> dict | None:
        r = await self.store.doc(doc_id)
        if r is None:
            return None
        ev = _record_to_ev(r)
        return ev if in_window(ev["source_date"], self.as_of, self.floor) else None

    async def trials(self, condition: str = "COPD", phase: str | None = None, status: str | None = None) -> list[dict]:
        phase = f"PHASE{phase}" if phase and phase.isdigit() else phase
        return self._keep([_record_to_ev(r) for r in await self.store.trials(condition, phase, status)])

    async def targets(self, disease_id: str) -> list[dict]:
        """Open Targets associations: the store's targets table if it has rows, else the fixture release.
        Both limited to releases dated before min(as_of, REFERENCE_CUTOFF); exempt from FLOOR."""
        cut = min(self.as_of, REFERENCE_CUTOFF)
        rows = await self.store._a("SELECT * FROM targets WHERE as_of_date < %s AND (disease_id = %s OR %s = '')",
                                   (cut, disease_id, disease_id or ""))
        if rows:
            latest = max(r["as_of_date"] for r in rows)
            return [{"target_id": r["target_id"], "symbol": r["symbol"],
                     "genetic_score": float(r.get("association_score") or 0),
                     "evidence_types": r.get("evidence_types") or [], "tractability": {},
                     "id": f"ot:{_iso(latest)}", "source": "opentargets", "source_date": _iso(latest),
                     "date_kind": "opentargets_release"} for r in rows if r["as_of_date"] == latest]
        from agents.analysis.retrieval import StubRetrieval

        out = await StubRetrieval(cut).targets(disease_id)
        for a in out:
            a.update(source="opentargets", date_kind="opentargets_release_fixture")
        return out

    async def drugs(self, mechanism: str | None = None, indication: str | None = None) -> list[dict]:
        return self._keep([_record_to_ev(r) for r in await self.store.drugs(mechanism, indication)])

    async def approvals(self, indication: str = "COPD") -> list[dict]:
        return self._keep([_record_to_ev(r) for r in await self.store.approvals(indication)])

    async def retractions(self, doi: str | None = None) -> list[dict]:
        """By DOI, or every retraction notice before as_of when doi is None (A6's checklist)."""
        if doi:
            return [_record_to_ev(r) for r in await self.store.retractions(doi)]
        rows = await self.store._a("SELECT doc_id, retracted_date, reason FROM retractions WHERE retracted_date < %s",
                                   (self.as_of,))
        return [{"id": r["doc_id"], "source": "retraction", "source_date": _iso(r["retracted_date"]),
                 "date_kind": "retraction_date", "title": "Retraction notice", "doc_id": r["doc_id"],
                 "body": r.get("reason") or ""} for r in rows]

    async def filings(self, company: str, form: str | None = None) -> list[dict]:
        return []


# ----------------------------------------------------------------------------- tool schemas
def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required or []}}}


_DATES = {"published_after": {"type": "string", "description": "YYYY-MM-DD, optional lower bound"},
          "published_before": {"type": "string", "description": "YYYY-MM-DD, optional upper bound"}}

TOOLS: dict[str, dict] = {
    "store_coverage": _fn(
        "store_coverage",
        "What the evidence store holds inside your date window: record counts and the earliest/latest "
        "publication date per source. Call this first to know how recent the data is.", {}),
    "search_evidence": _fn(
        "search_evidence",
        "Semantic + keyword search over COPD papers (pubmed), trial registrations (ctgov), FDA labels (fda) "
        "and curated market notes (curated). Returns passages with each record's publication date.",
        {"query": {"type": "string"},
         "sources": {"type": "array", "items": {"type": "string", "enum": ["pubmed", "ctgov", "fda", "curated", "news"]}},
         "k": {"type": "integer", "description": "max results, default 8"}, **_DATES},
        ["query"]),
    "get_document": _fn(
        "get_document", "Full record for one id (e.g. pmid:36681255, nct:NCT04701983, fda:NDA206756) with all its dates.",
        {"id": {"type": "string"}}, ["id"]),
    "list_trials": _fn(
        "list_trials",
        "COPD trials (latest registry version inside the window) with phase, status, why_stopped, and dates "
        "(first_posted, start, completion).",
        {"drug_id": {"type": "string"}, "phase": {"type": "string", "description": "e.g. PHASE3"},
         "status": {"type": "string", "description": "e.g. COMPLETED, TERMINATED, RECRUITING"}}),
    "list_drugs": _fn(
        "list_drugs",
        "COPD drugs from the market database: in_market (approved, sold) or shelved (failed / stopped), "
        "with class, pathway and targets.",
        {"status": {"type": "string", "enum": ["in_market", "shelved"]},
         "mechanism": {"type": "string", "description": "matches pathway or drug class"},
         "name": {"type": "string"}}),
    "market_stats": _fn(
        "market_stats",
        "Usage and cost stats for in-market COPD drugs: patients, annual cost, total spend, claims, "
        "insurance coverage, side effects, warnings (Medicare Part D period + publication date).",
        {"drug_id": {"type": "string", "description": "drug id (drug:roflumilast) or plain name"}}),
    "shelved_programs": _fn(
        "shelved_programs",
        "Failed or shelved COPD programs: phase reached, shelved date, reason category and detail, side effects.",
        {"reason": {"type": "string", "description": "reason category, e.g. efficacy, safety, strategic (all current rows: efficacy)"}}),
    "paper_findings": _fn(
        "paper_findings",
        "Structured results extracted from COPD papers: design, population, n, interventions, primary "
        "endpoint and result, met_primary, safety, conclusion, with a verified quote.",
        {"drug": {"type": "string"}, "met_primary": {"type": "string", "enum": ["yes", "no", "mixed", "not_reported"]},
         "design": {"type": "string"}}),
    "papers_for_drug": _fn(
        "papers_for_drug", "Papers and trials that test a drug (name fragment or drug_id).",
        {"drug": {"type": "string"}}, ["drug"]),
    "label_events": _fn(
        "label_events",
        "FDA label safety facts: adverse reactions (drug % vs placebo %), contraindications, warnings, "
        "population cautions.",
        {"drug_id": {"type": "string", "description": "drug id (drug:roflumilast), plain name, or label id (fda:NDA...)"},
         "kind": {"type": "string", "enum": ["adverse_reaction", "warning", "contraindication", "population_caution"]},
         "term": {"type": "string"}}),
    "web_search": _fn(
        "web_search",
        "Search the web (Tavily) for items inside your date window. Results are dated; undated pages are "
        "dropped in backtests. Use for things the store may not hold.",
        {"query": {"type": "string"}, "k": {"type": "integer", "description": "max results, default 5"}, **_DATES},
        ["query"]),
}

READ = ["store_coverage", "search_evidence", "get_document", "list_trials", "list_drugs", "market_stats",
        "shelved_programs", "paper_findings", "papers_for_drug", "label_events"]

# Who may call what (whiteboard, 2026-11-08). A7 (judge) has no tools. A1 write tools: db_ingest.py.
ACCESS: dict[str, list[str]] = {
    "a1_disease": ["store_coverage", "search_evidence", "get_document", "list_trials", "list_drugs",
                   "paper_findings", "papers_for_drug", "web_search"],
    "a2_mechanism": [t for t in READ if t != "label_events"],
    "a3_profile": READ,
    "a4_inactive": READ + ["web_search"],
    "a5_active": READ + ["web_search"],
    "a6_redteam": READ,
    "a7_judge": [],
}


def tools_for(agent: str, extra: list[dict] | None = None) -> list[dict]:
    """OpenAI `tools=` list for an agent (unknown agents get read-only DB tools)."""
    names = ACCESS.get(agent, READ)
    return [TOOLS[n] for n in names] + list(extra or [])


# ----------------------------------------------------------------------------- the executor
@dataclass
class ToolExecutor:
    """Runs one tool call for one agent. Every record it returns is also added to `pack`."""

    agent: str
    as_of: date
    pack: Any = None                       # EvidencePack (anything with .add(list[dict]))
    floor: date = FLOOR
    dsn: str | None = None
    web: Any = None                        # TavilySearch-like (search(q, k)); built lazily
    extra: dict[str, Callable] = field(default_factory=dict)   # name -> async fn(**args) (A1 write tools)
    log: list[dict] = field(default_factory=list)
    on_call: Callable[[dict], None] | None = None

    def __post_init__(self) -> None:
        self.allowed = set(ACCESS.get(self.agent, READ)) | set(self.extra)
        self._store = None

    @property
    def store(self):
        if self._store is None:
            self._store = store_client(self.as_of, self.dsn)
        return self._store

    # -- window
    def _bounds(self, args: dict) -> tuple[date, date]:
        lo, hi = self.floor, self.as_of
        try:
            if args.get("published_after"):
                lo = max(lo, date.fromisoformat(str(args["published_after"])[:10]))
            if args.get("published_before"):
                hi = min(hi, date.fromisoformat(str(args["published_before"])[:10]))
        except ValueError:
            pass
        return lo, hi

    def _shape(self, evs: list[dict], lo: date, hi: date) -> list[dict]:
        keep = [e for e in evs if in_window(e.get("source_date"), hi, lo)]
        if self.pack is not None:
            self.pack.add(keep)
        out = []
        for e in keep:
            d = date.fromisoformat(e["source_date"])
            item = {"id": e["id"], "published": e["source_date"], "date_kind": e.get("date_kind"),
                    "age_days": (self.as_of - d).days, "source": e["source"], "title": e.get("title")}
            for k in ("dates", "phase", "status", "why_stopped", "sponsor", "url", "doi", "source_ids"):
                if e.get(k):
                    item[k] = e[k]
            text = " … ".join(e["matched"]) if e.get("matched") else e.get("body") or ""
            item["text"] = text[:SNIPPET]
            out.append(item)
        return out

    # -- dispatch
    async def __call__(self, name: str, args: dict | str | None) -> dict:
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                return self._done(name, {}, {"error": "arguments were not valid JSON"})
        args = args or {}
        if name not in self.allowed:
            return self._done(name, args, {"error": f"tool '{name}' is not available to {self.agent}"})
        t0 = datetime.now()
        try:
            if name in self.extra:
                result = await self.extra[name](**args)
            else:
                result = await getattr(self, f"_t_{name}")(**args)
        except TypeError as e:
            result = {"error": f"bad arguments: {e}"}
        except Exception as e:  # noqa: BLE001 - the model gets the error and can try again
            result = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        return self._done(name, args, result, (datetime.now() - t0).total_seconds() * 1000)

    def _done(self, name: str, args: dict, result: dict, ms: float = 0.0) -> dict:
        items = result.get("results") or []
        entry = {"agent": self.agent, "tool": name, "args": args, "n": len(items),
                 "ids": [i.get("id") for i in items][:20],
                 "dates": [i.get("published") for i in items][:20],
                 "error": result.get("error"), "ms": round(ms)}
        self.log.append(entry)
        if self.on_call:
            self.on_call(entry)
        return result

    def _wrap(self, items: list[dict], lo: date, hi: date) -> dict:
        return {"window": {"from": lo.isoformat(), "before": hi.isoformat()}, "count": len(items), "results": items}

    # -- tools
    async def _t_store_coverage(self) -> dict:
        rows = await self.store._a(
            """SELECT source, count(*) AS n, min(source_date) AS earliest, max(source_date) AS latest
               FROM documents_as_of(%s) WHERE source_date >= %s GROUP BY source ORDER BY source""",
            (self.as_of, self.floor))
        market = await self.store._a(
            """SELECT status, count(*) AS n FROM drugs WHERE as_of_date < %s AND as_of_date >= %s GROUP BY status""",
            (self.as_of, self.floor))
        return {"window": {"from": self.floor.isoformat(), "before": self.as_of.isoformat()},
                "documents": _plain(rows), "drugs": _plain(market)}

    async def _t_search_evidence(self, query: str, sources: list[str] | None = None, k: int = 8,
                                 published_after: str | None = None, published_before: str | None = None) -> dict:
        lo, hi = self._bounds({"published_after": published_after, "published_before": published_before})
        k = max(1, min(int(k or 8), 20))
        recs = await self.store.search(query, sources=sources or None, k=k * 2)
        return self._wrap(self._shape([_record_to_ev(r) for r in recs], lo, hi)[:k], lo, hi)

    async def _t_get_document(self, id: str) -> dict:
        r = await self.store.doc(id)
        if r is None:
            return {"error": f"no record '{id}' dated before {self.as_of}"}
        ev = _record_to_ev(r)
        items = self._shape([ev], self.floor, self.as_of)
        if not items:
            return {"error": f"'{id}' is dated {ev['source_date']}, outside the window"}
        items[0]["text"] = ev["body"][:4000]
        return self._wrap(items, self.floor, self.as_of)

    async def _drug_id(self, v: str | None) -> str | None:
        """Accept drug:roflumilast, roflumilast, or a brand name; return the store's drug_id."""
        if not v or v.startswith("drug:"):
            return v
        rows = await self.store.drug_table(name=v)
        return rows[0]["drug_id"] if rows else f"drug:{v.strip().lower().replace(' ', '-')}"

    async def _t_list_trials(self, drug_id: str | None = None, phase: str | None = None,
                             status: str | None = None) -> dict:
        rows = await self.store.trial_table(await self._drug_id(drug_id), phase, status)
        evs = []
        for r in rows:
            tid = r["trial_id"]
            rid = tid if tid.startswith("nct:") else f"nct:{tid}"
            ev = _row_record("ctgov", rid, r["published_date"], r.get("title") or tid, r,
                             {"start": r.get("start_date"), "completion": r.get("completion_date")})
            ev["date_kind"] = DATE_KIND["ctgov"]
            for k in ("phase", "status", "why_stopped", "sponsor"):
                if r.get(k):
                    ev[k] = r[k]
            evs.append(ev)
        return self._wrap(self._shape(evs, self.floor, self.as_of)[:30], self.floor, self.as_of)

    async def _t_list_drugs(self, status: str | None = None, mechanism: str | None = None,
                            name: str | None = None) -> dict:
        rows = await self.store.drug_table(status, mechanism, name)
        evs = [_row_record("curated", r["drug_id"], r["as_of_date"], f"{r['name']} ({r['status']})", r,
                           source_ids=r.get("source_ids")) for r in rows]
        return self._wrap(self._shape(evs, self.floor, self.as_of), self.floor, self.as_of)

    async def _t_market_stats(self, drug_id: str | None = None) -> dict:
        rows = await self.store.market(await self._drug_id(drug_id))
        evs = [_row_record("market", f"market:{r['drug_id']}:{_iso(r['period'])}", r["published_date"],
                           f"{r['name']} market stats ({_iso(r['period'])[:4]})", r,
                           {"period": r.get("period")}, r.get("source_ids")) for r in rows]
        return self._wrap(self._shape(evs, self.floor, self.as_of), self.floor, self.as_of)

    async def _t_shelved_programs(self, reason: str | None = None) -> dict:
        rows = await self.store.shelved(reason)
        evs = [_row_record("shelved", f"shelved:{r['drug_id']}", r["published_date"],
                           f"{r['name']} shelved ({r.get('reason_category')})", r,
                           {"shelved_date": r.get("shelved_date")}, r.get("source_ids")) for r in rows]
        return self._wrap(self._shape(evs, self.floor, self.as_of), self.floor, self.as_of)

    async def _t_paper_findings(self, drug: str | None = None, met_primary: str | None = None,
                                design: str | None = None) -> dict:
        rows = await self.store.findings(drug, met_primary, design)
        evs = [_row_record("pubmed", r["doc_id"], r["published_date"], r.get("title") or r["doc_id"],
                           {k: v for k, v in r.items() if k not in ("model", "prompt_version", "title")})
               for r in rows]
        for e in evs:
            e["date_kind"] = DATE_KIND["pubmed"]
        return self._wrap(self._shape(evs, self.floor, self.as_of)[:25], self.floor, self.as_of)

    async def _t_papers_for_drug(self, drug: str) -> dict:
        rows = await self.store.papers_for_drug(drug)
        evs = []
        for r in rows:
            e = _row_record(r.get("source") or "pubmed", r["doc_id"], r["published_date"], r.get("title") or r["doc_id"], r)
            e["date_kind"] = DATE_KIND.get(r.get("source") or "pubmed", "published_date")
            evs.append(e)
        return self._wrap(self._shape(evs, self.floor, self.as_of)[:25], self.floor, self.as_of)

    async def _t_label_events(self, drug_id: str | None = None, kind: str | None = None,
                              term: str | None = None) -> dict:
        label_doc = drug_id if (drug_id or "").startswith("fda:") else None
        rows = await self.store.label_events(None if label_doc else await self._drug_id(drug_id), kind, term)
        if label_doc:
            rows = [r for r in rows if r["label_doc_id"] == label_doc]
        evs = [_row_record("label_event", f"{r['label_doc_id']}#{r['id']}", r["published_date"],
                           f"{r.get('brand')} label: {r.get('kind')} · {r.get('term')}", r) for r in rows]
        return self._wrap(self._shape(evs, self.floor, self.as_of)[:40], self.floor, self.as_of)

    async def _t_web_search(self, query: str, k: int = 5, published_after: str | None = None,
                            published_before: str | None = None) -> dict:
        lo, hi = self._bounds({"published_after": published_after, "published_before": published_before})
        if self.web is None:
            from agents.analysis.retrieval_web import TavilySearch

            self.web = TavilySearch(hi)
        docs = await self.web.search(query, k=max(1, min(int(k or 5), 10)))
        for d in docs:
            d.setdefault("date_kind", DATE_KIND["web"])
        return self._wrap(self._shape(docs, lo, hi), lo, hi)


def tool_log_rows(log: list[dict]) -> list[list]:
    """Rows for the workbook 'Tool calls' sheet."""
    return [[e["agent"], e["tool"], json.dumps(e["args"], default=str)[:300], e["n"],
             ", ".join(filter(None, e["ids"]))[:400], ", ".join(filter(None, e["dates"]))[:300],
             e.get("error") or "", e["ms"]] for e in log]
