"""The store's date window, from the team whiteboard: 2023, 2024 and 2025 only (person 1).

Every loader keeps only rows (and curated sources) dated inside the window. Running this
module deletes anything already stored outside it:

    uv run python -m frontier.ingest.window

The raw downloads in data/ are kept, so widening the window later only needs the loaders
to be run again.
"""

from __future__ import annotations

import sys
from datetime import date

from frontier.core.config import Settings

START = date(2023, 1, 1)
END = date(2025, 12, 31)


def in_window(when: date | str | None) -> bool:
    if when is None:
        return False
    if isinstance(when, str):
        when = date.fromisoformat(when)
    return START <= when <= END


def purge(conn) -> dict[str, int]:
    """Delete documents and parent chunks dated outside the window; counts per source."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM documents WHERE source_date < %s OR source_date > %s RETURNING source",
            (START, END),
        )
        counts: dict[str, int] = {}
        for (source,) in cur.fetchall():
            counts[source] = counts.get(source, 0) + 1
        cur.execute(
            "DELETE FROM parent_chunks WHERE published_date < %s OR published_date > %s",
            (START, END),
        )
        counts["parent_chunks"] = cur.rowcount
    return counts


def main() -> int:
    import psycopg

    with psycopg.connect(Settings().database_url) as conn:
        counts = purge(conn)
        left = conn.execute(
            "SELECT source, count(*) FROM documents GROUP BY source ORDER BY source"
        ).fetchall()
    print(f"deleted outside {START} to {END}: {counts}")
    print(f"left: {dict(left)}")
    print("next: run python -m frontier.ingest.structured to rebuild the tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
