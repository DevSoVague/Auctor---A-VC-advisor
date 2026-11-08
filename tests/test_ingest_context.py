"""COPD market context (person 1): consulting and analyst facts, typed and pre-2026."""

from datetime import date

from frontier.ingest import context


def test_context_facts():
    records = context.rows()
    assert records
    for r in records:
        f = r["fields"]
        assert f["type"] in {"consulting", "analyst"}
        assert r["source_date"] <= date(2025, 12, 31)
        assert r["id"].startswith("curated:context:") and f["url"].startswith("https://")
    assert any("PwC" in r["fields"]["fact"] for r in records)
