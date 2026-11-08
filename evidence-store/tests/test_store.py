"""Integration tests against the loaded store (DATABASE_URL). Skipped if Postgres is not reachable.

    conda run -n hack python -m pytest tests -q
    EVSTORE_LIVE=1 ...   also runs the RAG test that embeds a query on Nebius
"""

from __future__ import annotations

import asyncio
import os
from datetime import date

import pytest

from evstore.retrieval import StoreClient

try:
    from evstore.db import connect

    connect().close()
except Exception as e:  # pragma: no cover
    pytest.skip(f"Postgres not reachable: {e}", allow_module_level=True)

CUT = date(2025, 1, 1)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def early():
    c = StoreClient(CUT, use_vectors=False)
    yield c
    c.close()


@pytest.fixture(scope="module")
def late():
    c = StoreClient(date(2026, 1, 1), use_vectors=False)
    yield c
    c.close()


def _dates(rows: list[dict], *keys: str):
    for r in rows:
        for k in keys:
            if r.get(k) is not None:
                yield k, r[k]


def test_no_lookup_leaks_past_as_of(early):
    checks = {
        "drug_table": (run(early.drug_table()), ("as_of_date",)),
        "market": (run(early.market()), ("published_date",)),
        "shelved": (run(early.shelved()), ("published_date",)),
        "trial_table": (run(early.trial_table()), ("published_date",)),
        "findings": (run(early.findings()), ("published_date",)),
        "papers_for_drug": (run(early.papers_for_drug("dupilumab")), ("published_date",)),
        "labels": (run(early.labels()), ("published_date",)),
        "label_events": (run(early.label_events()), ("published_date",)),
        "trial_links": (run(early.trial_links()), ("published_date",)),
    }
    for name, (rows, keys) in checks.items():
        for k, d in _dates(rows, *keys):
            assert d < CUT, f"{name}.{k} = {d} is not before {CUT}"


def test_no_record_leaks_past_as_of(early):
    recs = run(early.trials("COPD")) + run(early.approvals("COPD")) + run(early.drugs())
    recs += run(early.search("dupilumab exacerbations", k=20))
    assert recs
    assert all(r.source_date < CUT for r in recs)


def test_rag_text_only_respects_as_of(early):
    hits = run(early.rag("ensifentrine exacerbation rate", k=10))
    assert hits and all(h.source_date < CUT for h in hits)
    assert any("ensifentrine" in (h.title or "").lower() for h in hits)


def test_shelved_programs_appear_only_once_reported(early, late):
    assert {r["name"] for r in run(late.shelved())} >= {"astegolimab", "itepekimab"}  # reported 2025
    assert not {r["name"] for r in run(early.shelved())} & {"astegolimab", "itepekimab"}


def test_versioned_trial_returns_latest_visible_version():
    c = StoreClient(date(2024, 6, 1), use_vectors=False)
    assert run(c.doc("nct:NCT06114238")).fields["version_label"] == "ctgov-registration"
    c.close()
    c = StoreClient(date(2025, 6, 1), use_vectors=False)
    assert run(c.doc("nct:NCT06114238")).fields["version_label"] == "ctgov-live"
    c.close()


def test_one_row_per_trial(late):
    ids = [r["trial_id"] for r in run(late.trial_table())]
    assert len(ids) == len(set(ids))


def test_protocol_methods_return_data(late):
    assert run(late.trials("COPD"))
    assert run(late.approvals("COPD"))  # FDA labels carry an indication now
    assert run(late.drugs(mechanism="LAMA"))
    paper = run(late.doc("pmid:37364283"))  # ensifentrine ENHANCE, has full text
    assert paper and paper.fields.get("full_text")


def test_label_events_are_verified(late):
    rows = run(late.label_events())
    assert rows and all(r["term_verified"] for r in rows if r["kind"] == "population_caution")


def test_every_chunk_is_embedded_and_points_at_a_document():
    conn = connect()
    assert conn.execute("SELECT count(*) FROM chunks WHERE embedding IS NULL").fetchone()[0] == 0
    orphans = conn.execute(
        "SELECT count(*) FROM chunks c WHERE NOT EXISTS "
        "(SELECT 1 FROM documents d WHERE d.id = c.doc_id AND d.source_date = c.doc_version)").fetchone()[0]
    assert orphans == 0
    conn.close()


@pytest.mark.skipif(not os.environ.get("EVSTORE_LIVE"), reason="set EVSTORE_LIVE=1 to embed a query on Nebius")
def test_rag_hybrid_live():
    c = StoreClient(CUT)
    hits = run(c.rag("anti-IL-33 antibody in COPD", k=6))
    assert hits and all(h.source_date < CUT for h in hits)
    assert any(h.vector_rank for h in hits)
    assert hits[0].doc_id == "pmid:38115209"  # tozorakimab phase 1
    c.close()
