"""The second, larger COPD drug-trial paper set (person 1): rule, ranking, set file."""

from datetime import date

from frontier.ingest import paperset
from frontier.ingest.pubmed import PubmedRecord


def rec(pmid, when, title, types, mesh=(), body="Made-up COPD abstract."):
    return PubmedRecord(
        pmid=pmid,
        source_date=when,
        title=title,
        body=body,
        fields={"publication_types": list(types), "mesh_terms": list(mesh), "journal": "J Fix"},
    )


RCT = ["Randomized Controlled Trial", "Journal Article"]
P3 = ["Clinical Trial, Phase III", "Randomized Controlled Trial"]
RECORDS = [
    rec(
        "FIXTURE-1",
        date(2024, 5, 1),
        "Fixturumab in COPD exacerbations",
        RCT,
        mesh=["Antibodies, Monoclonal, Humanized"],
    ),
    rec("FIXTURE-2", date(2025, 3, 1), "Tiotropium versus placebo in COPD", P3),
    rec("FIXTURE-3", date(2024, 1, 1), "Herbal decoction plus tiotropium in COPD", RCT),
    rec("FIXTURE-4", date(2024, 1, 1), "Budesonide in COPD: a cohort study", ["Journal Article"]),
    rec("FIXTURE-5", date(2022, 12, 31), "Roflumilast in COPD", P3),  # before the window
    rec(
        "FIXTURE-6",
        date(2024, 6, 1),
        "Ensifentrine in COPD",
        ["Clinical Trial", "Multicenter Study"],
    ),
    rec("FIXTURE-7", date(2024, 7, 1), "Dupilumab in COPD", P3),  # in the first set
    rec(
        "FIXTURE-8",
        date(2024, 8, 1),
        "Exercise training in COPD",
        RCT,
        mesh=["Bronchodilator Agents"],
    ),
    rec(
        "FIXTURE-9", date(2024, 8, 1), "Montelukast in asthma", RCT, body="Made-up asthma abstract."
    ),
]


def test_select_applies_the_rule_and_ranks():
    chosen = paperset.select(RECORDS, {"pmid:FIXTURE-7"}, n=10)
    assert [r.pmid for r in chosen] == ["FIXTURE-2", "FIXTURE-6", "FIXTURE-1"]
    # Phase III first, then multicenter, then other; herbal, cohort, pre-2023, first-set,
    # exercise and non-COPD papers left out
    assert paperset.select(RECORDS, set(), n=1)[0].pmid == "FIXTURE-2"  # newest Phase III


def test_set_file_has_the_curated_shape():
    s = paperset.as_set(paperset.select(RECORDS, set(), n=2), n=2)
    assert s["name"] == "copd-drug-trials-2023-2025-more"
    assert [p["id"] for p in s["papers"]] == ["pmid:FIXTURE-2", "pmid:FIXTURE-7"]
    assert s["papers"][0]["tier"] == "Phase III" and s["papers"][0]["source_date"] == "2025-03-01"
    assert "Top 2" in s["rule"]


def test_tiers():
    assert paperset.tier(P3) == "Phase III"
    assert paperset.tier(["Clinical Trial, Phase II"]) == "Phase II"
    assert paperset.tier(["Clinical Trial", "Multicenter Study"]) == "Multicenter trial"
    assert paperset.tier(RCT) == "Other trial"
