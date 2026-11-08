"""Store schema checks (person 1): rerunnable schema, versioned documents, paid tables kept."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "src/frontier/store/schema.sql").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()

PAID_TABLES = {"extractions", "llm_cache", "llm_calls"}


def _table(name: str) -> str:
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {name} \((.*?)\);", SCHEMA, re.S)
    assert match, f"table {name} missing"
    return match.group(1)


def test_schema_is_rerunnable():
    creates = re.findall(r"CREATE TABLE\b(?! IF NOT EXISTS)", SCHEMA)
    assert creates == [], "every CREATE TABLE must use IF NOT EXISTS"


def test_documents_are_versioned():
    documents = _table("documents")
    assert "PRIMARY KEY (id, source_date)" in documents
    assert "version_label text NOT NULL" in documents
    assert "fields jsonb" in documents


def test_doc_entities_has_no_fk_to_documents():
    assert "REFERENCES documents" not in _table("doc_entities")


def test_llm_cache_table():
    cache = _table("llm_cache")
    assert "cache_key text PRIMARY KEY" in cache
    assert "response text NOT NULL" in cache


def test_reset_db_keeps_paid_tables():
    match = re.search(r"^EVIDENCE_TABLES := (.+)$", MAKEFILE, re.M)
    assert match, "EVIDENCE_TABLES missing from Makefile"
    dropped = {t.strip() for t in match.group(1).split(",")}
    assert dropped == {"doc_entities", "documents", "entities", "programs"}
    assert not dropped & PAID_TABLES
