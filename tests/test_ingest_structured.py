"""Structured tables (person 1): drugs, market stats, shelved programs, trials."""

import json
from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import ctgov, fda, shelved, structured

FIX = fda.REPO_ROOT / "tests/fixtures/ingest"


def _docs():
    import csv

    labels = json.loads((FIX / "fda_labels.json").read_text())
    cms = list(csv.DictReader((FIX / "cms_part_d_sample.csv").open()))
    fda_rows, _ = fda.build(labels, cms)
    shelved_rows = shelved.rows()
    studies = json.loads((FIX / "ctgov_sample.json").read_text())
    ct_rows = [r for s in studies if ctgov.is_interventional(s) for r in ctgov.versions(s)]
    return fda_rows, shelved_rows, ct_rows


def test_build_tables():
    tables = structured.build(*_docs())
    drugs = {d["drug_id"]: d for d in tables["drugs"]}
    # 9 shelved drugs plus the 2 fixture labels that mention COPD.
    assert len(drugs) == 11
    assert drugs["drug:danirixin"]["status"] == "shelved"
    assert drugs["drug:benralizumab"]["name"] == "benralizumab"
    marketed = drugs["drug:fixture-generic-c"]
    assert marketed["status"] == "in_market" and marketed["brand_names"] == ["FIXTUREBRAND C"]

    stats = {s["drug_id"]: s for s in tables["market_stats"]}
    roflu = stats["drug:fixture-generic-c"]
    assert roflu["period"] == date(2023, 1, 1)
    assert roflu["patients_est"] == 500 and roflu["total_claims"] == 2000
    assert roflu["annual_cost_usd"] == 2000.0
    assert roflu["published_date"] == date(2025, 5, 31)  # the stats version, not the 2020 label
    assert roflu["side_effects"] == ["Made-up: diarrhea, weight decrease."]
    assert roflu["insurance"] == "unknown" and "Medicare Part D 2023" in roflu["coverage_notes"]
    assert roflu["industry_notes"].startswith("[news, 2017-06-27]")
    assert "news:eac662c4fb4f51e7" in roflu["source_ids"]

    programs = {p["drug_id"]: p for p in tables["shelved_programs"]}
    benra = programs["drug:benralizumab"]
    assert benra["shelved_date"] == date(2018, 5, 15)  # first public report
    assert benra["published_date"] == date(2025, 9, 17)  # latest source in the row
    assert benra["phase_reached"] == "phase3" and benra["reason_category"] == "efficacy"

    trials = {(t["trial_id"], t["version_label"]): t for t in tables["trials"]}
    assert len(trials) == 5
    reg = trials[("FIXTURE-0201", "ctgov-registration")]
    assert reg["status"] is None and reg["enrollment"] is None  # nothing from after registration
    live = trials[("FIXTURE-0201", "ctgov-live")]
    assert live["status"] == "COMPLETED" and live["completion_date"] == date(2022, 1, 15)
    assert live["start_date"] == date(2019, 3, 31)


def test_trial_links_to_drug_by_brand_or_single_generic():
    drugs = [
        {"drug_id": "drug:roflumilast", "name": "roflumilast", "brand_names": ["DALIRESP"]},
        {
            "drug_id": "drug:trelegy",
            "name": "fluticasone furoate, umeclidinium and vilanterol",
            "brand_names": ["TRELEGY ELLIPTA"],
        },
    ]
    index = structured.drug_index(drugs)

    def link(name):
        doc = {
            "source_date": date(2020, 1, 1),
            "version_label": "ctgov-live",
            "title": "t",
            "fields": {"nct_id": "N", "interventions": [{"name": name}]},
        }
        return structured.from_ctgov(doc, index)["drug_id"]

    assert link("Roflumilast 500 mcg") == "drug:roflumilast"
    assert link("Trelegy Ellipta") == "drug:trelegy"
    assert link("Fluticasone furoate") is None  # a combination's ingredient alone doesn't link


def _structured_tables_exist() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            return conn.execute("SELECT to_regclass('shelved_programs')").fetchone()[0] is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(
    not _structured_tables_exist(), reason="needs Postgres with the structured tables"
)
def test_write_is_rerunnable_and_rolled_back():
    tables = structured.build(*_docs())
    with psycopg.connect(Settings().database_url) as conn:
        try:
            structured.write(conn, tables)
            counts = structured.write(conn, tables)
            assert counts["drugs"] == 11 and counts["trials"] == 5
            n = conn.execute("SELECT count(*) FROM trials").fetchone()[0]
            assert n == 5
        finally:
            conn.rollback()  # leave the real tables as they were
