"""COPD target associations from Open Targets Platform, release 24.09 (person 1).

The agents' reference rule allows any Open Targets release before 2025-01-01; 24.09
(published 2024-09-18) is the last one. The live Open Targets API only serves the newest
release, so this reads the release's parquet files from the EBI FTP site:

    associationByOverallDirect   overall association score, per target, for COPD
    associationByDatatypeDirect  the genetic_association datatype score
    targets                      symbol, name, tractability
    knownDrugsAggregated         highest clinical phase of a drug for COPD on the target

Each part file is downloaded, filtered to COPD (EFO_0000341) and deleted; only the COPD
rows are kept, in data/opentargets/24.09/ (git-ignored), so a rerun reads those instead.
Targets with an overall score of at least --min-score (default 0.1), or a drug for COPD,
are stored:

* the targets table (target_id, symbol, name, association_score, genetic_score,
  tractability, max_phase, evidence_types, as_of_date 2024-09-18, release '24.09');
* documents rows, source opentargets, id ot:EFO_0000341:<ENSG id>, dated 2024-09-18,
  so the retrieval stub's targets() and search() can serve them.

    uv run python -m frontier.ingest.opentargets
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

import httpx

from frontier.core.config import REPO_ROOT, Settings

RELEASE = "24.09"
RELEASED = date(2024, 9, 18)  # the release's announcement date
DISEASE = "EFO_0000341"  # chronic obstructive pulmonary disease
BASE = f"https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/{RELEASE}/output/etl/parquet"
RAW_DIR = REPO_ROOT / "data" / "opentargets" / RELEASE
USER_AGENT = "frontier-hypothesis-engine/0.1"
PART = re.compile(r'href="(part-[^"]+\.parquet)"')


def part_files(index_html: str) -> list[str]:
    """Parquet part file names from an FTP directory listing."""
    return sorted(set(PART.findall(index_html)))


def tractability_labels(items) -> list[str]:
    """'SM: Approved Drug' style labels for the tractability buckets that are true."""
    out = []
    for t in items or []:
        if t.get("value"):
            out.append(f"{t.get('modality')}: {t.get('id')}")
    return out


def build(overall, genetic, targets, drugs, min_score: float) -> list[dict]:
    """One row per kept target, from the four filtered datasets (lists of dicts)."""
    gen = {r["targetId"]: r["score"] for r in genetic}
    info = {r["id"]: r for r in targets}
    phase: dict[str, float] = {}
    for d in drugs:
        if d.get("phase") is not None:
            phase[d["targetId"]] = max(phase.get(d["targetId"], 0), float(d["phase"]))
    rows = []
    for r in sorted(overall, key=lambda r: -r["score"]):
        tid = r["targetId"]
        if r["score"] < min_score and tid not in phase:
            continue
        t = info.get(tid, {})
        rows.append(
            {
                "target_id": tid,
                "symbol": t.get("approvedSymbol"),
                "name": t.get("approvedName"),
                "disease_id": DISEASE,
                "association_score": round(float(r["score"]), 4),
                "genetic_score": round(float(gen[tid]), 4) if tid in gen else None,
                "tractability": tractability_labels(t.get("tractability")),
                "max_phase": phase.get(tid),
                "evidence_types": ["genetic_association"] if tid in gen else [],
                "evidence_count": r.get("evidenceCount"),
                "as_of_date": RELEASED,
                "release": RELEASE,
            }
        )
    return rows


def document(row: dict) -> dict:
    title = f"{row['symbol'] or row['target_id']} and COPD: Open Targets association"
    lines = [
        f"{row['symbol']} ({row['name']}), {row['target_id']}",
        f"Overall association with COPD ({DISEASE}): {row['association_score']}",
        f"Genetic association score: {row['genetic_score']}"
        if row["genetic_score"] is not None
        else "No genetic association evidence.",
        f"Highest phase of a COPD drug on this target: {row['max_phase']}"
        if row["max_phase"] is not None
        else "No drug for COPD on this target.",
        "Tractability: " + ("; ".join(row["tractability"]) or "none listed"),
        f"Open Targets Platform release {RELEASE} (published {RELEASED.isoformat()}).",
    ]
    body = "\n".join(lines)
    fields = {k: v for k, v in row.items() if k != "as_of_date"}
    return {
        "id": f"ot:{DISEASE}:{row['target_id']}",
        "source_date": RELEASED,
        "version_label": f"ot-{RELEASE}",
        "title": title,
        "body": body,
        "url": f"https://platform.opentargets.org/evidence/{row['target_id']}/{DISEASE}",
        "fields": fields,
        "text_hash": hashlib.sha256(f"{title}\n{body}".encode()).hexdigest(),
    }


# --- fetching ------------------------------------------------------------------------


def _filtered(client: httpx.Client, dataset: str, columns: list[str], filters) -> list[dict]:
    """Rows of one dataset that pass the filters; cached as JSON in RAW_DIR."""
    cache = RAW_DIR / f"{dataset}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    import pyarrow.parquet as pq

    resp = client.get(f"{BASE}/{dataset}/")
    resp.raise_for_status()
    parts = part_files(resp.text)
    if not parts:
        raise RuntimeError(f"no parquet files listed at {BASE}/{dataset}/")
    rows, total = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, name in enumerate(parts, 1):
            path = Path(tmp) / name
            with client.stream("GET", f"{BASE}/{dataset}/{name}") as r:
                r.raise_for_status()
                with path.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            total += path.stat().st_size
            table = pq.read_table(path, columns=columns, filters=filters)
            rows += table.to_pylist()
            path.unlink()
            print(f"  {dataset}: {i}/{len(parts)} files, {total / 1e6:.0f} MB read", flush=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows, default=str))
    return rows


def fetch(client: httpx.Client | None = None) -> tuple[list, list, list, list]:
    client = client or httpx.Client(
        timeout=300, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    is_copd = [("diseaseId", "=", DISEASE)]
    overall = _filtered(
        client,
        "associationByOverallDirect",
        ["diseaseId", "targetId", "score", "evidenceCount"],
        is_copd,
    )
    genetic = _filtered(
        client,
        "associationByDatatypeDirect",
        ["diseaseId", "targetId", "datatypeId", "score"],
        [("diseaseId", "=", DISEASE), ("datatypeId", "=", "genetic_association")],
    )
    drugs = _filtered(
        client,
        "knownDrugsAggregated",
        ["diseaseId", "targetId", "drugId", "phase", "prefName"],
        is_copd,
    )
    ids = sorted({r["targetId"] for r in overall} | {r["targetId"] for r in drugs})
    targets = _filtered(
        client,
        "targets",
        ["id", "approvedSymbol", "approvedName", "tractability"],
        [("id", "in", ids)],
    )
    return overall, genetic, targets, drugs


# --- loading -------------------------------------------------------------------------

TARGET_COLUMNS = [
    "target_id",
    "symbol",
    "name",
    "disease_id",
    "association_score",
    "genetic_score",
    "tractability",
    "max_phase",
    "evidence_types",
    "as_of_date",
    "release",
]


UPSERT = """
INSERT INTO documents (id, source, source_date, version_label, title, body, url, fields, text_hash)
VALUES (%(id)s, 'opentargets', %(source_date)s, %(version_label)s, %(title)s, %(body)s, %(url)s,
        %(fields)s, %(text_hash)s)
ON CONFLICT (id, source_date) DO UPDATE SET
  version_label = EXCLUDED.version_label, title = EXCLUDED.title, body = EXCLUDED.body,
  url = EXCLUDED.url, fields = EXCLUDED.fields, text_hash = EXCLUDED.text_hash,
  embedding = CASE WHEN documents.text_hash = EXCLUDED.text_hash
                   THEN documents.embedding END
"""


def write(conn, rows: list[dict]) -> int:
    from psycopg.types.json import Jsonb

    cols = ", ".join(TARGET_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM targets WHERE disease_id = %s AND release = %s", (DISEASE, RELEASE)
        )
        cur.executemany(
            f"INSERT INTO targets ({cols}) VALUES ({', '.join(['%s'] * len(TARGET_COLUMNS))})",
            [[r[c] for c in TARGET_COLUMNS] for r in rows],
        )
        cur.execute(
            "DELETE FROM documents WHERE source = 'opentargets' AND version_label = %s",
            (f"ot-{RELEASE}",),
        )
        cur.executemany(UPSERT, [{**d, "fields": Jsonb(d["fields"])} for d in map(document, rows)])
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    import psycopg

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--min-score", type=float, default=0.1)
    args = parser.parse_args(argv)
    overall, genetic, targets, drugs = fetch()
    rows = build(overall, genetic, targets, drugs, args.min_score)
    print(
        f"COPD in Open Targets {RELEASE}: {len(overall)} direct associations,"
        f" {len(genetic)} with genetic evidence, {len({d['targetId'] for d in drugs})} targets"
        f" with a COPD drug; keeping {len(rows)} (score >= {args.min_score} or a COPD drug)"
    )
    with psycopg.connect(Settings().database_url) as conn:
        write(conn, rows)
    for r in rows[:10]:
        print(f"  {r['symbol']}: score {r['association_score']}, phase {r['max_phase']}")
    print(f"done: {len(rows)} targets stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
