"""Shelved or failed COPD drugs, curated by hand (person 1).

Reads ingest/curated/copd_shelved_drugs.json (pathway, status, reason, side effects,
trials and dated sources per drug) and stores one row per drug in documents,
source 'curated', id curated:shelved:<drug>. A row is dated by its latest source,
so a backtest only sees a failure once every fact in the row was public.

    uv run python -m frontier.ingest.shelved
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

from frontier.core.config import Settings

DATA = Path(__file__).resolve().parent / "curated" / "copd_shelved_drugs.json"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def source_url(source: dict) -> str:
    if source.get("url"):
        return source["url"]
    return f"https://pubmed.ncbi.nlm.nih.gov/{source['id'].removeprefix('pmid:')}/"


def rows(data: dict | None = None) -> list[dict]:
    data = data or json.loads(DATA.read_text())
    out = []
    for d in data["drugs"]:
        latest = max(date.fromisoformat(s["date"]) for s in d["sources"])
        cites = "; ".join(f"{s.get('id') or source_url(s)} ({s['date']})" for s in d["sources"])
        body = "\n".join(
            [
                f"Company: {d['company'] or 'not stated in sources'}",
                f"Pathway: {d['pathway']}",
                f"Status: {d['status']}",
                f"Reason: {d['reason']}",
                f"Side effects: {d['side_effects']}",
                f"Trials: {', '.join(d['trials'])}",
                f"Sources: {cites}",
            ]
        )
        title = f"{d['drug']}: failed or shelved in COPD"
        out.append(
            {
                "id": f"curated:shelved:{slug(d['drug'])}",
                "source_date": latest,
                "version_label": "curated-shelved-v1",
                "title": title,
                "body": body,
                "url": source_url(d["sources"][0]),
                "fields": {**d, "category": "shelved_or_failed", "collection": data["name"]},
                "text_hash": hashlib.sha256(f"{title}\n{body}".encode()).hexdigest(),
            }
        )
    return out


UPSERT = """
INSERT INTO documents (id, source, source_date, version_label, title, body, url, fields, text_hash)
VALUES (%(id)s, 'curated', %(source_date)s, %(version_label)s, %(title)s, %(body)s, %(url)s,
        %(fields)s, %(text_hash)s)
ON CONFLICT (id, source_date) DO UPDATE SET
  version_label = EXCLUDED.version_label, title = EXCLUDED.title, body = EXCLUDED.body,
  url = EXCLUDED.url, fields = EXCLUDED.fields, text_hash = EXCLUDED.text_hash,
  embedding = CASE WHEN documents.text_hash = EXCLUDED.text_hash
                   THEN documents.embedding END
"""


def upsert(conn, records: list[dict]) -> int:
    from psycopg.types.json import Jsonb

    with conn.cursor() as cur:
        cur.executemany(UPSERT, [{**r, "fields": Jsonb(r["fields"])} for r in records])
    return len(records)


def main() -> int:
    import psycopg

    records = rows()
    with psycopg.connect(Settings().database_url) as conn:
        upsert(conn, records)
    for r in records:
        print(f"{r['source_date']}  {r['title']}")
    print(f"done: {len(records)} drugs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
