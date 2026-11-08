"""Evidence-store tools: date window, access matrix, stub tool loop. Live-DB tests skip when the
evidence-store container (localhost:5433) is not running. 0 LLM tokens; search_evidence embeds the
query once per call unless EVSTORE_VECTORS=0 (set below)."""

from __future__ import annotations

import asyncio
import os
from datetime import date

import pytest

os.environ.setdefault("EVSTORE_VECTORS", "0")  # keyword search only: no Nebius call in tests

from agents.analysis._common import EvidencePack  # noqa: E402
from agents.analysis.context import StubLLM  # noqa: E402
from agents.analysis.db_tools import ACCESS, FLOOR, READ, ToolExecutor, in_window, tools_for  # noqa: E402
from agents.analysis.tool_loop import final_messages, gather  # noqa: E402


def _db_up() -> bool:
    try:
        from agents.analysis.db_tools import store_client

        store_client(date(2026, 1, 1))
        return True
    except Exception:  # noqa: BLE001
        return False


live = pytest.mark.skipif(not _db_up(), reason="evidence-store DB not reachable on :5433")


def run(c):
    return asyncio.run(c)


def test_window():
    assert FLOOR == date(2023, 1, 1)
    assert not in_window("2022-12-31", date(2026, 1, 1))
    assert in_window("2023-01-01", date(2026, 1, 1))
    assert not in_window("2025-01-01", date(2025, 1, 1))
    assert not in_window(None, date(2026, 1, 1))


def test_access_matrix():
    names = lambda a: {t["function"]["name"] for t in tools_for(a)}  # noqa: E731
    assert "web_search" in names("a1_disease") and "web_search" in names("a4_inactive") and "web_search" in names("a5_active")
    for a in ("a2_mechanism", "a3_profile", "a6_redteam"):
        assert "web_search" not in names(a)
    assert tools_for("a6b_judge") == []
    assert set(ACCESS["a6_redteam"]) == set(READ)


def test_refuses_tool_outside_access():
    ex = ToolExecutor("a6_redteam", date(2026, 1, 1))
    r = run(ex("web_search", {"query": "x"}))
    assert "not available" in r["error"]
    assert ex.log[-1]["error"]


@live
@pytest.mark.parametrize("as_of", [date(2026, 1, 1), date(2024, 1, 1)])
def test_every_result_is_dated_inside_window(as_of):
    pack = EvidencePack(as_of)
    ex = ToolExecutor("a6_redteam", as_of, pack)
    for name, args in [("search_evidence", {"query": "itepekimab exacerbations", "k": 5}),
                       ("list_trials", {"phase": "PHASE3"}), ("list_drugs", {}), ("market_stats", {}),
                       ("shelved_programs", {}), ("paper_findings", {}), ("label_events", {})]:
        r = run(ex(name, args))
        assert "error" not in r, (name, r)
        for item in r["results"]:
            assert FLOOR.isoformat() <= item["published"] < as_of.isoformat(), (name, item["id"])
            assert item["date_kind"] and item["age_days"] > 0
    assert all(FLOOR.isoformat() <= v["source_date"] < as_of.isoformat() for v in pack.items.values())


@live
def test_backtest_hides_later_records():
    ex = ToolExecutor("a3_profile", date(2024, 1, 1))
    assert run(ex("market_stats", {}))["count"] == 0  # market data is all dated 2025
    assert "outside" in run(ex("get_document", {"id": "fda:NDA206756"})).get("error", "") or \
        "no record" in run(ex("get_document", {"id": "fda:NDA206756"})).get("error", "")


@live
def test_published_bounds_narrow_the_window():
    ex = ToolExecutor("a2_mechanism", date(2026, 1, 1))
    r = run(ex("search_evidence", {"query": "COPD exacerbation", "k": 10, "published_after": "2024-06-01"}))
    assert r["window"]["from"] == "2024-06-01"
    assert all(i["published"] >= "2024-06-01" for i in r["results"])


@live
def test_stub_gather_adds_to_pack_and_final_messages():
    as_of = date(2026, 1, 1)
    llm = StubLLM()
    llm.tool_script = {"a3_profile": [("list_drugs", {"status": "in_market"}), ("market_stats", {})]}
    pack = EvidencePack(as_of)
    ex = ToolExecutor("a3_profile", as_of, pack)
    g = run(gather(llm, agent="a3_profile", messages=[{"role": "user", "content": "x"}], executor=ex,
                   tools=tools_for("a3_profile")))
    assert len(g["calls"]) == 2 and g["new_ids"]
    msgs = final_messages([{"role": "user", "content": "x"}], g, pack)
    assert len(msgs) == 2 and "published 2025" in msgs[-1]["content"]


# ----------------------------------------------------------------------------- A1 ingest (no network)
import xml.etree.ElementTree as ET  # noqa: E402

from agents.analysis.db_ingest import Ingestor, _pubmed_date, norm_url, page_date  # noqa: E402


def test_date_parsing():
    assert _pubmed_date(ET.fromstring("<d><Year>2025</Year><Month>Jul</Month><Day>9</Day></d>")) == (date(2025, 7, 9), "day")
    assert _pubmed_date(ET.fromstring("<d><Year>2024</Year><Month>Mar</Month></d>"))[1] == "month"
    assert _pubmed_date(ET.fromstring("<d><Year>2024</Year></d>"))[1] == "year"
    assert page_date('<meta name="citation_publication_date" content="2024/05/17">')[:2] == (date(2024, 5, 17), "day")
    assert page_date("<html>no date</html>")[0] is None
    assert norm_url("https://www.x.org/a/?q=1#h") == norm_url("http://x.org/a")


def _item(did="pmid:99999999", d=date(2025, 3, 3), prec="day", body="B" * 400, title="A brand new COPD trial"):
    return {"id": did, "source": "pubmed", "source_date": d, "title": title, "body": body, "url": f"https://x/{did}",
            "full_text": None, "sections": [], "version_label": "pubmed-a1",
            "fields": {"date_source": "test", "date_precision": prec}}


@live
@pytest.mark.parametrize("item,status,why", [
    (_item(d=None, prec="none"), "rejected", "no reliable publication date"),
    (_item(prec="year"), "rejected", "no reliable publication date"),
    (_item(d=date(2021, 5, 1)), "rejected", "outside"),
    (_item(d=date(2026, 2, 1)), "rejected", "outside"),
    (_item(body="short"), "rejected", "too little"),
    (_item(did="pmid:36681255", d=date(2023, 1, 18)), "duplicate", "already stored"),
])
def test_ingest_rules(item, status, why):
    ing = Ingestor(date(2026, 1, 1), dry_run=True)
    ing.fetched["f1"] = item
    out = run(ing.ingest_document("f1"))
    assert out["status"] == status and why in out["reason"], out


@live
def test_ingest_stores_text_verbatim_dry_run():
    body = "BACKGROUND: verbatim test ✓ — exact bytes.\n" + "x" * 300
    ing = Ingestor(date(2026, 1, 1), dry_run=True)
    ing.fetched["f1"] = _item(body=body)
    seen = {}
    orig = ing._q

    def spy(sql, params=()):
        if sql.lstrip().startswith("INSERT INTO documents"):
            seen["body"] = params[5]
        return orig(sql, params)

    ing._q = spy
    out = run(ing.ingest_document("f1"))
    assert out["status"] == "dry_run" and seen["body"] == body
    assert not run(ing.check_exists(pmid="99999999"))["exists"]  # dry run rolled back


def test_ingest_unknown_fetch_id():
    out = run(Ingestor(date(2026, 1, 1), dry_run=True).ingest_document("nope"))
    assert out["status"] == "rejected"


# ----------------------------------------------------------------------------- A6b judge (stub)
import json as _json  # noqa: E402

from agents.analysis import a6b_judge  # noqa: E402
from agents.analysis.context import RunContext  # noqa: E402
from agents.analysis.schemas import JudgeDecision, ProductProfile, RunConfig, Verdict  # noqa: E402
from agents.analysis.context import FIXTURES as FIX  # noqa: E402


@pytest.mark.parametrize("hid", ["h1", "h2", "h3", "h4", "h5"])
def test_a6b_judge_sees_only_a3_and_a6(hid):
    profile = ProductProfile.model_validate(_json.loads((FIX / "a3_profile_out.json").read_text())[hid])
    verdict = Verdict.model_validate(_json.loads((FIX / "a6_redteam_out.json").read_text())[hid])
    llm = StubLLM()
    cfg = RunConfig(disease="COPD", disease_id="EFO_0000341", as_of=date(2021, 1, 1), mode="single_shot", profile="dev")
    ctx = RunContext(config=cfg, retrieval=None, llm=llm)
    out = run(a6b_judge.run(profile, verdict, ctx))
    assert isinstance(out, JudgeDecision) and out.hypothesis_id == hid
    assert len(out.rulings) == len(verdict.risks)
    sent = _json.loads(llm.calls[0]["messages"][1]["content"].split("INPUT:\n", 1)[1])
    assert set(sent) == {"hypothesis_id", "product_profile", "red_team"}  # nothing else reaches the judge
