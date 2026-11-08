"""Structured tables built from the documents store (person 1).

Follows the team's DATABASE_SCHEMA.md (v0) for drugs, market_stats,
shelved_programs and trials, inside the existing Postgres. Reads the rows the
loaders already wrote (fda, curated, ctgov) and rebuilds the tables; safe to rerun.

    uv run python -m frontier.ingest.structured

Run it after fda, shelved and ctgov. Every row keeps a date (as_of_date or
published_date) so a backtest can drop rows dated on or after its cutoff.
"""

from __future__ import annotations

import re
import sys
from datetime import date

from frontier.core.config import Settings
from frontier.ingest.ctgov import to_date


def slug(name: str) -> str:
    name = name.split(" (")[0]  # "benralizumab (in COPD)" -> "benralizumab"
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def latest(docs: list[dict]) -> list[dict]:
    """The latest version of each id."""
    best: dict[str, dict] = {}
    for d in docs:
        if d["id"] not in best or d["source_date"] > best[d["id"]]["source_date"]:
            best[d["id"]] = d
    return list(best.values())


def from_fda(doc: dict) -> tuple[dict, dict]:
    f = doc["fields"]
    sections = f.get("sections", {})
    pathway = "; ".join(f.get("pharm_class_moa") or []) or f.get("drug_class")
    drug = {
        "drug_id": f"drug:{slug(f['generic'])}",
        "name": f["generic"].lower(),
        "brand_names": [f["brand"]],
        "drug_class": f.get("drug_class"),
        "pathway": pathway,
        "targets": [],
        "status": "in_market",
        "as_of_date": doc["source_date"],
        "source_ids": [doc["id"]],
    }
    part_d = f.get("medicare_part_d")
    usage = " ".join(
        sections[k] for k in ("indications_and_usage", "dosage_and_administration") if k in sections
    )
    stats = {
        "drug_id": drug["drug_id"],
        "period": date(part_d["year"], 1, 1)
        if part_d
        else date.fromisoformat(f["label_effective"]),
        "patients_est": int(part_d["total_beneficiaries"])
        if part_d and part_d.get("total_beneficiaries")
        else None,
        "annual_cost_usd": part_d.get("avg_spend_per_beneficiary_usd") if part_d else None,
        "total_spend_usd": part_d.get("total_spending_usd") if part_d else None,
        "total_claims": int(part_d["total_claims"])
        if part_d and part_d.get("total_claims")
        else None,
        "insurance": "unknown",
        "coverage_notes": (
            f"Medicare Part D {part_d['year']}: {part_d['total_claims']:,.0f} claims, "
            f"{part_d['total_beneficiaries']:,.0f} beneficiaries "
            f"({', '.join(part_d['brand_rows'])}; all indications). Other insurers: not in sources."
            if part_d
            else "No Medicare Part D row found. Other insurers: not in sources."
        ),
        "usage": usage or None,
        "side_effects": [sections["adverse_reactions"]] if "adverse_reactions" in sections else [],
        "warnings": [
            sections[k] for k in ("contraindications", "warnings_and_cautions") if k in sections
        ],
        "industry_notes": "\n".join(
            f"[{x['type']}, {x['date']}] {x['title']}: {x['fact']}"
            for x in f.get("industry_sources", [])
        )
        or None,
        "published_date": doc["source_date"],
        "source_ids": [doc["id"]]
        + [x.get("id") or x["url"] for x in f.get("industry_sources", [])],
    }
    return drug, stats


def from_shelved(doc: dict) -> tuple[dict, dict]:
    f = doc["fields"]
    drug_id = f"drug:{slug(f['drug'])}"
    sources = f["sources"]
    source_ids = [s.get("id") or s.get("url") for s in sources]
    drug = {
        "drug_id": drug_id,
        "name": f["drug"].split(" (")[0],
        "brand_names": [],
        "drug_class": None,
        "pathway": f["pathway"],
        "targets": [],
        "status": "shelved",
        "as_of_date": doc["source_date"],
        "source_ids": [doc["id"], *source_ids],
    }
    program = {
        "drug_id": drug_id,
        "shelved_date": min(date.fromisoformat(s["date"]) for s in sources),
        "phase_reached": f.get("phase_reached"),
        "reason_category": f.get("reason_category"),
        "reason_detail": f"{f['status']}. {f['reason']}",
        "side_effects": [f["side_effects"]],
        "published_date": doc["source_date"],
        "source_ids": [doc["id"], *source_ids],
    }
    return drug, program


def drug_index(drugs: list[dict]) -> list[tuple[re.Pattern, str]]:
    """Patterns for linking a trial's interventions to a drug: brand names, and the generic
    name of single-ingredient drugs (a combination's first ingredient would match too much).
    The first matching drug wins, so a link is a pointer, not a complete mapping."""
    index = []
    for d in drugs:
        for brand in d["brand_names"]:
            index.append((re.compile(rf"\b{re.escape(brand.split()[0])}\b", re.I), d["drug_id"]))
        if "," not in d["name"] and " and " not in d["name"]:
            index.append(
                (re.compile(rf"\b{re.escape(d['name'].split()[0])}\b", re.I), d["drug_id"])
            )
    return index


def from_ctgov(doc: dict, index: list[tuple[re.Pattern, str]]) -> dict:
    f = doc["fields"]
    names = " ".join(
        f"{i.get('name') or ''} {' '.join(i.get('other_names') or [])}"
        for i in f.get("interventions", [])
    )
    drug_id = next((d for pattern, d in index if pattern.search(names)), None)
    outcomes = f.get("primary_outcomes") or []
    return {
        "trial_id": f["nct_id"],
        "published_date": doc["source_date"],
        "version_label": doc["version_label"],
        "drug_id": drug_id,
        "title": doc["title"],
        "phase": f.get("phase"),
        "status": f.get("status"),
        "start_date": to_date(f.get("start")),
        "completion_date": to_date(f.get("primary_completion")),
        "enrollment": f.get("enrollment"),
        "primary_outcome": outcomes[0] if outcomes else None,
        "why_stopped": f.get("why_stopped"),
        "sponsor": f.get("sponsor"),
    }


def build(fda_docs: list[dict], shelved_docs: list[dict], ctgov_docs: list[dict]) -> dict:
    drugs, stats, programs = {}, [], []
    for doc in latest(shelved_docs):
        drug, program = from_shelved(doc)
        drugs[drug["drug_id"]] = drug
        programs.append(program)
    # fda.py rows carry the market data; dailymed.py adds earlier label versions of the same
    # ids. The drug is dated by its earliest version, so a backtest sees it from then on.
    first_seen: dict[str, date] = {}
    for doc in fda_docs:
        first_seen[doc["id"]] = min(
            first_seen.get(doc["id"], doc["source_date"]), doc["source_date"]
        )
    market = [d for d in fda_docs if not d["version_label"].startswith("dailymed-")]
    for doc in latest(market or fda_docs):
        drug, stat = from_fda(doc)
        drug["as_of_date"] = first_seen[doc["id"]]
        drugs[drug["drug_id"]] = drug  # marketed label wins if a drug is in both lists
        stats.append(stat)
    index = drug_index(list(drugs.values()))
    trials = [from_ctgov(doc, index) for doc in ctgov_docs]
    return {
        "drugs": list(drugs.values()),
        "market_stats": stats,
        "shelved_programs": programs,
        "trials": trials,
    }


COLUMNS = {
    "drugs": [
        "drug_id",
        "name",
        "brand_names",
        "drug_class",
        "pathway",
        "targets",
        "status",
        "as_of_date",
        "source_ids",
    ],
    "market_stats": [
        "drug_id",
        "period",
        "patients_est",
        "annual_cost_usd",
        "total_spend_usd",
        "total_claims",
        "insurance",
        "coverage_notes",
        "usage",
        "side_effects",
        "warnings",
        "industry_notes",
        "published_date",
        "source_ids",
    ],
    "shelved_programs": [
        "drug_id",
        "shelved_date",
        "phase_reached",
        "reason_category",
        "reason_detail",
        "side_effects",
        "published_date",
        "source_ids",
    ],
    "trials": [
        "trial_id",
        "published_date",
        "version_label",
        "drug_id",
        "title",
        "phase",
        "status",
        "start_date",
        "completion_date",
        "enrollment",
        "primary_outcome",
        "why_stopped",
        "sponsor",
    ],
}


def write(conn, tables: dict) -> dict:
    """Replace the four tables' contents in one transaction."""
    with conn.cursor() as cur:
        # approvals references drugs, so it is emptied too; nothing fills it yet.
        cur.execute("TRUNCATE market_stats, shelved_programs, trials, approvals, drugs")
        for table in ("drugs", "market_stats", "shelved_programs", "trials"):
            cols = COLUMNS[table]
            sql = (
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
            )
            cur.executemany(sql, [[row[c] for c in cols] for row in tables[table]])
        cur.execute(
            "INSERT INTO ingest_log (source, query, fetched, stored, skipped)"
            " VALUES (%s, %s, %s, %s, 0)",
            (
                "structured",
                "documents: fda, curated, ctgov",
                sum(len(v) for v in tables.values()),
                sum(len(v) for v in tables.values()),
            ),
        )
    return {t: len(rows) for t, rows in tables.items()}


def read_docs(conn, source: str) -> list[dict]:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, source_date, version_label, title, fields FROM documents WHERE source = %s",
            (source,),
        )
        return cur.fetchall()


def store_drug_index(conn) -> list[tuple[re.Pattern, str]]:
    """Drug patterns built from the drugs in the store (fda labels and shelved drugs)."""
    shelved = [d for d in read_docs(conn, "curated") if d["id"].startswith("curated:shelved:")]
    drugs = build(read_docs(conn, "fda"), shelved, [])["drugs"]
    return drug_index(drugs)


def is_linked(doc: dict, index: list[tuple[re.Pattern, str]]) -> bool:
    """Whether a ctgov document's interventions match one of the indexed drugs."""
    return from_ctgov(doc, index)["drug_id"] is not None


def main() -> int:
    import psycopg

    with psycopg.connect(Settings().database_url) as conn:
        shelved = [d for d in read_docs(conn, "curated") if d["id"].startswith("curated:shelved:")]
        tables = build(read_docs(conn, "fda"), shelved, read_docs(conn, "ctgov"))
        counts = write(conn, tables)
    linked = sum(1 for t in tables["trials"] if t["drug_id"])
    print(f"{counts}; trials linked to a drug: {linked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
