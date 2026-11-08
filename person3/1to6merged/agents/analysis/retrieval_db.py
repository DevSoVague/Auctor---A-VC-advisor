"""Database retrieval: RAG (Milvus) for text + read-only SQL (Postgres) for tables, same interface as the others.

    unstructured text   → rag_search()   Milvus finds small CHILD chunks, we return their larger PARENT chunk
                                         (from Postgres) with the doc id + date, so the agent can cite it
    original documents  → get_document() Postgres `documents` (full text; `file_path` points at the original file)
    stats / tables      → query_table()  Postgres tables (drugs, market_stats, shelved_programs, trials, ...)

Two backends behind the same classes:
    DB_BACKEND=sample   (default) small in-memory sample rows, no Docker needed  → tests/fixtures/db/sample.json
    DB_BACKEND=live     Postgres + Milvus. Needs POSTGRES_URL and MILVUS_URI (+ EMBED_MODEL for the query embedding)

All real table / column / field names live in db_mapping.yaml. When the schema arrives, edit that file.

Safety (the agents never write SQL):
  - only tables and columns listed in db_mapping.yaml can be read; values are bound parameters
  - the Postgres session is read-only with a statement timeout
  - every query adds  date < as_of  (rows on/after the cutoff are invisible), and results are re-checked here
  - retracted documents are excluded from RAG results
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import yaml

from agents.analysis.context import FIXTURES
from agents.analysis.retrieval import before

MAPPING_PATH = Path(os.getenv("DB_MAPPING", Path(__file__).with_name("db_mapping.yaml")))
SAMPLE_PATH = FIXTURES / "db" / "sample.json"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatabaseError(RuntimeError):
    pass


def load_mapping(path: Path | None = None) -> dict:
    return yaml.safe_load((path or MAPPING_PATH).read_text())


def _iso(v: Any) -> str | None:
    return None if v is None else str(v)[:10]


def _ymd(d: date) -> int:
    return int(d.strftime("%Y%m%d"))


# ---------------------------------------------------------------------------- table backends
class TableBackend(Protocol):
    async def select(self, logical: str, *, where: dict | None = None, ilike: dict | None = None,
                     before: date | None = None, columns: list[str] | None = None, limit: int = 50) -> list[dict]: ...


def _check(mapping: dict, logical: str, columns: list[str] | None, where: dict | None, ilike: dict | None) -> dict:
    t = (mapping.get("tables") or {}).get(logical)
    if t is None:
        raise DatabaseError(f"unknown table '{logical}'. Allowed: {sorted(mapping.get('tables') or {})}")
    allowed = set(t["columns"])
    for c in list(columns or []) + list(where or {}) + list(ilike or {}):
        if c not in allowed:
            raise DatabaseError(f"column '{c}' is not readable on '{logical}'. Allowed: {sorted(allowed)}")
    for ident in [t["table"], t["date"], *t["columns"]]:
        if not _IDENT.match(ident):
            raise DatabaseError(f"bad identifier in db_mapping.yaml: {ident!r}")
    return t


def build_select(mapping: dict, logical: str, *, where: dict | None = None, ilike: dict | None = None,
                 before: date | None = None, columns: list[str] | None = None, limit: int = 50) -> tuple[str, list]:
    """Parameterised SELECT built only from names in the mapping. Pure function (unit-testable without a database)."""
    t = _check(mapping, logical, columns, where, ilike)
    cols = columns or t["columns"]
    sql, params, conds = f"SELECT {', '.join(cols)} FROM {t['table']}", [], []
    for c, v in (where or {}).items():
        if isinstance(v, (list, tuple, set)):
            conds.append(f"{c} = ANY(%s)")
            params.append(list(v))
        else:
            conds.append(f"{c} = %s")
            params.append(v)
    for c, v in (ilike or {}).items():
        conds.append(f"{c}::text ILIKE %s")
        params.append(f"%{v}%")
    if before is not None:
        conds.append(f"{t['date']} < %s")
        params.append(before)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    return sql + f" ORDER BY {t['date']} DESC LIMIT %s", params + [max(1, min(int(limit), 200))]


class PostgresTables:
    def __init__(self, url: str, mapping: dict) -> None:
        self.url, self.mapping = url, mapping

    def _run(self, sql: str, params: list) -> list[dict]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise DatabaseError("psycopg is not installed. Run: pip install 'psycopg[binary]'") from e
        try:
            with psycopg.connect(self.url, autocommit=True, connect_timeout=5, row_factory=dict_row,
                                 options="-c default_transaction_read_only=on -c statement_timeout=15000") as c:
                return list(c.execute(sql, params).fetchall())
        except psycopg.OperationalError as e:
            raise DatabaseError(f"Postgres is not reachable at POSTGRES_URL ({e}). Is the docker container up?") from e

    async def select(self, logical, *, where=None, ilike=None, before=None, columns=None, limit=50):
        sql, params = build_select(self.mapping, logical, where=where, ilike=ilike, before=before,
                                   columns=columns, limit=limit)
        return await asyncio.to_thread(self._run, sql, params)


class SampleTables:
    """Same behaviour as PostgresTables over the in-memory sample rows."""

    def __init__(self, data: dict, mapping: dict) -> None:
        self.data, self.mapping = data, mapping

    async def select(self, logical, *, where=None, ilike=None, before=None, columns=None, limit=50):
        t = _check(self.mapping, logical, columns, where, ilike)
        out = []
        for r in self.data.get(logical, []):
            if before is not None and not (_iso(r.get(t["date"])) and _iso(r[t["date"]]) < before.isoformat()):
                continue
            if any((r.get(c) not in v) if isinstance(v, (list, tuple, set)) else r.get(c) != v
                   for c, v in (where or {}).items()):
                continue
            if any(str(v).lower() not in str(r.get(c, "")).lower() for c, v in (ilike or {}).items()):
                continue
            out.append(r)
        out.sort(key=lambda r: _iso(r.get(t["date"])) or "", reverse=True)
        cols = columns or t["columns"]
        return [{c: r.get(c) for c in cols} for r in out[: max(1, min(int(limit), 200))]]


# ---------------------------------------------------------------------------- vector backends
class VectorBackend(Protocol):
    async def search(self, query: str, k: int, before: date, sources: list[str] | None = None) -> list[dict]:
        """Child-chunk hits: {chunk_id, text, parent_id, doc_id, source, doc_type, source_date, score}."""


class SampleVector:
    def __init__(self, data: dict) -> None:
        self.children = data.get("child_chunks", [])

    async def search(self, query, k, before, sources=None):
        terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
        hits = []
        for c in self.children:
            if _iso(c["published_date"]) >= before.isoformat() or (sources and c["source"] not in sources):
                continue
            score = sum(t in c["text"].lower() for t in terms)
            if score:
                hits.append({**c, "source_date": _iso(c["published_date"]), "score": float(score)})
        hits.sort(key=lambda h: (-h["score"], h["source_date"]))
        return hits[:k]


class Embedder:
    """Query embeddings from any OpenAI-compatible endpoint (default: the Nebius endpoint in .env).
    Use the SAME model the ingest job used to embed the chunks."""

    def __init__(self) -> None:
        self.model = os.getenv("EMBED_MODEL", "")
        self.key = (os.getenv("EMBED_API_KEY") or os.getenv("NEBIUS_API_KEY") or "").strip()
        self.base = os.getenv("EMBED_BASE_URL") or os.getenv("NEBIUS_BASE_URL") or ""
        self._client = None

    async def embed(self, text: str) -> list[float]:
        if not self.model:
            raise DatabaseError("EMBED_MODEL is not set (the embedding model the ingest job used). Add it to frontier-engine/.env")
        if not self.key or not self.base:
            raise DatabaseError("Set EMBED_API_KEY and EMBED_BASE_URL (or NEBIUS_API_KEY / NEBIUS_BASE_URL) for query embeddings")
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self.key, base_url=self.base, timeout=60, max_retries=1)
        r = await self._client.embeddings.create(model=self.model, input=text[:8000])
        return list(r.data[0].embedding)


class MilvusVector:
    def __init__(self, uri: str, mapping: dict, embedder: Embedder | None = None) -> None:
        self.uri, self.m, self.embedder, self._client = uri, mapping["milvus"], embedder or Embedder(), None

    def _cli(self):
        if self._client is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as e:
                raise DatabaseError("pymilvus is not installed. Run: pip install pymilvus") from e
            try:
                self._client = MilvusClient(uri=self.uri, token=os.getenv("MILVUS_TOKEN", ""))
            except Exception as e:  # noqa: BLE001 - pymilvus raises its own error types
                raise DatabaseError(f"Milvus is not reachable at MILVUS_URI ({e}). Is the docker container up?") from e
        return self._client

    async def search(self, query, k, before, sources=None):
        f = self.m["fields"]
        vec = await self.embedder.embed(query)
        flt = f"{f['date']} < {_ymd(before)}"  # the date cutoff is applied inside Milvus
        if sources:
            flt += f" and {f['source']} in {json.dumps(sources)}"
        out_fields = [f[x] for x in ("chunk_id", "text", "parent_id", "doc_id", "source", "doc_type", "date")]

        def go():
            return self._cli().search(collection_name=self.m["collection"], data=[vec], limit=k, filter=flt,
                                      output_fields=out_fields, anns_field=self.m.get("vector_field", "embedding"))

        res = await asyncio.to_thread(go)
        hits = []
        for h in (res[0] if res else []):
            e = h.get("entity", h)
            d = str(e[f["date"]])
            hits.append({"chunk_id": e[f["chunk_id"]], "text": e[f["text"]], "parent_id": e[f["parent_id"]],
                         "doc_id": e[f["doc_id"]], "source": e[f["source"]], "doc_type": e.get(f["doc_type"]),
                         "source_date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else _iso(d),
                         "score": float(h.get("distance", 0))})
        return hits


# ---------------------------------------------------------------------------- the retrieval client
class DatabaseRetrieval:
    """Retrieval interface (search, doc, trials, targets, drugs, approvals, retractions) over the database."""

    def __init__(self, as_of: date, vector: VectorBackend, tables: TableBackend, label: str = "db") -> None:
        self.as_of, self.vector, self.tables, self.label = as_of, vector, tables, label
        self._retracted: set[str] | None = None

    # -- helpers
    async def _retracted_ids(self) -> set[str]:
        if self._retracted is None:
            rows = await self.tables.select("retractions", before=self.as_of, columns=["doc_id"], limit=200)
            self._retracted = {r["doc_id"] for r in rows}
        return self._retracted

    # -- RAG: child hit → parent text
    async def search(self, q: str, sources: list[str] | None = None, k: int = 10) -> list[dict]:
        hits = await self.vector.search(q, k=max(k * 3, 10), before=self.as_of, sources=sources)
        retracted = await self._retracted_ids()
        hits = [h for h in hits if h["doc_id"] not in retracted and before(h, self.as_of)]
        order: list[str] = []
        matched: dict[str, list[str]] = {}
        for h in hits:  # best child first; one result per parent
            if h["parent_id"] not in matched:
                order.append(h["parent_id"])
            matched.setdefault(h["parent_id"], []).append(h["text"])
        order = order[:k]
        if not order:
            return []
        parents = {p["parent_id"]: p for p in await self.tables.select(
            "parent_chunks", where={"parent_id": order}, before=self.as_of, limit=len(order))}
        doc_ids = sorted({h["doc_id"] for h in hits if h["parent_id"] in order})
        docs = {d["doc_id"]: d for d in await self.tables.select(
            "documents", where={"doc_id": doc_ids}, before=self.as_of,
            columns=["doc_id", "title", "url", "source", "doc_type", "published_date"], limit=len(doc_ids))}
        first = {h["parent_id"]: h for h in hits}
        out = []
        for pid in order:
            h, p = first[pid], parents.get(pid)
            d = docs.get(h["doc_id"], {})
            out.append({"id": pid, "doc_id": h["doc_id"], "source": h["source"],
                        "source_date": _iso(p["published_date"]) if p else h["source_date"],
                        "title": d.get("title") or h["doc_id"], "url": d.get("url"),
                        "body": (p["text"] if p else h["text"])[:2500],
                        "tags": [t for t in (self.label, h.get("doc_type")) if t], "matched": matched[pid][:3]})
        return out

    async def doc(self, doc_id: str) -> dict | None:
        if doc_id.startswith("chunk:"):
            rows = await self.tables.select("parent_chunks", where={"parent_id": doc_id}, before=self.as_of, limit=1)
            return None if not rows else {"id": doc_id, "doc_id": rows[0]["doc_id"], "source": self.label,
                                          "source_date": _iso(rows[0]["published_date"]), "title": doc_id,
                                          "body": rows[0]["text"]}
        rows = await self.tables.select("documents", where={"doc_id": doc_id}, before=self.as_of, limit=1)
        if not rows or doc_id in await self._retracted_ids():
            return None
        r = rows[0]
        return {"id": r["doc_id"], "source": r["source"], "source_date": _iso(r["published_date"]), "title": r["title"],
                "url": r.get("url"), "body": (r.get("full_text") or "")[:6000], "file_path": r.get("file_path"),
                "tags": [r.get("doc_type")] if r.get("doc_type") else []}

    # -- tables, mapped to the shapes the agents already use
    async def drugs(self, mechanism: str | None = None, indication: str | None = None) -> list[dict]:
        rows = await self.tables.select("drugs", ilike={"pathway": mechanism} if mechanism else None,
                                        before=self.as_of, limit=100)
        return [{"id": r["drug_id"], "source": self.label, "source_date": _iso(r["as_of_date"]),
                 "title": f"{r['name']} ({r['status']})", "name": r["name"], "mechanism": r.get("pathway") or "",
                 "target_symbols": r.get("targets") or [], "indications": ["COPD"], "status": r["status"],
                 "body": f"{r['name']}: {r.get('pathway') or r.get('drug_class')}. Status: {r['status']}."}
                for r in rows]

    async def trials(self, condition: str = "COPD", phase: str | None = None, status: str | None = None) -> list[dict]:
        where = {"status": status} if status else None
        rows = await self.tables.select("trials", where=where, ilike={"phase": phase} if phase else None,
                                        before=self.as_of, limit=100)
        return [{"id": r["trial_id"], "source": self.label, "source_date": _iso(r["published_date"]),
                 "title": r["title"], "phase": r.get("phase"), "intervention": r.get("drug_id"),
                 "primary_endpoint": r.get("primary_outcome"), "primary_completion": _iso(r.get("completion_date")),
                 "status": r.get("status"), "why_stopped": r.get("why_stopped"), "enrollment": r.get("enrollment")}
                for r in rows]

    async def targets(self, disease_id: str) -> list[dict]:
        rows = await self.tables.select("targets", where={"disease_id": disease_id}, before=self.as_of, limit=200)
        return [{"id": f"db:target:{r['target_id']}", "source": self.label, "source_date": _iso(r["as_of_date"]),
                 "target_id": r["target_id"], "symbol": r.get("symbol"), "genetic_score": r.get("association_score"),
                 "evidence_types": r.get("evidence_types") or [], "known_drug_max_phase_in_disease": None,
                 "tractability": {}, "safety": []} for r in rows]

    async def approvals(self, indication: str = "COPD") -> list[dict]:
        rows = await self.tables.select("approvals", before=self.as_of, limit=100)
        return [{"id": f"db:approval:{r['id']}", "source": self.label, "source_date": _iso(r["approved_date"]),
                 "title": f"Approval ({r.get('agency')}): {r.get('drug_id')}", "drug_id": r.get("drug_id"),
                 "body": f"{r.get('agency')} approved {r.get('drug_id')} for {r.get('indication')}."}
                for r in rows if indication.lower() in (r.get("indication") or "").lower() or indication == "COPD"]

    async def retractions(self, doi: str | None = None) -> list[dict]:
        rows = await self.tables.select("retractions", before=self.as_of, limit=200)
        return [{"id": f"db:retraction:{r['doc_id']}", "source": self.label, "source_date": _iso(r["retracted_date"]),
                 "title": "Retraction notice", "doc_id": r["doc_id"], "doi": None,
                 "body": f"Retracted: {r['doc_id']} ({r.get('reason')})."} for r in rows]

    # -- generic table query (used by the query_table tool)
    async def query_table(self, table: str, where: dict | None = None, ilike: dict | None = None,
                          columns: list[str] | None = None, limit: int = 20) -> list[dict]:
        return await self.tables.select(table, where=where, ilike=ilike, columns=columns,
                                        before=self.as_of, limit=limit)


# ---------------------------------------------------------------------------- factory
_LIVE: dict[tuple, tuple] = {}


def make_db(as_of: date, backend: str | None = None) -> DatabaseRetrieval:
    """DB_BACKEND=sample (default) or live. Live needs POSTGRES_URL and MILVUS_URI."""
    backend = (backend or os.getenv("DB_BACKEND", "sample")).strip().lower()
    mapping = load_mapping()
    if backend == "sample":
        data = json.loads(SAMPLE_PATH.read_text())
        return DatabaseRetrieval(as_of, SampleVector(data), SampleTables(data, mapping), label="db-sample")
    if backend != "live":
        raise DatabaseError(f"DB_BACKEND must be 'sample' or 'live', got '{backend}'")
    pg, mv = os.getenv("POSTGRES_URL", "").strip(), os.getenv("MILVUS_URI", "").strip()
    missing = [n for n, v in (("POSTGRES_URL", pg), ("MILVUS_URI", mv)) if not v]
    if missing:
        raise DatabaseError(f"DB_BACKEND=live but {', '.join(missing)} not set. Add to frontier-engine/.env "
                            "(e.g. POSTGRES_URL=postgresql://user:pass@localhost:5432/copd, MILVUS_URI=http://localhost:19530)")
    if (pg, mv) not in _LIVE:  # reuse connections between nodes
        _LIVE[(pg, mv)] = (MilvusVector(mv, mapping), PostgresTables(pg, mapping))
    vec, tab = _LIVE[(pg, mv)]
    return DatabaseRetrieval(as_of, vec, tab, label="db")
