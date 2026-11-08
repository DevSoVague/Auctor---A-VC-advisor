"""Agents 4 and 5 over the real evidence store: the shapes the store returns reach them intact.

Before this, the store's curated drugs (source 'curated') were dropped by the Record model, trial
interventions arrived as a 300-character string, and FDA labels had no drug name, so A4/A5 saw no
assets at all on store data. These rows are shaped like the real ones (no database needed).
"""

from __future__ import annotations

from datetime import date

from agents.analysis.db_tools import _record_to_ev
from agents.competitor.common import Mechanism, collect_assets
from agents.competitor.context import RecordAdapter


def rec(id: str, source: str, d: date, title: str, **fields) -> dict:
    return {"id": id, "source": source, "source_date": d, "title": title, "body": "", "url": None,
            "fields": fields}


DRUGS = [
    rec("drug:itepekimab", "curated", date(2025, 7, 9), "itepekimab", pathway="Anti-IL-33 antibody",
        drug_class=None, brand_names=[], targets=[], status="shelved"),
    rec("drug:roflumilast", "curated", date(2023, 1, 1), "roflumilast", pathway="PDE4 inhibitor",
        drug_class="PDE4 inhibitor", brand_names=["Daliresp"], targets=[], status="in_market"),
]
TRIAL = rec("nct:NCT1", "ctgov", date(2024, 5, 1), "A COPD trial", phase="PHASE3", status="RECRUITING",
            sponsor="Sanofi", sponsor_class="INDUSTRY",
            interventions=[{"name": "Itepekimab", "type": "DRUG", "other_names": ["SAR440340"]}])
LABEL = rec("fda:NDA022522", "fda", date(2023, 1, 1), "DALIRESP (ROFLUMILAST): FDA label",
            generic="ROFLUMILAST", brand="Daliresp", manufacturer="AstraZeneca")


def adapter_records(rows: list[dict]):
    return [RecordAdapter._rec(_record_to_ev(r)) for r in rows]


def test_curated_drugs_reach_agents_4_and_5_as_chembl_drugs():
    drugs = adapter_records(DRUGS)
    assert all(d is not None and d.source == "chembl" for d in drugs)
    ite = drugs[0]
    assert ite.fields["name"] == "itepekimab"
    assert ite.fields["mechanism"] == "Anti-IL-33 antibody"
    assert ite.fields["molecule_type"] == "antibody"
    assert ite.fields["development_status"] == "shelved"
    assert drugs[1].fields["synonyms"] == ["Daliresp"]


def test_trial_interventions_and_sponsor_survive_the_wrapper():
    (trial,) = adapter_records([TRIAL])
    assert trial.fields["interventions"][0]["name"] == "Itepekimab"
    assert trial.fields["sponsor"] == "Sanofi" and trial.fields["sponsor_class"] == "INDUSTRY"


def test_fda_label_names_its_drug():
    (label,) = adapter_records([LABEL])
    assert label.fields["drug"] == "ROFLUMILAST" and label.fields["manufacturer"] == "AstraZeneca"


def test_assets_are_formed_from_store_rows_and_placed_on_their_mechanism():
    drugs = adapter_records(DRUGS)
    (trial,) = adapter_records([TRIAL])
    (label,) = adapter_records([LABEL])
    assets = {c.name: c for c in collect_assets([trial], [label], drugs)}

    assert assets["itepekimab"].stage == "phase3" and assets["itepekimab"].drug is not None
    assert assets["roflumilast"].stage == "approved"
    il33 = Mechanism.of("IL-33 / ST2 alarmin blockade", [])
    assert il33.place(assets["itepekimab"]) == "on"
    assert il33.place(assets["roflumilast"]) != "on"
