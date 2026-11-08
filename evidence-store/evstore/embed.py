"""Embed chunks with Qwen3-Embedding-8B on Nebius. Cached forever in embedding_cache."""

from __future__ import annotations

import asyncio

import numpy as np
import psycopg
from openai import AsyncOpenAI

from evstore.chunk import all_rows
from evstore.config import EMBED_DIM, EMBED_MODEL, NEBIUS_API_KEY, NEBIUS_BASE_URL, QUERY_INSTRUCTION

BATCH = 16
CONCURRENCY = 4


def client() -> AsyncOpenAI:
    if not NEBIUS_API_KEY:
        raise RuntimeError("NEBIUS_API_KEY is not set (looked in env, evidence-store/.env, frontier-engine/.env)")
    return AsyncOpenAI(base_url=NEBIUS_BASE_URL, api_key=NEBIUS_API_KEY, timeout=120, max_retries=4)


async def embed_texts(texts: list[str], api: AsyncOpenAI | None = None) -> tuple[list[np.ndarray], int]:
    """Returns (vectors, prompt tokens). Order matches texts."""
    api = api or client()
    sem = asyncio.Semaphore(CONCURRENCY)
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
    vecs, _ = await embed_texts([f"Instruct: {QUERY_INSTRUCTION}\nQuery: {q}"], api)
    return vecs[0]


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
