"""Export the evidence store for teammates (person 1).

Writes data/export/frontier-store/ (git-ignored) and a zip of it:

* retrieval/records.json: every documents row whose source the retrieval Record
  accepts, in the Record shape, so StubRetrievalClient can serve the real store:
  FRONTIER_FIXTURES_DIR=<unzipped folder> makes agents read it instead of the fixtures.
  Full text (where stored) is in fields.full_text.
* retrieval/records-other-sources.json: rows whose source Record does not accept yet
  (curated: shelved drugs and market context). Same shape; not loaded by the stub.
* tables/<table>.json: the structured tables (drugs, market_stats, shelved_programs,
  trials) and parent_chunks.
* README.txt: what is in it, with row counts.

    uv run python -m frontier.ingest.export
"""

from __future__ import annotations

import json
import shutil
import sys
import typing
from pathlib import Path

from frontier.core.config import REPO_ROOT, Settings
from frontier.core.retrieval import Record, Source

OUT = REPO_ROOT / "data" / "export" / "frontier-store"
TABLES = ("drugs", "market_stats", "shelved_programs", "trials", "parent_chunks")
ACCEPTED = set(typing.get_args(Source))


def records(conn) -> tuple[list[dict], list[dict]]:
    """(rows Record accepts, rows from other sources), as Record-shaped dicts."""
    rows = conn.execute(
        "SELECT id, source, source_date, title, body, url, fields, full_text, version_label"
        " FROM documents ORDER BY source, id, source_date"
    ).fetchall()
    ok, other = [], []
    for id_, source, when, title, body, url, fields, full_text, label in rows:
        extra = dict(fields or {})
        extra["version_label"] = label
        if full_text:
            extra["full_text"] = full_text
        rec = {
            "id": id_,
            "source": source,
            "source_date": when.isoformat(),
            "title": title,
            "body": body,
            "url": url,
            "fields": extra,
        }
        if source in ACCEPTED:
            Record.model_validate(rec)  # fails loudly if the shape drifts
            ok.append(rec)
        else:
            other.append(rec)
    return ok, other


def table(conn, name: str) -> list[dict]:
    cur = conn.execute(f"SELECT row_to_json(t) FROM {name} t")  # names from TABLES only
    return [r[0] for r in cur.fetchall()]


def write(conn, out: Path = OUT) -> dict[str, int]:
    if out.exists():
        shutil.rmtree(out)
    (out / "retrieval").mkdir(parents=True)
    (out / "tables").mkdir()
    ok, other = records(conn)
    counts = {"records.json": len(ok), "records-other-sources.json": len(other)}
    (out / "retrieval" / "records.json").write_text(json.dumps(ok, ensure_ascii=False))
    (out / "retrieval" / "records-other-sources.json").write_text(
        json.dumps(other, ensure_ascii=False)
    )
    for name in TABLES:
        rows = table(conn, name)
        counts[f"tables/{name}.json"] = len(rows)
        (out / "tables" / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False))
    by_source: dict[str, int] = {}
    for r in ok + other:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    with_text = len({r["id"] for r in ok if "full_text" in r["fields"]})
    lines = [
        "Frontier Hypothesis Engine: COPD evidence store export (person 1).",
        "All data dated 2023-01-01 to 2025-12-31.",
        "",
        "retrieval/records.json: documents rows in the retrieval Record shape.",
        "  Use with StubRetrievalClient: FRONTIER_FIXTURES_DIR=<this folder>.",
        f"  Papers with full text (fields.full_text): {with_text}.",
        "  Free-to-read PDF text is for the team's own analysis, not for republishing.",
        "retrieval/records-other-sources.json: curated rows (shelved drugs, market context);",
        "  'curated' is not yet a retrieval Source, so the stub does not load them.",
        "tables/: structured tables and parent_chunks (full-text sections).",
        "",
        "Rows by source: " + ", ".join(f"{k} {v}" for k, v in sorted(by_source.items())),
        *[f"{k}: {v} rows" for k, v in counts.items()],
    ]
    (out / "README.txt").write_text("\n".join(lines) + "\n")
    return {**counts, **{f"source {k}": v for k, v in by_source.items()}}


def main() -> int:
    import psycopg

    with psycopg.connect(Settings().database_url) as conn:
        counts = write(conn)
    zipped = shutil.make_archive(str(OUT), "zip", OUT.parent, OUT.name)
    for k, v in counts.items():
        print(f"{k}: {v}")
    print(f"written to {OUT} and {zipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
