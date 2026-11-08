"""StoreClient: the one object agents use to read the evidence store.

Bound to one as_of date at creation; nothing dated on or after it is ever returned
(documents, chunks, and every lookup table). For versioned records (ctgov trials),
the latest version before as_of is the one returned.

Three ways to read:
  1. Lookups (tabular, no RAG, no network):  drug_table, market, shelved, trial_table,
     findings, label_events, labels, papers_for_drug, trials_for_drug ...
  2. RAG (passages):  rag(q)  -> ChunkHit list  (hybrid: pgvector cosine + Postgres full text, RRF)
  3. Team RetrievalClient protocol (Record-shaped): search, doc, trials, targets, drugs,
     approvals, filings, retractions -- drop-in for frontier.core.retrieval.StubRetrievalClient.

Async methods run the (sync) psycopg calls in a worker thread.
"""

from __future__ import annotations

import asyncio
import re
import threading
from datetime import date
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from evstore.db import connect

# The team's Source plus the two kinds person 1 also stores.
Source = Literal["pubmed", "ctgov", "fda", "opentargets", "chembl", "edgar", "retraction", "patent",
                 "curated", "news"]

RRF_K = 60
W_VECTOR, W_TEXT = 1.0, 0.6   # semantic ranking leads; full text breaks ties and rescues exact names
MAX_DF = 0.2                  # query words in more than 20% of chunks are too common to rank on
_STOP = set("the a an and or of for in on to with by is are was were be what which who how does do did "
            "copd patients patient drug drugs trial trials study effect effects".split())


class Record(BaseModel):
    """Same shape as frontier.core.retrieval.Record (documents row)."""

    id: str
    source: Source
    source_date: date
    title: str | None = None
    body: str | None = None
    url: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class ChunkHit(BaseModel):
    chunk_id: str
    doc_id: str                # cite this
    source: str
    section: str | None
    text: str
    source_date: date          # the chunk's published date
    title: str | None
    url: str | None
    score: float               # RRF score (higher is better)
    vector_rank: int | None
    text_rank: int | None


def _terms(q: str) -> list[str]:
    words = [w.strip("-") for w in re.findall(r"[a-z0-9][a-z0-9-]{1,}", q.lower())]
    return list(dict.fromkeys(w for w in words if len(w) > 1 and w not in _STOP))


class StoreClient:
    def __init__(self, as_of: date | str, dsn: str | None = None, use_vectors: bool = True):
        self.as_of = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        self.use_vectors = use_vectors
        self._conn = connect(dsn, autocommit=True)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ plumbing
    def _rows(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    async def _a(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        return await asyncio.to_thread(self._rows, sql, params)

    @staticmethod
    def _record(r: dict) -> Record:
        fields = dict(r.get("fields") or {})
        fields["version_label"] = r.get("version_label")
        if r.get("full_text"):
            fields["full_text"] = r["full_text"]
        return Record(id=r["id"], source=r["source"], source_date=r["source_date"], title=r.get("title"),
                      body=r.get("body"), url=r.get("url"), fields=fields)

    # ------------------------------------------------------------------ RAG
    def _selective_query(self, q: str) -> list[str]:
        """The query words rare enough to discriminate (a cheap IDF). OR-ed together in SQL."""
        terms = _terms(q)
        if not terms:
            return []
        df = self._rows(
            """SELECT t, (SELECT count(*) FROM chunks WHERE tsv @@ term_query(t)) AS n,
                      (SELECT count(*) FROM chunks) AS total
               FROM unnest(%s::text[]) t""", (terms,))
        keep = [r["t"] for r in df if r["n"] and r["n"] <= MAX_DF * r["total"]]
        if not keep:  # every word is common: rank on the two rarest that occur at all
            keep = [r["t"] for r in sorted((r for r in df if r["n"]), key=lambda r: r["n"])[:2]]
        return keep

    def rag_sync(self, q: str, k: int = 8, sources: list[str] | None = None,
                 doc_ids: list[str] | None = None, query_vector: np.ndarray | None = None,
                 per_doc: int = 2) -> list[ChunkHit]:
        """Passages for q, best first, at most per_doc passages from one document.
        One query-embedding call to Nebius unless use_vectors=False."""
        pool = max(200, k * 20)
        filt, params = "", {"d": self.as_of, "pool": pool}
        if sources:
            filt += " AND c.source = ANY(%(sources)s)"
            params["sources"] = sources
        if doc_ids:
            filt += " AND c.doc_id = ANY(%(doc_ids)s)"
            params["doc_ids"] = doc_ids

        ranks: dict[str, dict[str, int]] = {}
        if self.use_vectors:
            if query_vector is None:
                from evstore.embed import embed_query
                query_vector = asyncio.run(embed_query(q)) if not _in_loop() else _run_in_thread(embed_query(q))
            params["qv"] = query_vector
            for i, r in enumerate(self._rows(
                f"""SELECT c.chunk_id FROM chunks_as_of(%(d)s) c
                    WHERE c.embedding IS NOT NULL {filt}
                    ORDER BY c.embedding <=> %(qv)s LIMIT %(pool)s""", params)):
                ranks.setdefault(r["chunk_id"], {})["v"] = i + 1
        tsq = self._selective_query(q)
        if tsq:
            params["tsq"] = tsq
            for i, r in enumerate(self._rows(
                f"""SELECT c.chunk_id FROM chunks_as_of(%(d)s) c,
                           any_terms(%(tsq)s::text[]) query
                    WHERE c.tsv @@ query {filt}
                    ORDER BY ts_rank_cd(c.tsv, query, 1) DESC LIMIT %(pool)s""", params)):
                ranks.setdefault(r["chunk_id"], {})["t"] = i + 1
        if not ranks:
            return []
        ranked = sorted(
            ((sum((W_VECTOR if kind == "v" else W_TEXT) / (RRF_K + r) for kind, r in rk.items()), cid, rk) for cid, rk in ranks.items()),
            reverse=True)
        rows = {r["chunk_id"]: r for r in self._rows(
            """SELECT c.chunk_id, c.doc_id, c.source, c.section, c.text, c.published_date, d.title, d.url
               FROM chunks c JOIN documents d ON d.id = c.doc_id AND d.source_date = c.doc_version
               WHERE c.chunk_id = ANY(%s)""", ([s[1] for s in ranked],))}
        scored, used = [], {}
        for item in ranked:
            did = rows[item[1]]["doc_id"]
            if used.get(did, 0) >= per_doc:
                continue
            used[did] = used.get(did, 0) + 1
            scored.append(item)
            if len(scored) == k:
                break
        return [ChunkHit(chunk_id=cid, doc_id=rows[cid]["doc_id"], source=rows[cid]["source"],
                         section=rows[cid]["section"], text=rows[cid]["text"],
                         source_date=rows[cid]["published_date"], title=rows[cid]["title"],
                         url=rows[cid]["url"], score=round(score, 5),
                         vector_rank=rk.get("v"), text_rank=rk.get("t"))
                for score, cid, rk in scored]

    async def rag(self, q: str, k: int = 8, sources: list[str] | None = None,
                  doc_ids: list[str] | None = None, per_doc: int = 2) -> list[ChunkHit]:
        qv = None
        if self.use_vectors:
            from evstore.embed import embed_query
            qv = await embed_query(q)
        return await asyncio.to_thread(self.rag_sync, q, k, sources, doc_ids, qv, per_doc)

    # ------------------------------------------------------------------ team RetrievalClient protocol
    async def search(self, q: str, sources: list[str] | None = None, k: int = 10) -> list[Record]:
        """Documents ranked by their best passages (RAG), each with fields.matched_chunks."""
        hits = await self.rag(q, k=k * 3, sources=sources, per_doc=3)
        order: list[str] = []
        by_doc: dict[str, list[ChunkHit]] = {}
        for h in hits:
            if h.doc_id not in by_doc:
                order.append(h.doc_id)
            by_doc.setdefault(h.doc_id, []).append(h)
        order = order[:k]
        if not order:
            return []
        docs = {r["id"]: r for r in await self._a(
            "SELECT * FROM documents_as_of(%s) WHERE id = ANY(%s)", (self.as_of, order))}
        out = []
        for did in order:
            rec = self._record(docs[did])
            rec.fields.pop("full_text", None)  # search returns passages, not whole papers
            rec.fields["matched_chunks"] = [
                {"chunk_id": h.chunk_id, "section": h.section, "text": h.text, "score": h.score}
                for h in by_doc[did][:3]]
            out.append(rec)
        return out

    async def doc(self, id: str) -> Record | None:
        rows = await self._a("SELECT * FROM documents_as_of(%s) WHERE id = %s", (self.as_of, id))
        return self._record(rows[0]) if rows else None

    async def trials(self, condition: str = "COPD", phase: str | None = None,
                     status: str | None = None) -> list[Record]:
        """ctgov documents (latest version before as_of). condition matches conditions or title;
        'COPD' also matches 'Chronic Obstructive'."""
        terms = [condition] + (["chronic obstructive"] if condition.strip().lower() == "copd" else [])
        rows = await self._a(
            """SELECT * FROM documents_as_of(%(d)s) WHERE source = 'ctgov'
               AND (EXISTS (SELECT 1 FROM unnest(%(terms)s::text[]) t
                            WHERE fields->>'conditions' ILIKE '%%' || t || '%%' OR title ILIKE '%%' || t || '%%'))
               AND (%(phase)s::text IS NULL OR fields->>'phase' ILIKE '%%' || %(phase)s || '%%')
               AND (%(status)s::text IS NULL OR fields->>'status' = %(status)s)
               ORDER BY source_date DESC""",
            {"d": self.as_of, "terms": terms, "phase": phase, "status": status})
        return [self._record(r) for r in rows]

    async def targets(self, disease_id: str) -> list[Record]:
        return []  # no Open Targets data in the store yet (targets table is empty)

    async def drugs(self, mechanism: str | None = None, indication: str | None = None) -> list[Record]:
        """Curated drugs as Records (source 'curated', id = drug_id), with market/shelved facts in fields."""
        rows = await self.drug_table(mechanism=mechanism)
        out = []
        for r in rows:
            if indication and indication.lower() not in ("copd", "chronic obstructive pulmonary disease"):
                continue  # every curated drug in this store is a COPD drug
            out.append(Record(id=r["drug_id"], source="curated", source_date=r["as_of_date"], title=r["name"],
                              body=f"{r['name']} ({r['drug_class'] or '?'}): {r['pathway'] or ''}. "
                                   f"Status in COPD: {r['status']}.",
                              fields={k: v for k, v in r.items() if k not in ("name",)}))
        return out

    async def approvals(self, indication: str = "COPD") -> list[Record]:
        """FDA label documents whose indication section matches."""
        terms = [indication] + (["chronic obstructive"] if indication.strip().lower() == "copd" else [])
        rows = await self._a(
            """SELECT d.* FROM documents_as_of(%(d)s) d WHERE d.source = 'fda'
               AND EXISTS (SELECT 1 FROM unnest(%(terms)s::text[]) t
                           WHERE d.fields->'sections'->>'indications_and_usage' ILIKE '%%' || t || '%%')""",
            {"d": self.as_of, "terms": terms})
        out = []
        for r in rows:
            rec = self._record(r)
            rec.fields["indication"] = (r["fields"].get("sections") or {}).get("indications_and_usage")
            out.append(rec)
        return out

    async def filings(self, company: str, form: str | None = None) -> list[Record]:
        return []  # no EDGAR data in the store yet

    async def retractions(self, doi: str) -> list[Record]:
        rows = await self._a("SELECT doc_id, retracted_date, reason FROM retractions "
                             "WHERE doc_id = %s AND retracted_date < %s", (doi, self.as_of))
        return [Record(id=r["doc_id"], source="retraction", source_date=r["retracted_date"],
                       body=r["reason"]) for r in rows]

    # ------------------------------------------------------------------ lookups (tabular)
    async def drug_table(self, status: str | None = None, mechanism: str | None = None,
                         name: str | None = None) -> list[dict]:
        """drugs: status in_market | shelved; mechanism matches pathway/class; name matches name/brand."""
        return await self._a(
            """SELECT * FROM drugs WHERE as_of_date < %(d)s
               AND (%(status)s::text IS NULL OR status = %(status)s)
               AND (%(mech)s::text IS NULL OR pathway ILIKE '%%' || %(mech)s || '%%'
                    OR drug_class ILIKE '%%' || %(mech)s || '%%')
               AND (%(name)s::text IS NULL OR name ILIKE '%%' || %(name)s || '%%'
                    OR array_to_string(brand_names, ' ') ILIKE '%%' || %(name)s || '%%')
               ORDER BY status, name""",
            {"d": self.as_of, "status": status, "mech": mechanism, "name": name})

    async def market(self, drug_id: str | None = None) -> list[dict]:
        """market_stats joined to drug name and label Part D numbers."""
        return await self._a(
            """SELECT d.name, d.drug_class, m.*, l.brand, l.part_d_year, l.part_d_spend_per_beneficiary_usd
               FROM market_stats m JOIN drugs d USING (drug_id)
               LEFT JOIN labels l ON l.drug_id = m.drug_id AND l.published_date < %(d)s
               WHERE m.published_date < %(d)s AND (%(id)s::text IS NULL OR m.drug_id = %(id)s)
               ORDER BY m.total_spend_usd DESC NULLS LAST""",
            {"d": self.as_of, "id": drug_id})

    async def shelved(self, reason: str | None = None) -> list[dict]:
        """Failed / shelved COPD programs (only those reported before as_of)."""
        return await self._a(
            """SELECT d.name, d.pathway, s.* FROM shelved_programs s JOIN drugs d USING (drug_id)
               WHERE s.published_date < %(d)s AND (%(r)s::text IS NULL OR s.reason_category = %(r)s)
               ORDER BY s.shelved_date""",
            {"d": self.as_of, "r": reason})

    async def trial_table(self, drug_id: str | None = None, phase: str | None = None,
                          status: str | None = None) -> list[dict]:
        """trials, latest version before as_of. phase like 'PHASE3'; status like 'COMPLETED'."""
        return await self._a(
            """SELECT t.*, d.name AS drug_name FROM trials_as_of(%(d)s) t LEFT JOIN drugs d USING (drug_id)
               WHERE (%(id)s::text IS NULL OR t.drug_id = %(id)s)
                 AND (%(p)s::text IS NULL OR t.phase ILIKE '%%' || %(p)s || '%%')
                 AND (%(s)s::text IS NULL OR t.status = %(s)s)
               ORDER BY t.published_date DESC""",
            {"d": self.as_of, "id": drug_id, "p": phase, "s": status})

    async def findings(self, drug: str | None = None, met_primary: str | None = None,
                       design: str | None = None) -> list[dict]:
        """paper_findings (one row per paper). drug matches interventions (any name fragment)."""
        return await self._a(
            """SELECT f.*, p.title, p.journal, p.url FROM paper_findings f JOIN publications p USING (doc_id)
               WHERE f.published_date < %(d)s
                 AND (%(drug)s::text IS NULL OR EXISTS (SELECT 1 FROM unnest(f.interventions) i
                                                        WHERE i ILIKE '%%' || %(drug)s || '%%'))
                 AND (%(m)s::text IS NULL OR f.met_primary = %(m)s)
                 AND (%(des)s::text IS NULL OR f.study_design = %(des)s)
               ORDER BY f.published_date""",
            {"d": self.as_of, "drug": drug, "m": met_primary, "des": design})

    async def papers_for_drug(self, drug: str) -> list[dict]:
        """Documents (papers and trials) that test a drug, by name fragment or drug_id."""
        return await self._a(
            """SELECT dd.*, v.title, v.source FROM doc_drugs dd
               JOIN documents_as_of(%(d)s) v ON v.id = dd.doc_id
               WHERE dd.published_date < %(d)s AND (dd.drug_id = %(q)s OR dd.drug_name ILIKE '%%' || %(q)s || '%%')
               ORDER BY dd.published_date""",
            {"d": self.as_of, "q": drug})

    async def labels(self, drug_id: str | None = None) -> list[dict]:
        return await self._a(
            """SELECT * FROM labels WHERE published_date < %(d)s
               AND (%(id)s::text IS NULL OR drug_id = %(id)s)""",
            {"d": self.as_of, "id": drug_id})

    async def label_events(self, drug_id: str | None = None, kind: str | None = None,
                           term: str | None = None) -> list[dict]:
        """Side effects / contraindications / warnings / population cautions from FDA labels."""
        return await self._a(
            """SELECT e.*, l.brand FROM label_events e JOIN labels l USING (label_doc_id)
               WHERE e.published_date < %(d)s
                 AND (%(id)s::text IS NULL OR e.drug_id = %(id)s)
                 AND (%(k)s::text IS NULL OR e.kind = %(k)s)
                 AND (%(t)s::text IS NULL OR e.term ILIKE '%%' || %(t)s || '%%')
               ORDER BY e.drug_id, e.kind, e.frequency_pct DESC NULLS LAST""",
            {"d": self.as_of, "id": drug_id, "k": kind, "t": term})

    async def trial_links(self, doc_id: str | None = None, trial_id: str | None = None) -> list[dict]:
        return await self._a(
            """SELECT * FROM trial_links WHERE published_date < %(d)s
               AND (%(doc)s::text IS NULL OR doc_id = %(doc)s)
               AND (%(t)s::text IS NULL OR trial_id = %(t)s)""",
            {"d": self.as_of, "doc": doc_id, "t": trial_id})

    async def counts(self) -> dict[str, int]:
        """What this as_of can see."""
        r = await self._a(
            """SELECT (SELECT count(*) FROM documents_as_of(%(d)s)) AS documents,
                      (SELECT count(*) FROM chunks_as_of(%(d)s)) AS chunks,
                      (SELECT count(*) FROM paper_findings WHERE published_date < %(d)s) AS paper_findings,
                      (SELECT count(*) FROM trials_as_of(%(d)s)) AS trials,
                      (SELECT count(*) FROM drugs WHERE as_of_date < %(d)s) AS drugs,
                      (SELECT count(*) FROM shelved_programs WHERE published_date < %(d)s) AS shelved,
                      (SELECT count(*) FROM labels WHERE published_date < %(d)s) AS labels,
                      (SELECT count(*) FROM label_events WHERE published_date < %(d)s) AS label_events""",
            {"d": self.as_of})
        return r[0]


def _in_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _run_in_thread(coro):
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("v", asyncio.run(coro)))
    t.start()
    t.join()
    return box["v"]
