"""Postgres connection and schema application."""

from __future__ import annotations

import hashlib

import psycopg
from pgvector.psycopg import register_vector

from evstore.config import DATABASE_URL, SQL_DIR


def connect(dsn: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    conn = psycopg.connect(dsn or DATABASE_URL, autocommit=autocommit)
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    return conn


def apply_schema(conn: psycopg.Connection) -> list[str]:
    """Apply sql/*.sql in name order. Every file is safe to rerun."""
    applied = []
    for path in sorted(SQL_DIR.glob("*.sql")):
        conn.execute(path.read_text())
        applied.append(path.name)
    conn.commit()
    return applied


# Paid tables: model work we never want to pay for twice. Exported/imported as tab-separated text,
# which is portable across Postgres and pgvector versions.
PAID = {
    "embedding_cache": "embed_hash, model, embedding, created_at",
    "extractions": "text_hash, prompt_version, model, output, created_at",
}


def export_cache(conn: psycopg.Connection, out_dir) -> dict[str, int]:
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for table, cols in PAID.items():
        n = 0
        with open(out / f"{table}.tsv", "wb") as f, conn.cursor() as cur:
            with cur.copy(f"COPY {table} ({cols}) TO STDOUT") as cp:
                for block in cp:
                    f.write(block)
        n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        counts[table] = n
    return counts


def import_cache(conn: psycopg.Connection, in_dir) -> dict[str, int]:
    """Merge cache files into the paid tables (existing rows win)."""
    from pathlib import Path

    src = Path(in_dir)
    counts = {}
    for table, cols in PAID.items():
        path = src / f"{table}.tsv"
        if not path.exists():
            continue
        with conn.cursor() as cur:
            cur.execute(f"CREATE TEMP TABLE _imp (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
            with open(path, "rb") as f, cur.copy(f"COPY _imp ({cols}) FROM STDIN") as cp:
                while chunk := f.read(1 << 20):
                    cp.write(chunk)
            cur.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _imp ON CONFLICT DO NOTHING")
            counts[table] = cur.rowcount
        conn.commit()
    return counts


def clean(text: str | None) -> str | None:
    """Postgres text cannot hold NUL bytes (some PDF extractions have them)."""
    return text.replace("\x00", "") if isinstance(text, str) else text


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
