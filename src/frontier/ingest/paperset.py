"""A second, larger set of COPD drug-trial papers, 2023-2025 (person 1).

Person 3 asked for more papers than the whiteboard's 50. The rule that picked the 50
(see copd_drug_trials_2023_2024.json) is applied here to the downloaded PubMed months
2023-01 to 2025-12 (data/pubmed/, the months pubmed.py stores), over 2023-01-01 to
2025-12-31, leaving out the 50 and the papers
excluded by hand there:

* publication type is a clinical trial (any phase, randomized or controlled trial);
* the title or a MeSH heading names a COPD drug or drug class (DRUG below);
* herbal, supplement, device, technique, rehabilitation, screening and service studies
  are left out by title (NOT_DRUG below);
* ranked Phase III, Phase II, multicenter, other trial; randomized first; newest first.

The top --n (default 200) are written to curated/copd_drug_trials_2023_2025_more.json,
a set like the 50: pubmed.py then stores them, collections.py tags them and pmc.py
fetches their open full text.

    uv run python -m frontier.ingest.paperset --n 200
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

from frontier.ingest import pubmed
from frontier.ingest.window import END, START, in_window

CURATED = Path(__file__).resolve().parent / "curated"
FIRST = CURATED / "copd_drug_trials_2023_2024.json"
OUT = CURATED / "copd_drug_trials_2023_2025_more.json"
NAME = "copd-drug-trials-2023-2025-more"
TRIAL = re.compile(r"clinical trial|randomized controlled trial|controlled clinical trial", re.I)
COPD = re.compile(r"\bCOPD\b|chronic obstructive|emphysema|chronic bronchitis", re.I)
DRUG = re.compile(
    r"bronchodilat|muscarinic|anticholinergic|beta-?2|\bLAMA\b|\bLABA\b|\bICS\b|corticosteroid"
    r"|glucocorticoid|steroid|tiotropium|umeclidinium|aclidinium|glycopyrr|revefenacin"
    r"|indacaterol|olodaterol|vilanterol|formoterol|salmeterol|arformoterol|budesonide"
    r"|fluticasone|beclometasone|beclomethasone|mometasone|prednis|roflumilast|ensifentrine"
    r"|phosphodiesterase|\bPDE\d?|dupilumab|mepolizumab|benralizumab|tezepelumab|itepekimab"
    r"|astegolimab|tozorakimab|tanimilast|icenticaftor|ivacaftor|azithromycin|doxycycline"
    r"|antibiotic|anti-bacterial|macrolide|statin|simvastatin|atorvastatin|beta-?blocker"
    r"|bisoprolol|metoprolol|carbocisteine|N-acetylcysteine|acetylcysteine|mucolytic"
    r"|antibod|biologic|monoclonal|inhibitor|antagonist|agonist|triple therapy|inhaled therapy"
    r"|drug therapy|pharmacotherapy",
    re.I,
)
NOT_DRUG = re.compile(
    r"herbal|traditional chinese|decoction|acupunct|moxibust|supplement|vitamin|probiotic"
    r"|nutrition|device|valve|stent|surgery|surgical|technique|rehabilitat|exercise|training"
    r"|yoga|tai chi|singing|breathing exercise|screening|education|self-management|telehealth"
    r"|telemedicine|mobile app|smartphone|nurse-led|service|pathway of care|oxygen therapy"
    r"|ventilation|high-flow|vaccin",
    re.I,
)


def tier(types: list[str]) -> str:
    joined = " ".join(types)
    if "Phase III" in joined:
        return "Phase III"
    if "Phase II" in joined:
        return "Phase II"
    if "Multicenter Study" in types:
        return "Multicenter trial"
    return "Other trial"


TIER_RANK = {"Phase III": 0, "Phase II": 1, "Multicenter trial": 2, "Other trial": 3}


def eligible(r: pubmed.PubmedRecord) -> bool:
    types = r.fields.get("publication_types") or []
    mesh = " ".join(r.fields.get("mesh_terms") or [])
    text = f"{r.title} {mesh}"
    return (
        in_window(r.source_date)
        and any(TRIAL.search(t) for t in types)
        and bool(COPD.search(f"{r.title} {mesh} {r.body[:600]}"))
        and bool(DRUG.search(text))
        and not NOT_DRUG.search(r.title)
    )


def rank(records: list[pubmed.PubmedRecord]) -> list[pubmed.PubmedRecord]:
    def key(r):
        types = r.fields.get("publication_types") or []
        randomized = "Randomized Controlled Trial" in types
        return (TIER_RANK[tier(types)], not randomized, -r.source_date.toordinal(), r.pmid)

    return sorted(records, key=key)


def select(records, exclude: set[str], n: int) -> list[pubmed.PubmedRecord]:
    latest: dict[str, pubmed.PubmedRecord] = {}
    for r in records:
        if r.id not in exclude and eligible(r):
            if r.id not in latest or r.source_date > latest[r.id].source_date:
                latest[r.id] = r
    return rank(list(latest.values()))[:n]


def as_set(chosen: list[pubmed.PubmedRecord], n: int) -> dict:
    return {
        "name": NAME,
        "rule": (
            f"PubMed COPD store, source_date {START} to {END}; publication type is a clinical"
            " trial; COPD in title, MeSH or abstract; title or MeSH names a drug or drug class;"
            " herbal, supplement, device, technique, rehabilitation, screening and service"
            " studies excluded by title; the 50 papers of copd-drug-trials-2023-2024 and its"
            " hand exclusions left out. Ranked Phase III, Phase II, multicenter, other; then"
            f" randomized first; then newest first. Top {n}. Built by ingest/paperset.py."
        ),
        "excluded_by_hand": [],
        "papers": [
            {
                "id": r.id,
                "source_date": r.source_date.isoformat(),
                "tier": tier(r.fields.get("publication_types") or []),
                "title": r.title,
                "journal": r.fields.get("journal"),
                "publication_types": r.fields.get("publication_types") or [],
            }
            for r in chosen
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--n", type=int, default=200)
    args = parser.parse_args(argv)
    first = json.loads(FIRST.read_text())
    exclude = {p["id"] for p in first["papers"]} | set(first.get("excluded_by_hand") or [])
    records = []
    # the months pubmed.py stores; a later month can list a paper dated in the window that
    # pubmed.py would never store
    months = pubmed.months(f"{START:%Y-%m}", f"{END:%Y-%m}")
    files = [p for y, m in months if (p := pubmed.RAW_DIR / f"{y}-{m:02d}.xml.gz").exists()]
    for path in files:
        records += [
            r
            for r in pubmed.parse_xml(gzip.decompress(path.read_bytes()))
            if in_window(r.source_date)
        ]
    chosen = select(records, exclude, args.n)
    OUT.write_text(json.dumps(as_set(chosen, args.n), indent=1, ensure_ascii=False) + "\n")
    tiers: dict[str, int] = {}
    for r in chosen:
        t = tier(r.fields.get("publication_types") or [])
        tiers[t] = tiers.get(t, 0) + 1
    print(
        f"{len(files)} month files, {len({r.id for r in records})} papers in 2023-2025,"
        f" {len(chosen)} chosen: {tiers}; written to {OUT.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
