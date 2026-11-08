"""ClinicalTrials.gov ingestion for the COPD store (person 1).

Downloads every interventional COPD study from the ClinicalTrials.gov API v2,
keeps the raw JSON in data/ctgov/ (git-ignored) as the archive, and writes up to two versions
of each study into the documents table (one row per version):

  ctgov-live          the current record, dated by its last-update-posted date
  ctgov-registration  registration-only fields, dated by its first-posted date

Retrieval returns the latest version dated before as_of. A study last updated
before the backtest cutoff is therefore seen in full, exactly as it stood. A study
updated after the cutoff is seen only as registered: no status, enrollment,
completion dates, stop reason or results, so later outcomes can't leak in.
This is the PRD's fallback; the API only serves the current record, and AACT's
dated archive snapshots need an account (not set up yet).

Only trials linked to a drug already in the store are written (run fda and shelved
first); pass --all to write every trial.

    uv run python -m frontier.ingest.ctgov            # download today's records and load
    uv run python -m frontier.ingest.ctgov --refresh  # download again even if done today
"""

from __future__ import annotations

import argparse
import calendar
import gzip
import hashlib
import json
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime

import httpx

from frontier.core.config import REPO_ROOT, Settings
from frontier.ingest.window import in_window

API = "https://clinicaltrials.gov/api/v2/studies"
CONDITION = "COPD"  # the API expands condition synonyms
PAGE_SIZE = 1000
RAW_DIR = REPO_ROOT / "data" / "ctgov"
USER_AGENT = "frontier-hypothesis-engine/0.1"


def to_date(value: str | None) -> date | None:
    """'2020-12-18' as is; '2020-12' and '2020' as the last day they could mean."""
    if not value:
        return None
    parts = [int(p) for p in value.split("-")]
    try:
        if len(parts) == 3:
            return date(*parts)
        if len(parts) == 2:
            return date(parts[0], parts[1], calendar.monthrange(parts[0], parts[1])[1])
        return date(parts[0], 12, 31)
    except ValueError:
        return None


def _get(d: dict, path: str, default=None):
    for key in path.split("."):
        if not isinstance(d, dict) or key not in d:
            return default
        d = d[key]
    return d


def _date_of(study: dict, struct: str) -> str | None:
    return _get(study, f"protocolSection.statusModule.{struct}.date")


def snapshot(study: dict) -> dict | None:
    """What one record of a study says: title, registration fields, current fields, summary.
    None if the record has no NCT id or first-posted date."""
    p = study.get("protocolSection", {})
    nct = _get(p, "identificationModule.nctId")
    first_posted = to_date(_date_of(study, "studyFirstPostDateStruct"))
    last_update = to_date(_date_of(study, "lastUpdatePostDateStruct")) or first_posted
    if not (nct and first_posted):
        return None

    title = (
        _get(p, "identificationModule.briefTitle")
        or _get(p, "identificationModule.officialTitle")
        or nct
    )
    conditions = _get(p, "conditionsModule.conditions", [])
    phases = _get(p, "designModule.phases", [])
    sponsor = _get(p, "sponsorCollaboratorsModule.leadSponsor", {}) or {}
    interventions = [
        {"type": i.get("type"), "name": i.get("name"), "other_names": i.get("otherNames", [])}
        for i in _get(p, "armsInterventionsModule.interventions", []) or []
    ]
    registration = {
        "nct_id": nct,
        "conditions": conditions,
        "phase": "/".join(phases) if phases else None,
        "study_type": _get(p, "designModule.studyType"),
        "sponsor": sponsor.get("name"),
        "sponsor_class": sponsor.get("class"),
        "interventions": interventions,
        "first_posted": first_posted.isoformat(),
    }
    enrollment = _get(p, "designModule.enrollmentInfo", {}) or {}
    current = {
        **registration,
        "status": _get(p, "statusModule.overallStatus"),
        "why_stopped": _get(p, "statusModule.whyStopped"),
        "start": _get(p, "statusModule.startDateStruct.date"),
        "primary_completion": _get(p, "statusModule.primaryCompletionDateStruct.date"),
        "primary_completion_type": _get(p, "statusModule.primaryCompletionDateStruct.type"),
        "completion": _get(p, "statusModule.completionDateStruct.date"),
        "enrollment": enrollment.get("count"),
        "enrollment_type": enrollment.get("type"),
        "last_update_posted": last_update.isoformat(),
        "results_first_posted": _date_of(study, "resultsFirstPostDateStruct"),
        "has_results": bool(study.get("hasResults")),
        "primary_outcomes": [
            o.get("measure") for o in _get(p, "outcomesModule.primaryOutcomes", []) or []
        ],
    }
    return {
        "nct": nct,
        "title": title,
        "first_posted": first_posted,
        "last_update": last_update,
        "registration": registration,
        "current": current,
        "summary": _get(p, "descriptionModule.briefSummary") or "",
    }


def versions(study: dict) -> list[dict]:
    """The rows to store for one study: registration and current, oldest first."""
    s = snapshot(study)
    if s is None:
        return []
    rows = []
    if s["last_update"] > s["first_posted"]:
        rows.append(
            _row(
                s["nct"], s["first_posted"], "ctgov-registration", s["title"], s["registration"], ""
            )
        )
    rows.append(
        _row(s["nct"], s["last_update"], "ctgov-live", s["title"], s["current"], s["summary"])
    )
    return rows


def _body(fields: dict, summary: str) -> str:
    lines = [
        f"Conditions: {', '.join(fields['conditions'])}" if fields.get("conditions") else "",
        f"Phase: {fields['phase']}" if fields.get("phase") else "",
        f"Sponsor: {fields['sponsor']} ({fields.get('sponsor_class')})"
        if fields.get("sponsor")
        else "",
        "Interventions: "
        + "; ".join(f"{i['type']}: {i['name']}" for i in fields.get("interventions", []))
        if fields.get("interventions")
        else "",
        f"Status: {fields['status']}" if fields.get("status") else "",
        f"Why stopped: {fields['why_stopped']}" if fields.get("why_stopped") else "",
        f"Enrollment: {fields['enrollment']} ({fields.get('enrollment_type')})"
        if fields.get("enrollment") is not None
        else "",
        f"Primary completion: {fields['primary_completion']}"
        f" ({fields.get('primary_completion_type')})"
        if fields.get("primary_completion")
        else "",
        "Primary outcomes: " + "; ".join(fields["primary_outcomes"])
        if fields.get("primary_outcomes")
        else "",
        summary,
    ]
    return "\n".join(line for line in lines if line)


def row(nct: str, when: date, label: str, title: str, fields: dict, summary: str) -> dict:
    """One documents row for a study version."""
    return _row(nct, when, label, title, fields, summary)


def _row(nct: str, when: date, label: str, title: str, fields: dict, summary: str) -> dict:
    body = _body(fields, summary)
    return {
        "id": f"nct:{nct}",
        "source_date": when,
        "version_label": label,
        "title": title,
        "body": body,
        "url": f"https://clinicaltrials.gov/study/{nct}",
        "fields": fields,
        "text_hash": hashlib.sha256(f"{title}\n{body}".encode()).hexdigest(),
    }


def is_interventional(study: dict) -> bool:
    return _get(study, "protocolSection.designModule.studyType") == "INTERVENTIONAL"


# --- fetching ------------------------------------------------------------------------


def download(path, client: httpx.Client | None = None) -> None:
    """All COPD studies, as a gzipped JSON list, written atomically."""
    client = client or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
    studies, token = [], None
    while True:
        params = {"query.cond": CONDITION, "pageSize": PAGE_SIZE, "format": "json"}
        if token:
            params["pageToken"] = token
        for attempt in range(5):
            response = client.get(API, params=params)
            if response.status_code == 200:
                break
            time.sleep(2**attempt)
        else:
            raise RuntimeError(f"ClinicalTrials.gov API failed ({response.status_code})")
        page = response.json()
        studies.extend(page.get("studies", []))
        print(f"  downloaded {len(studies)} studies", flush=True)
        token = page.get("nextPageToken")
        if not token:
            break
        time.sleep(0.5)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(studies).encode()))
    tmp.replace(path)


def load(path) -> Iterator[dict]:
    yield from json.loads(gzip.decompress(path.read_bytes()))


# --- loading -------------------------------------------------------------------------

UPSERT = """
INSERT INTO documents (id, source, source_date, version_label, title, body, url, fields, text_hash)
VALUES (%(id)s, 'ctgov', %(source_date)s, %(version_label)s, %(title)s, %(body)s, %(url)s,
        %(fields)s, %(text_hash)s)
ON CONFLICT (id, source_date) DO UPDATE SET
  version_label = EXCLUDED.version_label, title = EXCLUDED.title, body = EXCLUDED.body,
  url = EXCLUDED.url, fields = EXCLUDED.fields, text_hash = EXCLUDED.text_hash,
  embedding = CASE WHEN documents.text_hash = EXCLUDED.text_hash
                   THEN documents.embedding END
"""


def upsert(conn, rows: Iterable[dict]) -> int:
    from psycopg.types.json import Jsonb

    rows = [{**r, "fields": Jsonb(r["fields"])} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(UPSERT, rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--refresh", action="store_true", help="download again even if done today")
    parser.add_argument("--download-only", action="store_true", help="skip the database")
    parser.add_argument(
        "--all", action="store_true", help="store every trial, not only those linked to a drug"
    )
    args = parser.parse_args(argv)

    today = datetime.now(UTC).date()
    path = RAW_DIR / f"{today.isoformat()}.json.gz"  # one snapshot per day
    if args.refresh or not path.exists():
        download(path)
    studies = [s for s in load(path) if is_interventional(s)]
    rows = [row for study in studies for row in versions(study) if in_window(row["source_date"])]
    print(f"{len(studies)} interventional studies, {len(rows)} versions", flush=True)
    if not args.download_only:
        import psycopg

        from frontier.ingest import structured

        with psycopg.connect(Settings().database_url) as conn:
            if not args.all:
                index = structured.store_drug_index(conn)
                rows = [r for r in rows if structured.is_linked(r, index)]
                print(
                    f"{len(rows)} versions linked to a drug in the store (run fda, shelved first)"
                )
            upsert(conn, rows)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
