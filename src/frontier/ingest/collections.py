"""Tag curated sets of documents in the store (person 1).

Each JSON file in ingest/curated/ with a "papers" list names a set of documents,
e.g. copd-drug-trials-2023-2024. Running this adds the set's name to
fields.collections on every row of those ids, so agents and the team can filter
to the set (fields->'collections' ? 'copd-drug-trials-2023-2024'). Safe to rerun.

    uv run python -m frontier.ingest.collections
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from frontier.core.config import Settings

CURATED = Path(__file__).resolve().parent / "curated"

TAG = """
UPDATE documents
SET fields = jsonb_set(fields, '{collections}',
                       COALESCE(fields->'collections', '[]'::jsonb) || to_jsonb(%(name)s::text))
WHERE id = ANY(%(ids)s) AND NOT COALESCE(fields->'collections', '[]'::jsonb) ? %(name)s
"""


def load_sets() -> list[dict]:
    sets = []
    for path in sorted(CURATED.glob("*.json")):
        data = json.loads(path.read_text())
        if "papers" in data:
            sets.append({"name": data["name"], "ids": [p["id"] for p in data["papers"]]})
    return sets


def tag(conn, name: str, ids: list[str]) -> tuple[int, list[str]]:
    """Tag the rows; returns (rows newly tagged, ids not in the store)."""
    with conn.cursor() as cur:
        cur.execute(TAG, {"name": name, "ids": ids})
        tagged = cur.rowcount
        cur.execute("SELECT DISTINCT id FROM documents WHERE id = ANY(%s)", (ids,))
        present = {row[0] for row in cur.fetchall()}
    return tagged, [i for i in ids if i not in present]


def main() -> int:
    import psycopg

    with psycopg.connect(Settings().database_url) as conn:
        for s in load_sets():
            tagged, missing = tag(conn, s["name"], s["ids"])
            print(
                f"{s['name']}: {len(s['ids'])} ids, {tagged} rows newly tagged, missing: {missing}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
