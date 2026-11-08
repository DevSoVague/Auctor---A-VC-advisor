"""Tools a `run_tools` node can call BEFORE the model runs. Add your own with @tool.

A tool is  async def fn(ctx, state, hid, **args) -> list[dict]  returning evidence docs:
    {"id": "tool:...", "source_date": "2019-05-01", "title": "...", "body": "...", "source": "tool"}
They are added to the agent's evidence pack (so the model can cite them). Docs dated on or after
the as-of date are dropped by the pack, so a tool can never leak the future.
Args may use {disease} and {mechanism} (the current card's mechanism).
"""

from __future__ import annotations

import hashlib
from typing import Any, Awaitable, Callable

REGISTRY: dict[str, Callable[..., Awaitable[list[dict]]]] = {}


def tool(name: str):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def _id(name: str, key: str) -> str:
    return f"tool:{name}:{hashlib.sha1(key.encode()).hexdigest()[:8]}"


@tool("corpus_search")
async def corpus_search(ctx, state, hid, query: str, k: int = 6, **_: Any) -> list[dict]:
    """Extra search of the retrieval corpus (stub fixtures or person 1's API)."""
    return await ctx.retrieval.search(query, k=k)


@tool("http_json")
async def http_json(ctx, state, hid, url: str, title: str = "HTTP tool result", source_date: str | None = None,
                    **_: Any) -> list[dict]:
    """GET a JSON/text URL and return it as one evidence doc. Give it a source_date before the as-of date."""
    import httpx

    if not source_date:
        raise ValueError("http_json needs source_date (YYYY-MM-DD, before the as-of date) so the leakage guard can check it")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url)
        r.raise_for_status()
    return [{"id": _id("http", url), "source": "tool", "source_date": source_date, "title": title,
             "body": r.text[:4000]}]


# ---------------------------------------------------------------------------- database tools (Postgres + Milvus)
def _db(ctx):
    """The database client: the run's own retrieval if it is the database, else one built for the as-of date."""
    from agents.analysis.retrieval_db import DatabaseRetrieval, make_db

    r = ctx.retrieval
    r = getattr(r, "base", r)  # WithWebSearch wraps the real client
    return r if isinstance(r, DatabaseRetrieval) else make_db(ctx.config.as_of)


@tool("rag_search")
async def rag_search(ctx, state, hid, query: str, k: int = 6, **_: Any) -> list[dict]:
    """RAG over the database's unstructured text: small chunks are matched, the larger parent text is returned."""
    return await _db(ctx).search(query, k=k)


@tool("get_document")
async def get_document(ctx, state, hid, doc_id: str, **_: Any) -> list[dict]:
    """One original document (full text) from the database, by id (doc:... or a chunk:... parent id)."""
    d = await _db(ctx).doc(doc_id)
    return [d] if d else []


@tool("query_table")
async def query_table(ctx, state, hid, table: str, where: dict | None = None, contains: dict | None = None,
                      columns: list[str] | None = None, limit: int = 20, **_: Any) -> list[dict]:
    """Read rows from a database table (stats: drugs, market_stats, shelved_programs, trials, targets, ...).

    where    {column: value or [values]}   exact match          contains  {column: "text"}   case-insensitive contains
    Only tables/columns listed in db_mapping.yaml can be read; rows dated on/after the as-of date are hidden.
    Returns ONE evidence doc holding the rows as a small table, so the model can cite it.
    """
    from agents.analysis.retrieval_db import _iso, load_mapping

    db = _db(ctx)
    rows = await db.query_table(table, where=where, ilike=contains, columns=columns, limit=limit)
    if not rows:
        return []
    date_col = load_mapping()["tables"][table]["date"]
    dates = [_iso(r.get(date_col)) for r in rows if _iso(r.get(date_col))]
    cols = list(rows[0])
    body = " | ".join(cols) + "\n" + "\n".join(" | ".join(str(r.get(c)) for c in cols) for r in rows)
    key = f"{table}|{where}|{contains}|{columns}|{limit}"
    return [{"id": _id(f"table:{table}", key), "source": "db", "source_date": max(dates) if dates else None,
             "title": f"Table {table}: {len(rows)} row(s)", "body": body[:4000], "tags": ["table", table]}]
