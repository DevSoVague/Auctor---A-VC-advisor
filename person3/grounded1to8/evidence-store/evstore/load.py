"""Load person 1's export (frontier-store/) into Postgres. Idempotent upserts.

documents       <- retrieval/records.json + retrieval/records-other-sources.json (curated)
drugs, market_stats, shelved_programs, trials, parent_chunks <- tables/*.json
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from evstore.config import STORE_DIR
from evstore.db import clean, sha

LIFTED = ("version_label", "full_text")  # stored as columns, not inside fields


def _read(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def _deep_clean(value):
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [_deep_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _deep_clean(v) for k, v in value.items()}
    return value


def load_documents(conn: psycopg.Connection, store: Path) -> int:
    rows = _read(store / "retrieval" / "records.json")
    other = store / "retrieval" / "records-other-sources.json"
    if other.exists():
        rows += _read(other)
    with conn.cursor() as cur:
        for r in rows:
            fields = _deep_clean(dict(r.get("fields") or {}))
            label = fields.pop("version_label", None) or f"{r['source']}-export"
            full_text = clean(fields.pop("full_text", None))
            title, body = clean(r.get("title")), clean(r.get("body"))
            cur.execute(
                """
                INSERT INTO documents (id, source, source_date, version_label, title, body, url,
                                       fields, text_hash, full_text, origin)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'seed')
                ON CONFLICT (id, source_date) DO UPDATE SET
                  source = EXCLUDED.source, version_label = EXCLUDED.version_label,
                  title = EXCLUDED.title, body = EXCLUDED.body, url = EXCLUDED.url,
                  fields = EXCLUDED.fields, text_hash = EXCLUDED.text_hash,
                  full_text = EXCLUDED.full_text
                """,
                (r["id"], r["source"], r["source_date"], label, title, body, r.get("url"),
                 Jsonb(fields), sha(f"{title}\n{body}\n{full_text or ''}"), full_text),
            )
    return len(rows)


def _upsert(conn: psycopg.Connection, table: str, rows: list[dict], key: tuple[str, ...]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key) or f"{key[0]} = EXCLUDED.{key[0]}"
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
           f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}")
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(sql, [_deep_clean(r[c]) for c in cols])
    return len(rows)


def load_tables(conn: psycopg.Connection, store: Path) -> dict[str, int]:
    t = store / "tables"
    counts = {}
    # drugs first: the others reference drugs(drug_id).
    counts["drugs"] = _upsert(conn, "drugs", _read(t / "drugs.json"), ("drug_id",))
    counts["market_stats"] = _upsert(conn, "market_stats", _read(t / "market_stats.json"), ("drug_id", "period"))
    counts["shelved_programs"] = _upsert(conn, "shelved_programs", _read(t / "shelved_programs.json"), ("drug_id",))
    counts["trials"] = _upsert(conn, "trials", _read(t / "trials.json"), ("trial_id", "published_date"))
    counts["parent_chunks"] = _upsert(conn, "parent_chunks", _read(t / "parent_chunks.json"), ("parent_id",))
    return counts


def load(conn: psycopg.Connection, store: Path = STORE_DIR) -> dict[str, int]:
    if not (store / "retrieval" / "records.json").exists():
        raise FileNotFoundError(f"{store}/retrieval/records.json not found; unzip frontier-store.zip there "
                                "or set FRONTIER_STORE_DIR")
    counts = {"documents": load_documents(conn, store)}
    counts.update(load_tables(conn, store))
    conn.execute(
        "INSERT INTO ingest_log (source, query, fetched, stored, skipped) VALUES ('export', %s, %s, %s, 0)",
        (str(store), counts["documents"], counts["documents"]),
    )
    conn.commit()
    return counts
