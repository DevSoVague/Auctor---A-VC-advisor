"""Shelved or failed COPD drugs, curated by hand (person 1).

Reads ingest/curated/copd_shelved_drugs.json (pathway, status, reason, side effects,
trials and dated sources per drug) and stores it in documents,
source 'curated', id curated:shelved:<drug>. Each drug gets one row per source date
(cumulative), so a backtest sees a failure as soon as it was first reported.

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


def _cite(s: dict) -> str:
    return f"{s.get('id') or source_url(s)} ({s['date']}, {s['type']})"


def rows(data: dict | None = None) -> list[dict]:
    """One version per drug per source date. A version holds only the sources public by
    its date, so a backtest sees each failure as it was known then. The latest version
    also carries the summary fields (status, reason, side effects, trials)."""
    data = data or json.loads(DATA.read_text())
    out = []
    for d in data["drugs"]:
        dates = sorted({date.fromisoformat(s["date"]) for s in d["sources"]})
        for n, when in enumerate(dates, start=1):
            known = [s for s in d["sources"] if date.fromisoformat(s["date"]) <= when]
            is_latest = n == len(dates)
            lines = [
                f"Company: {d['company'] or 'not stated in sources'}",
                f"Pathway: {d['pathway']}",
            ]
            if is_latest:
                lines += [
                    f"Status: {d['status']}",
                    f"Reason: {d['reason']}",
                    f"Side effects: {d['side_effects']}",
                    f"Trials: {', '.join(d['trials'])}",
                ]
            lines += ["Reported so far:"] + [f"- {_cite(s)}: {s['fact']}" for s in known]
            body = "\n".join(lines)
            keep = ("drug", "company", "pathway", "phase_reached", "reason_category")
            fields = {k: d.get(k) for k in keep}
            if is_latest:
                fields.update({k: d[k] for k in ("status", "reason", "side_effects", "trials")})
            fields.update(
                {"sources": known, "category": "shelved_or_failed", "collection": data["name"]}
            )
            title = f"{d['drug']}: failed or shelved in COPD"
            out.append(
                {
                    "id": f"curated:shelved:{slug(d['drug'])}",
                    "source_date": when,
                    "version_label": "curated-shelved-v2" + ("" if is_latest else "-partial"),
                    "title": title,
                    "body": body,
                    "url": source_url(known[-1]),
                    "fields": fields,
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


def remove_stale(conn, records: list[dict]) -> int:
    """Drop curated:shelved rows whose (id, date) the current file no longer produces."""
    keep = [(r["id"], r["source_date"]) for r in records]
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM documents WHERE id LIKE 'curated:shelved:%%'"
            " AND (id, source_date) NOT IN (SELECT * FROM unnest(%s::text[], %s::date[]))",
            ([k[0] for k in keep], [k[1] for k in keep]),
        )
        return cur.rowcount


def main() -> int:
    import psycopg

    records = rows()
    with psycopg.connect(Settings().database_url) as conn:
        upsert(conn, records)
        removed = remove_stale(conn, records)
    for r in records:
        print(f"{r['source_date']}  {r['version_label']:<28} {r['title']}")
    drugs = len({r["id"] for r in records})
    print(f"done: {drugs} drugs, {len(records)} versions, {removed} stale removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
