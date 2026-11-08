"""Cut every document version into passages (child chunks) for RAG.

pubmed   abstract (body) + each full-text parent chunk, split to ~1200 chars
ctgov    registry text of each version (both registration and live versions)
fda      each label section separately (adverse_reactions, warnings_and_cautions, ...)
curated  body

What gets embedded is "title | section\n passage", so a passage keeps its context;
`text` stores the passage alone.
"""

from __future__ import annotations

import re

import psycopg

from evstore.db import clean, sha

TARGET = 1200   # chars per chunk (~300 tokens)
MAX = 1700
OVERLAP = 150

# Sections that are never evidence. ('Funding' and 'Front matter' are kept: in PDF-extracted
# papers they often hold the abstract or key-points box.)
SKIP_SECTIONS = re.compile(
    r"^(author contributions?|conflicts? of interest( statement)?|competing interests?|"
    r"financial/nonfinancial disclosures|disclosures?|acknowledg(e)?ments?|patient consent|"
    r"source data|supplementary information|electronic supplementary material|references|"
    r"abbreviations|data availability( statement)?)\.?$", re.I)

_SENT = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9(\[])")


def split(text: str, target: int = TARGET, max_len: int = MAX, overlap: int = OVERLAP) -> list[str]:
    """Paragraph-first, then sentence, then hard splits; small overlap between chunks."""
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    units: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= target:
            units.append(para)
            continue
        for sent in _SENT.split(para):
            while len(sent) > max_len:  # a run-on table row or PDF line
                units.append(sent[:target])
                sent = sent[target:]
            if sent.strip():
                units.append(sent.strip())
    chunks, cur = [], ""
    for u in units:
        if cur and len(cur) + 1 + len(u) > target:
            chunks.append(cur)
            tail = cur[-overlap:]
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            cur = f"{tail} {u}"
        else:
            cur = f"{cur} {u}".strip() if cur else u
    if cur:
        if chunks and len(cur) < target // 4:
            chunks[-1] = f"{chunks[-1]} {cur}"
        else:
            chunks.append(cur)
    return chunks


def _rows_for(doc: dict, parents: list[dict]) -> list[dict]:
    did, src, ver = doc["id"], doc["source"], doc["source_date"]
    title = (doc["title"] or "").strip()
    out: list[dict] = []

    def add(section: str, text: str, published, prefix: str, parent_id: str | None = None):
        for i, piece in enumerate(split(text)):
            out.append({
                "chunk_id": f"{prefix}:c{i}",
                "doc_id": did, "doc_version": ver, "parent_id": parent_id, "source": src,
                "section": section, "position": len(out), "text": clean(piece),
                "published_date": published,
                "embed_input": f"{title} | {section}\n{piece}",
            })

    if src == "pubmed":
        if doc["body"]:
            add("Abstract", doc["body"], ver, f"chunk:{did}:abs")
        for p in sorted(parents, key=lambda p: p["position"]):
            if SKIP_SECTIONS.match((p["section"] or "").strip(" ;.")):
                continue
            add(p["section"] or "Full text", p["text"], p["published_date"], p["parent_id"], p["parent_id"])
    elif src == "ctgov":
        add("Registry record", doc["body"] or "", ver, f"chunk:{did}@{ver}:b")
    elif src == "fda":
        sections = (doc["fields"] or {}).get("sections") or {}
        if isinstance(sections, dict) and sections:
            for name, text in sections.items():
                add(name, text or "", ver, f"chunk:{did}:{name}")
        else:
            add("Label", doc["body"] or "", ver, f"chunk:{did}:b")
    else:
        add("Summary", doc["body"] or "", ver, f"chunk:{did}@{ver}:b")

    seen, unique = set(), []
    for r in out:  # an abstract repeated as a full-text section is embedded once
        h = sha(r["text"])
        if h not in seen:
            seen.add(h)
            unique.append(r)
    return unique


def all_rows(conn: psycopg.Connection) -> list[dict]:
    """Every chunk row the current documents and parent_chunks produce (deterministic)."""
    docs = conn.execute(
        "SELECT id, source, source_date, title, body, fields FROM documents ORDER BY id, source_date"
    ).fetchall()
    parents: dict[str, list[dict]] = {}
    for pid, did, pos, sec, text, pub in conn.execute(
        "SELECT parent_id, doc_id, position, section, text, published_date FROM parent_chunks"
    ).fetchall():
        parents.setdefault(did, []).append(
            {"parent_id": pid, "position": pos, "section": sec, "text": text, "published_date": pub})
    rows: list[dict] = []
    for d in docs:
        doc = dict(zip(("id", "source", "source_date", "title", "body", "fields"), d))
        rows += _rows_for(doc, parents.get(doc["id"], []))
    for r in rows:
        r["embed_hash"] = sha(r["embed_input"])
    return rows


def build_chunks(conn: psycopg.Connection) -> dict[str, int]:
    """Rebuild the chunks table. Vectors come back from embedding_cache, so this is free."""
    from evstore.config import EMBED_MODEL

    rows = all_rows(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks")
        for r in rows:
            cur.execute(
                """INSERT INTO chunks (chunk_id, doc_id, doc_version, parent_id, source, section, position,
                                       text, published_date, embed_hash, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           (SELECT embedding FROM embedding_cache WHERE embed_hash = %s AND model = %s))""",
                (r["chunk_id"], r["doc_id"], r["doc_version"], r["parent_id"], r["source"], r["section"],
                 r["position"], r["text"], r["published_date"], r["embed_hash"],
                 r["embed_hash"], EMBED_MODEL),
            )
    conn.commit()
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    return {"chunks": len(rows), **by_source}
