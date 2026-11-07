"""Response cache for model calls: a repeated call is never paid for twice (owner: person 2).

Postgres table (agreed with person 1, added in store/schema.sql):

    CREATE TABLE llm_cache (
      cache_key text PRIMARY KEY,   -- sha256 of model, messages, schema, temperature
      model text NOT NULL,
      response text NOT NULL,       -- raw JSON text the model returned
      input_tokens int, output_tokens int,
      created_at timestamptz DEFAULT now());
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

import psycopg
from pydantic import BaseModel


def cache_key(model: str, messages: list[dict], schema: type[BaseModel], temperature: float) -> str:
    """sha256 of model, messages, schema and temperature (PRD, Services, step 2)."""
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "schema": schema.model_json_schema(),
            "temperature": temperature,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class CachedResponse:
    model: str
    response: str
    input_tokens: int
    output_tokens: int


class ResponseCache(Protocol):
    async def get(self, key: str) -> CachedResponse | None: ...
    async def put(self, key: str, value: CachedResponse) -> None: ...


class MemoryCache:
    """In-process cache for tests."""

    def __init__(self) -> None:
        self.rows: dict[str, CachedResponse] = {}

    async def get(self, key: str) -> CachedResponse | None:
        return self.rows.get(key)

    async def put(self, key: str, value: CachedResponse) -> None:
        self.rows[key] = value


class PostgresCache:
    """The llm_cache table."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def get(self, key: str) -> CachedResponse | None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            cur = await conn.execute(
                "SELECT model, response, input_tokens, output_tokens"
                " FROM llm_cache WHERE cache_key = %s",
                (key,),
            )
            row = await cur.fetchone()
        return CachedResponse(row[0], row[1], row[2] or 0, row[3] or 0) if row else None

    async def put(self, key: str, value: CachedResponse) -> None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                "INSERT INTO llm_cache (cache_key, model, response, input_tokens, output_tokens)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (cache_key) DO NOTHING",
                (key, value.model, value.response, value.input_tokens, value.output_tokens),
            )
