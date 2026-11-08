"""Marketed COPD drugs (person 1): label parsing, COPD check, Part D stats, versions."""

import csv
import json
from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import fda

FIX = fda.REPO_ROOT / "tests/fixtures/ingest"
LABELS = json.loads((FIX / "fda_labels.json").read_text())
CMS = list(csv.DictReader((FIX / "cms_part_d_sample.csv").open()))


def test_label_query_stops_at_2025():
    params = fda.label_params("OHTUVAYRE")
    assert "effective_time:[20000101 TO 20251231]" in params["search"]
    assert params["sort"] == "effective_time:desc"


def test_cms_stats_use_overall_rows_and_sum_brand_forms():
    daliresp = fda.cms_stats(CMS, "Daliresp")
    assert daliresp["brand_rows"] == ["Daliresp"]  # the per-manufacturer row is not double counted
    assert daliresp["total_spending_usd"] == 1_000_000
    assert daliresp["total_claims"] == 2000
    assert daliresp["avg_spend_per_dosage_unit_usd"] == 20.0
    dupixent = fda.cms_stats(CMS, "Dupixent")
    assert dupixent["brand_rows"] == ["Dupixent Pen", "Dupixent Syringe"]
    assert dupixent["total_spending_usd"] == 4_000_000
    assert dupixent["avg_spend_per_claim_usd"] == 10_000.0
    assert "avg_spend_per_dosage_unit_usd" not in dupixent  # not summable across forms
    assert fda.cms_stats(CMS, "Ohtuvayre") is None


def test_build_checks_copd_and_dates_versions():
    rows, report = fda.build(LABELS, CMS)
    assert any("DUPIXENT: label indications don't mention COPD" in r for r in report)
    assert any("NUCALA: no label" in r for r in report)
    by_id = {}
    for row in rows:
        by_id.setdefault(row["id"], []).append(row)
    # One row per drug. 0401: 2024 label, no Part D row, no industry source in the window.
    assert [r["version_label"] for r in by_id["fda:NDAFIXTURE0401"]] == ["fda-label"]
    assert by_id["fda:NDAFIXTURE0401"][0]["source_date"] == date(2024, 6, 26)
    assert "inhibits fixture enzymes" in by_id["fda:NDAFIXTURE0401"][0]["body"]
    (roflu,) = by_id["fda:NDAFIXTURE0403"]  # the 2020 label-only version is not kept
    assert (roflu["version_label"], roflu["source_date"]) == ("fda-label+cms", date(2025, 5, 31))
    assert roflu["fields"]["medicare_part_d"]["total_claims"] == 2000
    assert "Medicare Part D 2023" in roflu["body"]
    assert roflu["fields"]["industry_sources"] == []  # its 2017 news headline is outside 2023-2025
    assert "[news, 2017-06-27]" not in roflu["body"]


def test_industry_sources_are_typed_dated_and_pre_2026():
    drugs = json.loads(fda.DRUGS.read_text())["drugs"]
    sources = [s for d in drugs for s in d.get("industry_sources", [])]
    assert sources
    for s in sources:
        assert s["type"] in {"company", "regulatory_filing", "news"}
        assert date.fromisoformat(s["date"]) <= date(2025, 12, 31)
        assert s["fact"] and (s.get("url") or s.get("id"))


def test_industry_only_drug_gets_market_version():
    label = fda.parse_label(LABELS["OHTUVAYRE"])
    industry = [
        {
            "type": "news",
            "date": "2024-07-01",
            "title": "t",
            "url": "https://x.invalid",
            "fact": "f",
        }
    ]
    rows = fda.rows_for(label, None, "class", industry)
    assert [(r["version_label"], r["source_date"]) for r in rows] == [
        ("fda-label+market", date(2024, 7, 1)),
    ]


def _documents_versioned() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'documents' AND column_name = 'version_label'"
            ).fetchone()
            return row is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _documents_versioned(), reason="needs Postgres with the versioned schema")
def test_upsert_is_idempotent_and_rolled_back():
    rows, _ = fda.build(LABELS, CMS)
    with psycopg.connect(Settings().database_url) as conn:
        try:
            fda.upsert(conn, rows)
            fda.upsert(conn, rows)
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE id LIKE 'fda:%FIXTURE%'"
            ).fetchone()[0]
            assert count == 2  # one row per drug
        finally:
            conn.rollback()  # never leave fixture rows in a real store
