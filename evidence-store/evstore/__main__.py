"""CLI.

    python -m evstore build            # schema + load + chunk + deterministic tables (free)
    python -m evstore embed            # embed missing chunks (Nebius, cached)
    python -m evstore extract          # LLM tables: paper_findings, label_events (Nebius, cached)
    python -m evstore all              # build + embed + extract
    python -m evstore cache-export     # paid tables -> data/cache/*.tsv (share with teammates)
    python -m evstore cache-import     # data/cache/*.tsv -> paid tables (no Nebius calls needed after)
    python -m evstore status [--as-of 2025-01-01]
    python -m evstore ask "question" [--as-of 2025-01-01] [--k 6] [--no-vectors]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from evstore import chunk, db, embed, extract, load
from evstore.config import ROOT, STORE_DIR


def cmd_build(conn) -> None:
    print("schema   ", db.apply_schema(conn))
    print("load     ", load.load(conn, STORE_DIR))
    print("chunks   ", chunk.build_chunks(conn))
    print("tables   ", extract.build_deterministic(conn))


def cmd_embed(conn) -> None:
    print("embed    ", embed.embed_missing(conn))


def cmd_extract(conn) -> None:
    print("papers   ", extract.extract_papers(conn))
    print("labels   ", extract.extract_labels(conn))


def cmd_status(conn, as_of: date) -> None:
    from evstore.retrieval import StoreClient

    totals = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in (
        "documents", "parent_chunks", "chunks", "embedding_cache", "drugs", "market_stats", "shelved_programs",
        "trials", "publications", "paper_findings", "doc_drugs", "trial_links", "labels", "label_events",
        "extractions")}
    totals["chunks_without_vector"] = conn.execute(
        "SELECT count(*) FROM chunks WHERE embedding IS NULL").fetchone()[0]
    print("totals   ", json.dumps(totals))
    c = StoreClient(as_of)
    print(f"as_of {as_of}", json.dumps(asyncio.run(c.counts()), default=str))
    c.close()


def cmd_ask(q: str, as_of: date, k: int, vectors: bool) -> None:
    from evstore.retrieval import StoreClient

    c = StoreClient(as_of, use_vectors=vectors)
    for h in asyncio.run(c.rag(q, k=k)):
        print(f"[{h.score:.4f} v{h.vector_rank} t{h.text_rank}] {h.doc_id} {h.source_date} | {h.section}")
        print(f"   {h.title}")
        print(f"   {h.text[:300]}...\n")
    c.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="evstore")
    ap.add_argument("cmd", choices=["build", "embed", "extract", "all", "status", "ask", "cache-export", "cache-import"])
    ap.add_argument("question", nargs="?")
    ap.add_argument("--as-of", default="2026-01-01")
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--no-vectors", action="store_true")
    a = ap.parse_args()
    as_of = date.fromisoformat(a.as_of)

    conn = db.connect()
    cache_dir = ROOT / "data" / "cache"
    if a.cmd == "cache-export":
        print("exported ", db.export_cache(conn, cache_dir), "->", cache_dir)
    if a.cmd == "cache-import":
        db.apply_schema(conn)
        print("imported ", db.import_cache(conn, cache_dir))
    if a.cmd in ("build", "all"):
        cmd_build(conn)
    if a.cmd in ("embed", "all"):
        cmd_embed(conn)
    if a.cmd in ("extract", "all"):
        cmd_extract(conn)
    if a.cmd in ("status", "all"):
        cmd_status(conn, as_of)
    if a.cmd == "ask":
        if not a.question:
            ap.error("ask needs a question")
        cmd_ask(a.question, as_of, a.k, not a.no_vectors)
    conn.close()


if __name__ == "__main__":
    main()
