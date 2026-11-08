"""Open Targets 24.09 COPD associations (person 1): listing, filtering, rows, storage."""

from datetime import date

import httpx
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from frontier.core.config import Settings
from frontier.ingest import opentargets as ot

OVERALL = [
    {"diseaseId": ot.DISEASE, "targetId": "ENSGFIXTURE1", "score": 0.62, "evidenceCount": 40},
    {"diseaseId": ot.DISEASE, "targetId": "ENSGFIXTURE2", "score": 0.05, "evidenceCount": 2},
    {"diseaseId": ot.DISEASE, "targetId": "ENSGFIXTURE3", "score": 0.03, "evidenceCount": 1},
]
GENETIC = [
    {
        "diseaseId": ot.DISEASE,
        "targetId": "ENSGFIXTURE1",
        "datatypeId": "genetic_association",
        "score": 0.5,
    }
]
TARGETS = [
    {
        "id": "ENSGFIXTURE1",
        "approvedSymbol": "FIXA",
        "approvedName": "fixture receptor A",
        "tractability": [
            {"modality": "SM", "id": "Approved Drug", "value": True},
            {"modality": "AB", "id": "UniProt loc high conf", "value": False},
        ],
    },
    {
        "id": "ENSGFIXTURE2",
        "approvedSymbol": "FIXB",
        "approvedName": "fixture B",
        "tractability": [],
    },
]
DRUGS = [
    {"diseaseId": ot.DISEASE, "targetId": "ENSGFIXTURE2", "drugId": "CHEMBLFIX", "phase": 2.0},
    {"diseaseId": ot.DISEASE, "targetId": "ENSGFIXTURE2", "drugId": "CHEMBLFIX2", "phase": 3.0},
]


def test_part_files_from_a_listing():
    html = '<a href="part-00001-x.snappy.parquet">a</a><a href="part-00000-x.snappy.parquet">b</a>'
    html += '<a href="_SUCCESS">c</a><a href="part-00000-x.snappy.parquet">dup</a>'
    assert ot.part_files(html) == ["part-00000-x.snappy.parquet", "part-00001-x.snappy.parquet"]


def test_build_keeps_high_scores_and_targets_with_a_copd_drug():
    rows = ot.build(OVERALL, GENETIC, TARGETS, DRUGS, min_score=0.1)
    assert [r["target_id"] for r in rows] == ["ENSGFIXTURE1", "ENSGFIXTURE2"]  # 3: low, no drug
    a, b = rows
    assert (a["symbol"], a["genetic_score"], a["tractability"]) == (
        "FIXA",
        0.5,
        ["SM: Approved Drug"],
    )
    assert a["evidence_types"] == ["genetic_association"] and a["max_phase"] is None
    assert (b["max_phase"], b["genetic_score"], b["association_score"]) == (3.0, None, 0.05)
    assert a["as_of_date"] == date(2024, 9, 18) and a["release"] == "24.09"


def test_document_is_dated_by_the_release():
    (a, _) = ot.build(OVERALL, GENETIC, TARGETS, DRUGS, min_score=0.1)
    doc = ot.document(a)
    assert doc["id"] == f"ot:{ot.DISEASE}:ENSGFIXTURE1"
    assert doc["source_date"] == date(2024, 9, 18) and doc["version_label"] == "ot-24.09"
    assert doc["fields"]["disease_id"] == ot.DISEASE  # what the retrieval stub's targets() reads
    assert "FIXA" in doc["title"] and "Genetic association score: 0.5" in doc["body"]


def test_filtered_downloads_parts_filters_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(ot, "RAW_DIR", tmp_path / "cache")
    parts = {}
    for i, chunk in enumerate(
        [OVERALL[:2], OVERALL[2:] + [{**OVERALL[0], "diseaseId": "EFO_OTHER"}]]
    ):
        buf = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist(chunk), buf)
        parts[f"part-0000{i}-fix.snappy.parquet"] = buf.getvalue().to_pybytes()
    calls = []

    def site(request):
        calls.append(request.url.path)
        name = request.url.path.rsplit("/", 1)[-1]
        if name in parts:
            return httpx.Response(200, content=parts[name])
        listing = "".join(f'<a href="{n}">{n}</a>' for n in parts)
        return httpx.Response(200, text=listing)

    client = httpx.Client(transport=httpx.MockTransport(site))
    cols = ["diseaseId", "targetId", "score", "evidenceCount"]
    rows = ot._filtered(
        client, "associationByOverallDirect", cols, [("diseaseId", "=", ot.DISEASE)]
    )
    assert sorted(r["targetId"] for r in rows) == ["ENSGFIXTURE1", "ENSGFIXTURE2", "ENSGFIXTURE3"]
    n = len(calls)
    assert ot._filtered(client, "associationByOverallDirect", cols, []) == rows  # from the cache
    assert len(calls) == n


def _targets_ready() -> bool:
    try:
        with psycopg.connect(Settings().database_url, connect_timeout=2) as conn:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'targets' AND column_name = 'release'"
            ).fetchone()
            return row is not None
    except psycopg.Error:
        return False


@pytest.mark.skipif(not _targets_ready(), reason="needs Postgres with the Open Targets columns")
def test_write_fills_targets_and_documents_rolled_back():
    rows = ot.build(OVERALL, GENETIC, TARGETS, DRUGS, min_score=0.1)
    with psycopg.connect(Settings().database_url) as conn:
        try:
            ot.write(conn, rows)
            ot.write(conn, rows)  # rerunnable
            t = conn.execute(
                "SELECT target_id, tractability, max_phase FROM targets"
                " WHERE target_id LIKE 'ENSGFIXTURE%' ORDER BY target_id"
            ).fetchall()
            assert [(r[0], r[1], float(r[2]) if r[2] is not None else None) for r in t] == [
                ("ENSGFIXTURE1", ["SM: Approved Drug"], None),
                ("ENSGFIXTURE2", [], 3.0),
            ]
            d = conn.execute(
                "SELECT count(*) FROM documents"
                " WHERE source = 'opentargets' AND id LIKE '%FIXTURE%'"
            ).fetchone()[0]
            assert d == 2
        finally:
            conn.rollback()
