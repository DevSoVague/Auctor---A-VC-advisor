"""Embed chunks with Qwen3-Embedding-8B on Nebius. Cached forever in embedding_cache."""

from __future__ import annotations

import asyncio
import contextvars

import numpy as np
import psycopg
from openai import AsyncOpenAI

from evstore.chunk import all_rows
from evstore.config import EMBED_API_KEY, EMBED_BASE_URL, EMBED_DIM, EMBED_MODEL, QUERY_INSTRUCTION

BATCH = 16
CONCURRENCY = 12  # embedding calls in flight per event loop (all agents, cards and branches); ~1-3 s each


# Callers can route embeddings to another Nebius account for the current async context (A4/A5 do).
KEY_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar("evstore_embed_key", default=None)

# One client (= one connection pool) and one in-flight cap per key and event loop. A new client per query opened a
# fresh HTTPS connection for every search; with A3/A4/A5 forking for several cards at once that burst of new
# connections got refused ("All connection attempts failed").
_CLIENTS: dict[tuple, AsyncOpenAI] = {}
_GATES: dict[int, asyncio.Semaphore] = {}
_QUERY_CACHE: dict[tuple[str, str], np.ndarray] = {}  # (model, query) -> vector, for this process
_QUERY_CACHE_MAX = 4096
_PENDING: dict[tuple, asyncio.Future] = {}  # identical queries in flight at the same moment share one call


def _loop_id() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


def _gate() -> asyncio.Semaphore:
    lid = _loop_id()
    if lid not in _GATES:
        _GATES[lid] = asyncio.Semaphore(CONCURRENCY)
    return _GATES[lid]


def client() -> AsyncOpenAI:
    key = (KEY_OVERRIDE.get() or "").strip() or EMBED_API_KEY
    if not key:
        raise RuntimeError("EMBED_API_KEY / NEBIUS_API_KEY is not set (looked in env, evidence-store/.env, repo .env)")
    k = (key, EMBED_BASE_URL, _loop_id())
    if k not in _CLIENTS:
        _CLIENTS[k] = AsyncOpenAI(base_url=EMBED_BASE_URL, api_key=key, timeout=120, max_retries=6)
    return _CLIENTS[k]


async def embed_texts(texts: list[str], api: AsyncOpenAI | None = None) -> tuple[list[np.ndarray], int]:
    """Returns (vectors, prompt tokens). Order matches texts."""
    api = api or client()
    sem = _gate()
    out: list[np.ndarray | None] = [None] * len(texts)
    tokens = 0

    async def one(start: int):
        nonlocal tokens
        async with sem:
            resp = await api.embeddings.create(model=EMBED_MODEL, input=texts[start:start + BATCH])
        for item in resp.data:
            vec = np.asarray(item.embedding, dtype=np.float32)
            assert vec.shape == (EMBED_DIM,), vec.shape
            out[start + item.index] = vec
        tokens += getattr(resp.usage, "prompt_tokens", 0) or 0

    await asyncio.gather(*(one(i) for i in range(0, len(texts), BATCH)))
    return out, tokens  # type: ignore[return-value]


async def embed_query(q: str, api: AsyncOpenAI | None = None) -> np.ndarray:
    """Same query, same vector: cards and agents search the same drug names, so repeats are free."""
    key = (EMBED_MODEL, q)
    hit = _QUERY_CACHE.get(key)
    if hit is not None:
        return hit
    pk = (*key, _loop_id())
    if pk in _PENDING:
        return await asyncio.shield(_PENDING[pk])
    fut = asyncio.get_running_loop().create_future()
    _PENDING[pk] = fut
    try:
        vecs, _ = await embed_texts([f"Instruct: {QUERY_INSTRUCTION}\nQuery: {q}"], api)
        if len(_QUERY_CACHE) >= _QUERY_CACHE_MAX:
            _QUERY_CACHE.pop(next(iter(_QUERY_CACHE)))
        _QUERY_CACHE[key] = vecs[0]
        fut.set_result(vecs[0])
        return vecs[0]
    except BaseException as exc:
        fut.set_exception(exc)
        fut.exception()  # mark retrieved: no "never retrieved" warning when nobody else was waiting
        raise
    finally:
        _PENDING.pop(pk, None)


def embed_missing(conn: psycopg.Connection) -> dict[str, int]:
    """Embed every chunk that has no vector yet; fill from cache first."""
    conn.execute(
        """UPDATE chunks c SET embedding = e.embedding FROM embedding_cache e
           WHERE c.embedding IS NULL AND e.embed_hash = c.embed_hash AND e.model = %s""",
        (EMBED_MODEL,),
    )
    missing = {h for (h,) in conn.execute("SELECT DISTINCT embed_hash FROM chunks WHERE embedding IS NULL")}
    if not missing:
        conn.commit()
        return {"embedded": 0, "tokens": 0}
    inputs = {r["embed_hash"]: r["embed_input"] for r in all_rows(conn) if r["embed_hash"] in missing}
    hashes = sorted(inputs)
    vecs, tokens = asyncio.run(embed_texts([inputs[h] for h in hashes]))
    with conn.cursor() as cur:
        for h, v in zip(hashes, vecs):
            cur.execute(
                "INSERT INTO embedding_cache (embed_hash, model, embedding) VALUES (%s,%s,%s) "
                "ON CONFLICT DO NOTHING", (h, EMBED_MODEL, v))
        cur.execute(
            """UPDATE chunks c SET embedding = e.embedding FROM embedding_cache e
               WHERE c.embedding IS NULL AND e.embed_hash = c.embed_hash AND e.model = %s""",
            (EMBED_MODEL,),
        )
    conn.commit()
    return {"embedded": len(hashes), "tokens": tokens}
