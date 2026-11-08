"""Earlier FDA label versions from DailyMed (person 1): history, SPL parsing, rows, dates."""

import io
import json
import zipfile
from datetime import date

import psycopg
import pytest

from frontier.core.config import Settings
from frontier.ingest import dailymed, fda, structured

FIX = dailymed.REPO_ROOT / "tests/fixtures/ingest"
SPL = (FIX / "dailymed_spl.xml").read_bytes()
HISTORY = json.loads((FIX / "dailymed_history.json").read_text())
CURRENT = {
    "id": "fda:NDAFIXTURE0401",
    "source_date": date(2025, 5, 31),
    "version_label": "fda-label+cms",
    "fields": {
        "brand": "FIXTUREBRAND A",
        "generic": "FIXTURE-GENERIC-A",
        "manufacturer": "Fixture Pharma",
        "application_number": "NDAFIXTURE0401",
        "pharm_class_moa": ["Fixture Inhibitors [MoA]"],
        "set_id": "fixture-set-0401",
        "drug_class": "PDE3/PDE4 inhibitor",
        "label_effective": "2025-04-01",
        "sections": {"indications_and_usage": "Made-up: COPD."},
        "medicare_part_d": {
            "year": 2023,
            "total_beneficiaries": 10,
            "total_claims": 20,
            "total_spending_usd": 300.0,
            "brand_rows": ["Fixturebrand"],
        },
    },
}


def test_versions_parse_dates_and_sort():
    assert dailymed.versions(HISTORY) == [
        (2, date(2021, 9, 26)),
        (4, date(2023, 6, 2)),
        (5, date(2024, 3, 15)),
        (7, date(2025, 11, 3)),
    ]


def test_parse_spl_keeps_sections_and_drops_highlights():
    spl = dailymed.parse_spl(SPL)
    s = spl["sections"]
    assert spl["label_effective"] == "2024-03-01"
    assert s["indications_and_usage"].startswith("1 INDICATIONS AND USAGE Made-up: maintenance")
    assert "highlight" not in s["indications_and_usage"]
    assert s["adverse_reactions"] == (
        "6 ADVERSE REACTIONS Made-up: back pain 3% , headache. "
        "6.1 Clinical Trials Experience Made-up subsection."
    )
    assert s["mechanism_of_action"] == "12.1 Mechanism of Action Made-up: inhibits fixture enzymes."


def test_spl_from_zip_reads_the_xml():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fixture.xml", SPL)
        z.writestr("image.jpg", b"not xml")
    assert dailymed.spl_from_zip(buf.getvalue()) == SPL


def test_rows_keep_window_versions_with_a_copd_indication():
    no_copd = {
        "sections": {"indications_and_usage": "Made-up: asthma only."},
        "label_effective": None,
    }
    spls = {2: dailymed.parse_spl(SPL), 4: no_copd, 5: dailymed.parse_spl(SPL)}
    rows, report = dailymed.rows_for(CURRENT, dailymed.versions(HISTORY), spls)
    assert [(r["version_label"], r["source_date"]) for r in rows] == [
        ("dailymed-v5", date(2024, 3, 15))  # v2 is before 2023; v7 was not downloaded
    ]
    assert report == ["FIXTUREBRAND A v4 (2023-06-02): no COPD indication, skipped"]
    (row,) = rows
    assert row["id"] == "fda:NDAFIXTURE0401"
    assert row["fields"]["spl_version"] == 5 and row["fields"]["set_id"] == "fixture-set-0401"
    assert "medicare_part_d" not in row["fields"]  # market data stays on fda.py's row
    assert "inhibits fixture enzymes" in row["body"]
    assert "version=5" in row["url"]


def test_structured_dates_drug_by_earliest_version_and_keeps_market_row():
    spls = {5: dailymed.parse_spl(SPL)}
    (older,) = dailymed.rows_for(CURRENT, dailymed.versions(HISTORY), spls)[0]
    newer = {**older, "source_date": date(2025, 11, 3), "version_label": "dailymed-v7"}
    tables = structured.build([CURRENT, older, newer], [], [])
    (drug,) = tables["drugs"]
    assert drug["as_of_date"] == date(2024, 3, 15)
    (stat,) = tables["market_stats"]
    assert stat["total_claims"] == 20 and stat["published_date"] == date(2025, 5, 31)


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
def test_fda_rerun_keeps_dailymed_versions_rolled_back():
    rows, _ = dailymed.rows_for(CURRENT, dailymed.versions(HISTORY), {5: dailymed.parse_spl(SPL)})
    with psycopg.connect(Settings().database_url) as conn:
        try:
            fda.upsert(conn, rows)
            fda.remove_stale(conn, [])  # an fda.py rerun that produced no rows
            left = conn.execute(
                "SELECT version_label FROM documents WHERE id = 'fda:NDAFIXTURE0401'"
            ).fetchall()
            assert left == [("dailymed-v5",)]
        finally:
            conn.rollback()
