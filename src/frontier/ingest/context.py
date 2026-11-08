"""COPD market context from consulting and analyst sources (person 1).

Reads ingest/curated/copd_market_context.json and stores one curated row per fact,
source 'curated', id curated:context:<n>, dated by its source.

    uv run python -m frontier.ingest.context
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from frontier.core.config import Settings
from frontier.ingest.shelved import UPSERT, upsert

DATA = Path(__file__).resolve().parent / "curated" / "copd_market_context.json"

__all__ = ["rows", "UPSERT", "upsert"]


def rows(data: dict | None = None) -> list[dict]:
    data = data or json.loads(DATA.read_text())
    out = []
    for n, f in enumerate(data["facts"], start=1):
        title = f["title"]
        body = f"[{f['type']}, {f['date']}] {f['fact']}\nSource: {f['url']}"
        out.append(
            {
                "id": f"curated:context:{n}",
                "source_date": date.fromisoformat(f["date"]),
                "version_label": "curated-context-v1",
                "title": title,
                "body": body,
                "url": f["url"],
                "fields": {**f, "category": "market_context", "collection": data["name"]},
                "text_hash": hashlib.sha256(f"{title}\n{body}".encode()).hexdigest(),
            }
        )
    return out


def main() -> int:
    import psycopg

    records = rows()
    with psycopg.connect(Settings().database_url) as conn:
        upsert(conn, records)
    for r in records:
        print(f"{r['source_date']}  {r['title']}")
    print(f"done: {len(records)} facts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
