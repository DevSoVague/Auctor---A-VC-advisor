"""Database retrieval (RAG + tables): sample backend, SQL builder, safety rules. No Docker, no network, 0 tokens."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from p3graph import engine as E  # noqa: E402
from p3graph import tools as T  # noqa: E402
from p3graph.graph import build  # noqa: E402
from agents.analysis.retrieval_db import DatabaseError, build_select, load_mapping, make_db  # noqa: E402
from test_graph import cfg, drive, inp, run  # noqa: E402

AS_OF = date(2021, 1, 1)


class Ctx:  # what a tool sees
    def __init__(self, as_of=AS_OF, retrieval=None):
        self.config = type("C", (), {"as_of": as_of})()
        self.retrieval = retrieval or make_db(as_of)


def test_rag_returns_parent_text_with_doc_id_and_date():
    docs = run(make_db(AS_OF).search("PDE4 inhibitor exacerbations", k=3))
    assert docs and docs[0]["id"] == "chunk:s1:p1" and docs[0]["doc_id"] == "doc:s1"
    assert docs[0]["body"].startswith("Roflumilast") and docs[0]["source_date"] == "2015-03-01"
    assert docs[0]["title"] == "Roflumilast reduces exacerbations in severe COPD"


def test_rag_hides_future_and_retracted_documents():
    db = make_db(AS_OF)
    ids = {d["doc_id"] for d in run(db.search("COPD exacerbations dupilumab biomarker retracted study", k=10))}
    assert "doc:s3" not in ids      # 2023: after the cutoff
    assert "doc:s4" not in ids      # retracted
    assert "doc:s3" in {d["doc_id"] for d in run(make_db(date(2025, 1, 1)).search("dupilumab", k=5))}


def test_get_document_and_date_guard():
    db = make_db(AS_OF)
    assert run(db.doc("doc:s2"))["file_path"] == "files/pubmed/s2.pdf"
    assert run(db.doc("doc:s3")) is None and run(db.doc("doc:s4")) is None
    assert run(db.doc("chunk:s1:p1"))["doc_id"] == "doc:s1"


def test_tables_map_to_agent_shapes():
    db = make_db(AS_OF)
    assert [d["name"] for d in run(db.drugs(mechanism="PDE4"))] == ["roflumilast"]
    assert all(d["name"] != "dupilumab" for d in run(db.drugs()))
    assert run(db.targets("EFO_0000341"))[0]["symbol"] in ("PDE4B", "IL5RA")
    assert run(db.trials())[0]["id"] == "NCT-SAMPLE-1" and run(db.retractions())[0]["doc_id"] == "doc:s4"


def test_query_table_tool_returns_one_citable_doc():
    out = run(T.REGISTRY["query_table"](Ctx(date(2025, 1, 1)), {}, None, table="market_stats",
                                         where={"drug_id": "drug:roflumilast"}))
    assert len(out) == 1 and out[0]["id"].startswith("tool:") and "annual_cost_usd" in out[0]["body"]
    assert out[0]["source_date"] == "2018-01-01"
    assert run(T.REGISTRY["query_table"](Ctx(AS_OF), {}, None, table="market_stats",
                                         where={"drug_id": "drug:dupilumab"})) == []  # 2024 row is hidden in a 2021 run


def test_tools_registered_and_work_without_database_search_mode():
    assert {"rag_search", "get_document", "query_table"} <= set(T.REGISTRY)
    assert run(T.REGISTRY["rag_search"](Ctx(), {}, None, query="benralizumab IL5RA"))[0]["doc_id"] == "doc:s2"


def test_only_mapped_tables_and_columns_are_readable():
    db = make_db(AS_OF)
    with pytest.raises(DatabaseError, match="unknown table"):
        run(db.query_table("users"))
    with pytest.raises(DatabaseError, match="not readable"):
        run(db.query_table("drugs", where={"password": "x"}))
    with pytest.raises(DatabaseError, match="not readable"):
        run(db.query_table("drugs", columns=["name; DROP TABLE drugs"]))


def test_sql_builder_is_parameterised_and_date_limited():
    sql, params = build_select(load_mapping(), "drugs", where={"status": "shelved", "drug_id": ["a", "b"]},
                               ilike={"pathway": "IL5'; DROP TABLE x;--"}, before=AS_OF, limit=999)
    assert "DROP" not in sql and sql.count("%s") == len(params)
    assert "as_of_date < %s" in sql and "= ANY(%s)" in sql and params[-1] == 200  # limit is capped
    assert params[-2] == AS_OF and "%IL5'; DROP TABLE x;--%" in params


def test_live_backend_without_config_says_what_is_missing(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "live")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("MILVUS_URI", raising=False)
    with pytest.raises(DatabaseError, match="POSTGRES_URL, MILVUS_URI"):
        make_db(AS_OF)


def test_search_source_options_include_database():
    assert {"database", "database+web"} <= set(E.SEARCH_SOURCES)
    assert type(E.make_retrieval(AS_OF, "stub", "database")).__name__ == "DatabaseRetrieval"
    assert type(E.make_retrieval(AS_OF, "stub", "database+web")).__name__ == "WithWebSearch"


def test_database_evidence_reaches_the_agents_pack():
    """The stub model replays answers that cite the OLD corpus ids, so a stub run on the sample database stops at
    A1's citation check. That is correct: it proves the database evidence is what the agent is held to. A full
    run on the database needs a live model."""
    g = build(InMemorySaver())
    with pytest.raises(E.a1_disease.A1Error, match="needs a cited claim"):
        run(drive(g, {**inp(), "search": "database"}, cfg()))
