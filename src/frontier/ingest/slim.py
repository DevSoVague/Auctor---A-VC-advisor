"""Slim the store to what the team whiteboard asks for (person 1).

Keeps the papers in a curated collection (the 50 drug-trial papers and any later set),
drugs (fda, curated), the trials linked to those drugs, market context and news (news is
back in the store at Person 3's request, 2026-11-08). Every other PubMed paper and every
unlinked trial is first written to data/archive/ (git-ignored), then deleted from the
store. Stops
without deleting anything if any of the 50 papers is missing or untagged, or if the store
has no drugs to link trials to.

    uv run python -m frontier.ingest.slim
"""

from __future__ import annotations

import gzip
import json
import sys

from frontier.core.config import REPO_ROOT, Settings
from frontier.ingest import collections, structured

ARCHIVE = REPO_ROOT / "data" / "archive"
KEEP = "copd-drug-trials-2023-2024"
DROP = "source = 'pubmed' AND jsonb_array_length(COALESCE(fields->'collections', '[]'::jsonb)) = 0"


def check(conn) -> list[str]:
    """Problems that must stop the slim-down (empty list = safe)."""
    (papers,) = [s for s in collections.load_sets() if s["name"] == KEEP]
    rows = conn.execute(
        "SELECT DISTINCT id FROM documents"
        " WHERE id = ANY(%(ids)s) AND COALESCE(fields->'collections', '[]'::jsonb) ? %(keep)s",
        {"ids": papers["ids"], "keep": KEEP},
    ).fetchall()
    tagged = {r[0] for r in rows}
    missing = [i for i in papers["ids"] if i not in tagged]
    problems = (
        [f"{len(missing)} of the 50 papers missing or untagged: {missing[:5]}"] if missing else []
    )
    if not structured.store_drug_index(conn):
        problems.append("no drugs in the store (run fda and shelved first)")
    return problems


def unlinked_trials(conn) -> list[str]:
    """Ids of ctgov rows whose interventions match none of the store's drugs."""
    index = structured.store_drug_index(conn)
    docs = structured.read_docs(conn, "ctgov")
    return sorted({d["id"] for d in docs if not structured.is_linked(d, index)})


def _add_to_archive(path, rows: list[dict]) -> int:
    """Merge rows into an archive file, never dropping what it already holds.

    Returns the number of rows not already in it."""
    old = json.loads(gzip.decompress(path.read_bytes())) if path.exists() else []
    key = lambda r: (r["id"], r["source_date"], r["version_label"])  # noqa: E731
    seen = {key(r) for r in old}
    added = [r for r in rows if key(r) not in seen]
    if added:
        path.write_bytes(gzip.compress(json.dumps(old + added).encode()))
    return len(added)


ROW = (
    "SELECT json_build_object('id', id, 'source', source, 'source_date', source_date,"
    " 'version_label', version_label, 'title', title, 'body', body, 'url', url,"
    " 'fields', fields) FROM documents WHERE "
)


def archive(conn) -> dict[str, int]:
    """Add every row about to be deleted to data/archive/; returns rows newly archived."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    counts = {}
    for source in ("pubmed",):
        rows = conn.execute(
            ROW + "source = %(source)s AND (" + DROP + ")", {"source": source}
        ).fetchall()
        counts[source] = _add_to_archive(
            ARCHIVE / f"{source}-2023-2025.json.gz", [r[0] for r in rows]
        )
    trials = conn.execute(
        ROW + "source = 'ctgov' AND id = ANY(%s)", (unlinked_trials(conn),)
    ).fetchall()
    counts["ctgov"] = _add_to_archive(ARCHIVE / "ctgov-2023-2025.json.gz", [r[0] for r in trials])
    return counts


def delete(conn) -> int:
    unlinked = unlinked_trials(conn)
    cur = conn.execute("DELETE FROM documents WHERE " + DROP)
    n = cur.rowcount
    cur = conn.execute("DELETE FROM documents WHERE source = 'ctgov' AND id = ANY(%s)", (unlinked,))
    return n + cur.rowcount


def main() -> int:
    import psycopg

    with psycopg.connect(Settings().database_url) as conn:
        problems = check(conn)
        if problems:
            print("stopped, nothing deleted:", *problems, sep="\n  ")
            return 1
        archived = archive(conn)
        print(f"archived to {ARCHIVE}: {archived}")
        deleted = delete(conn)
        left = conn.execute(
            "SELECT source, count(*) FROM documents GROUP BY source ORDER BY source"
        ).fetchall()
    print(f"deleted {deleted} rows; left: {dict(left)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
