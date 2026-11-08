"""Marketed COPD drugs: FDA labels plus Medicare Part D stats (person 1).

For each drug in ingest/curated/copd_market_drugs.json:

* FDA label from openFDA (drug/label), the latest version effective on or before
  LABEL_CUTOFF: mechanism of action (pathway), indications and usage, dosage,
  contraindications, warnings, adverse reactions (side effects), clinical studies.
  A label whose indications don't mention COPD is reported and skipped.
* Medicare Part D Spending by Drug (CMS, data year 2023, released May 2025): total
  spending, claims, beneficiaries and average spend per claim, per beneficiary and
  per dosage unit, summed over the drug's brand-name rows (all indications, not
  COPD only).

Each drug is stored under one id, fda:<application number>, in two versions:
  fda-label      label only, dated by the label's effective date
  fda-label+cms  label plus Part D stats, dated by the later of the label date
                 and the CMS release, so a backtest never sees the stats early.

    uv run python -m frontier.ingest.fda
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from frontier.core.config import REPO_ROOT, Settings

OPENFDA = "https://api.fda.gov/drug/label.json"
LABEL_CUTOFF = "20251231"  # no 2026 label versions
CMS_CSV = (
    "https://data.cms.gov/sites/default/files/2025-05/56d95a8b-138c-4b60-84a5-613fbab7197f/"
    "DSD_PTD_RY25_P04_V10_DY23_BGM.csv"
)
CMS_RELEASED = date(2025, 5, 31)  # file published May 2025; read as the month's last day
CMS_YEAR = "2023"
DRUGS = Path(__file__).resolve().parent / "curated" / "copd_market_drugs.json"
RAW_DIR = REPO_ROOT / "data" / "fda"
USER_AGENT = "frontier-hypothesis-engine/0.1"
COPD = re.compile(r"\bCOPD\b|chronic obstructive", re.IGNORECASE)
SECTIONS = {
    "mechanism_of_action": "Mechanism of action",
    "indications_and_usage": "Indications and usage",
    "dosage_and_administration": "Dosage and administration",
    "contraindications": "Contraindications",
    "warnings_and_cautions": "Warnings and precautions",
    "adverse_reactions": "Adverse reactions",
    "clinical_studies": "Clinical studies",
}
STAT_COLUMNS = {
    "Tot_Spndng": "total_spending_usd",
    "Tot_Clms": "total_claims",
    "Tot_Benes": "total_beneficiaries",
    "Avg_Spnd_Per_Clm": "avg_spend_per_claim_usd",
    "Avg_Spnd_Per_Bene": "avg_spend_per_beneficiary_usd",
    "Avg_Spnd_Per_Dsg_Unt_Wghtd": "avg_spend_per_dosage_unit_usd",
}


def _text(value) -> str:
    if isinstance(value, list):
        value = "\n".join(value)
    return " ".join((value or "").split())


def label_params(brand: str) -> dict:
    return {
        "search": f'openfda.brand_name:"{brand}" AND effective_time:[20000101 TO {LABEL_CUTOFF}]',
        "sort": "effective_time:desc",
        "limit": 1,
    }


def parse_label(result: dict) -> dict:
    """The fields we keep from one openFDA label result."""
    of = result.get("openfda", {})
    eff = result.get("effective_time", "")
    return {
        "brand": (of.get("brand_name") or [""])[0],
        "generic": (of.get("generic_name") or [""])[0],
        "manufacturer": (of.get("manufacturer_name") or [""])[0],
        "application_number": (of.get("application_number") or [""])[0],
        "pharm_class_epc": of.get("pharm_class_epc", []),
        "pharm_class_moa": of.get("pharm_class_moa", []),
        "set_id": result.get("set_id"),
        "label_effective": f"{eff[:4]}-{eff[4:6]}-{eff[6:8]}" if len(eff) == 8 else None,
        "sections": {key: _text(result.get(key)) for key in SECTIONS if result.get(key)},
    }


def cms_stats(rows: list[dict], brand_prefix: str) -> dict | None:
    """Part D stats for one drug, summed over its brand-name rows ('Overall' rows only)."""

    def col(row, stem):
        return next((v for k, v in row.items() if k == f"{stem}_{CMS_YEAR}"), None)

    def num(value):
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    brand_key = next((k for k in (rows[0] if rows else {}) if k.lower().startswith("brnd")), None)
    mftr_key = next((k for k in (rows[0] if rows else {}) if k.lower() == "mftr_name"), None)
    matched = [
        r
        for r in rows
        if brand_key
        and r[brand_key].strip().lower().startswith(brand_prefix.lower())
        and (mftr_key is None or r[mftr_key].strip().lower() == "overall")
    ]
    if not matched:
        return None
    totals = {}
    for stem in ("Tot_Spndng", "Tot_Clms", "Tot_Benes"):
        values = [num(col(r, stem)) for r in matched]
        values = [v for v in values if v is not None]
        totals[STAT_COLUMNS[stem]] = round(sum(values), 2) if values else None
    stats = {
        "year": int(CMS_YEAR),
        "brand_rows": [r[brand_key].strip() for r in matched],
        **totals,
    }
    if totals["total_spending_usd"] and totals["total_claims"]:
        stats["avg_spend_per_claim_usd"] = round(
            totals["total_spending_usd"] / totals["total_claims"], 2
        )
    if totals["total_spending_usd"] and totals["total_beneficiaries"]:
        stats["avg_spend_per_beneficiary_usd"] = round(
            totals["total_spending_usd"] / totals["total_beneficiaries"], 2
        )
    if len(matched) == 1:
        stats["avg_spend_per_dosage_unit_usd"] = num(col(matched[0], "Avg_Spnd_Per_Dsg_Unt_Wghtd"))
    return stats


def body_of(label: dict, stats: dict | None, drug_class: str) -> str:
    lines = [f"{label['brand']} ({label['generic']}), {drug_class}; {label['manufacturer']}"]
    for key, heading in SECTIONS.items():
        if key in label["sections"]:
            lines.append(f"{heading}: {label['sections'][key]}")
    if stats:
        lines.append(
            f"Medicare Part D {stats['year']} ({', '.join(stats['brand_rows'])}, all indications): "
            f"spending ${stats['total_spending_usd']:,.0f}, claims {stats['total_claims']:,.0f}, "
            f"beneficiaries {stats['total_beneficiaries']:,.0f}"
        )
    return "\n".join(lines)


def industry_lines(industry: list[dict]) -> list[str]:
    return [f"[{s['type']}, {s['date']}] {s['title']}: {s['fact']}" for s in industry]


def rows_for(
    label: dict, stats: dict | None, drug_class: str, industry: list[dict] | None = None
) -> list[dict]:
    """Label-only version, then a market version (Part D stats and/or industry sources)
    dated by the latest of the label, the CMS release and the industry sources."""
    industry = industry or []
    label_date = date.fromisoformat(label["label_effective"])
    fields = {k: v for k, v in label.items() if k != "sections"}
    fields.update({"drug_class": drug_class, "sections": label["sections"]})
    fields["source_urls"] = [
        f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={label['set_id']}"
    ]
    out = [("fda-label", label_date, fields, body_of(label, None, drug_class))]
    if stats or industry:
        market = {**fields, "industry_sources": industry}
        dates = [label_date] + [date.fromisoformat(s["date"]) for s in industry]
        if stats:
            market.update({"medicare_part_d": stats, "cms_release": CMS_RELEASED.isoformat()})
            dates.append(CMS_RELEASED)
        market["source_urls"] = (
            fields["source_urls"]
            + ([CMS_CSV] if stats else [])
            + [s["url"] for s in industry if s.get("url")]
        )
        body = "\n".join([body_of(label, stats, drug_class), *industry_lines(industry)])
        out.append(("fda-label+cms" if stats else "fda-label+market", max(dates), market, body))
    title = f"{label['brand']} ({label['generic']}): FDA label"
    return [
        {
            "id": f"fda:{label['application_number']}",
            "source_date": when,
            "version_label": tag,
            "title": title,
            "body": body,
            "url": f["source_urls"][0],
            "fields": f,
            "text_hash": hashlib.sha256(f"{title}\n{body}".encode()).hexdigest(),
        }
        for tag, when, f, body in out
    ]


# --- fetching ------------------------------------------------------------------------


def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    for attempt in range(4):
        response = client.get(url, params=params)
        if response.status_code in (200, 404):  # openFDA answers 404 when nothing matches
            return response
        time.sleep(2**attempt)
    response.raise_for_status()
    return response


def fetch_all(client: httpx.Client | None = None) -> tuple[dict, list[dict]]:
    """Labels by brand query, and the CMS rows; raw copies saved in data/fda/."""
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT})
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    labels = {}
    for drug in json.loads(DRUGS.read_text())["drugs"]:
        brand = drug["label_query"]
        response = _get(client, OPENFDA, label_params(brand))
        results = response.json().get("results", []) if response.status_code == 200 else []
        labels[brand] = results[0] if results else None
        time.sleep(0.3)
    (RAW_DIR / "labels.json.gz").write_bytes(gzip.compress(json.dumps(labels).encode()))
    cms_path = RAW_DIR / "part_d_dy2023.csv.gz"
    if not cms_path.exists():
        cms_path.write_bytes(gzip.compress(_get(client, CMS_CSV).content))
    cms_rows = list(
        csv.DictReader(io.StringIO(gzip.decompress(cms_path.read_bytes()).decode("utf-8-sig")))
    )
    return labels, cms_rows


UPSERT = """
INSERT INTO documents (id, source, source_date, version_label, title, body, url, fields, text_hash)
VALUES (%(id)s, 'fda', %(source_date)s, %(version_label)s, %(title)s, %(body)s, %(url)s,
        %(fields)s, %(text_hash)s)
ON CONFLICT (id, source_date) DO UPDATE SET
  version_label = EXCLUDED.version_label, title = EXCLUDED.title, body = EXCLUDED.body,
  url = EXCLUDED.url, fields = EXCLUDED.fields, text_hash = EXCLUDED.text_hash,
  embedding = CASE WHEN documents.text_hash = EXCLUDED.text_hash
                   THEN documents.embedding END
"""


def build(labels: dict, cms_rows: list[dict]) -> tuple[list[dict], list[str]]:
    rows, report = [], []
    for drug in json.loads(DRUGS.read_text())["drugs"]:
        brand = drug["label_query"]
        result = labels.get(brand)
        if not result:
            report.append(f"{brand}: no label effective on or before {LABEL_CUTOFF}")
            continue
        label = parse_label(result)
        if not COPD.search(label["sections"].get("indications_and_usage", "")):
            report.append(f"{brand}: label indications don't mention COPD, skipped")
            continue
        if not (label["application_number"] and label["label_effective"]):
            report.append(f"{brand}: label has no application number or date, skipped")
            continue
        stats = cms_stats(cms_rows, drug["cms_brand"])
        rows += rows_for(label, stats, drug["class"], drug.get("industry_sources"))
        report.append(
            f"{brand}: label {label['label_effective']}, "
            + (
                f"Part D {stats['year']} claims {stats['total_claims']:,.0f}"
                if stats
                else "no Part D row"
            )
        )
    return rows, report


def upsert(conn, rows: list[dict]) -> int:
    from psycopg.types.json import Jsonb

    with conn.cursor() as cur:
        cur.executemany(UPSERT, [{**r, "fields": Jsonb(r["fields"])} for r in rows])
    return len(rows)


def main() -> int:
    import psycopg

    labels, cms_rows = fetch_all()
    rows, report = build(labels, cms_rows)
    print("\n".join(report))
    with psycopg.connect(Settings().database_url) as conn:
        upsert(conn, rows)
    print(f"done: {len(rows)} rows ({len({r['id'] for r in rows})} drugs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
