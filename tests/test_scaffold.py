"""Scaffold checks: the package imports and the layout matches docs/PRD.md."""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

SUBPACKAGES = [
    "frontier.core",
    "frontier.store",
    "frontier.ingest",
    "frontier.api",
    "frontier.agents",
    "frontier.agents.analysis",
    "frontier.agents.competitor",
    "frontier.agents.citations",
    "frontier.flow",
    "frontier.memo",
    "frontier.eval",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name):
    importlib.import_module(name)


def test_api_health():
    from frontier.api.main import app

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_raw_data_stays_out_of_git():
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert "data/*" in gitignore
    assert ".env" in gitignore


def test_schema_has_as_of_column():
    schema = (ROOT / "src/frontier/store/schema.sql").read_text()
    assert "source_date date NOT NULL" in schema
