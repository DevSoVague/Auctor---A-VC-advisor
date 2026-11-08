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
    # Label after the CMS release? No: 2024 label, May 2025 CMS release, but no Part D row.
    assert [r["version_label"] for r in by_id["fda:NDAFIXTURE0401"]] == ["fda-label"]
    assert by_id["fda:NDAFIXTURE0401"][0]["source_date"] == date(2024, 6, 26)
    assert "inhibits fixture enzymes" in by_id["fda:NDAFIXTURE0401"][0]["body"]
    roflu = by_id["fda:NDAFIXTURE0403"]
    assert [(r["version_label"], r["source_date"]) for r in roflu] == [
        ("fda-label", date(2020, 3, 1)),
        ("fda-label+cms", date(2025, 5, 31)),  # stats only visible from the CMS release
    ]
    assert "medicare_part_d" not in roflu[0]["fields"]
    assert roflu[1]["fields"]["medicare_part_d"]["total_claims"] == 2000
    assert "Medicare Part D 2023" in roflu[1]["body"]


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
            assert count == 3
        finally:
            conn.rollback()  # never leave fixture rows in a real store
