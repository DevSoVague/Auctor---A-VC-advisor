"""Slim the store to what the team whiteboard asks for (person 1).

Keeps the 50 curated drug-trial papers, drugs (fda, curated), the trials linked to those
drugs, and market context. Every other PubMed paper, every news item and every unlinked
trial is first written to data/archive/ (git-ignored), then deleted from the store. Stops
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
DROP = (
    "source = 'news'"
    " OR (source = 'pubmed' AND NOT COALESCE(fields->'collections', '[]'::jsonb) ? %(keep)s)"
)


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


def archive(conn) -> dict[str, int]:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    counts = {}
    for source in ("pubmed", "news"):
        rows = conn.execute(
            "SELECT json_build_object('id', id, 'source', source, 'source_date', source_date,"
            " 'version_label', version_label, 'title', title, 'body', body, 'url', url,"
            " 'fields', fields) FROM documents WHERE source = %(source)s AND (" + DROP + ")",
            {"source": source, "keep": KEEP},
        ).fetchall()
        path = ARCHIVE / f"{source}-2023-2025.json.gz"
        path.write_bytes(gzip.compress(json.dumps([r[0] for r in rows]).encode()))
        counts[source] = len(rows)
    trials = conn.execute(
        "SELECT json_build_object('id', id, 'source', source, 'source_date', source_date,"
        " 'version_label', version_label, 'title', title, 'body', body, 'url', url,"
        " 'fields', fields) FROM documents WHERE source = 'ctgov' AND id = ANY(%s)",
        (unlinked_trials(conn),),
    ).fetchall()
    (ARCHIVE / "ctgov-2023-2025.json.gz").write_bytes(
        gzip.compress(json.dumps([r[0] for r in trials]).encode())
    )
    counts["ctgov"] = len(trials)
    return counts


def delete(conn) -> int:
    unlinked = unlinked_trials(conn)
    cur = conn.execute("DELETE FROM documents WHERE " + DROP, {"keep": KEEP})
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
