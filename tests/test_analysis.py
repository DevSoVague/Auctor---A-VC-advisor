"""Person 3 tests. Run: LLM_MODE=stub pytest -q"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from openpyxl import load_workbook

from agents.analysis import a1_disease, a2_mechanism, a3_profile, a6_redteam
from agents.analysis._common import EvidencePack, post_check
from agents.analysis.chain import run_chain
from agents.analysis.context import FIXTURES, RunContext, StubLLM
from agents.analysis.retrieval import StubRetrieval
from agents.analysis.schemas import (
    Claim, HypothesisCard, ProductProfile, RedTeamInput, RunConfig, Verdict, WhyNow,
)
from exports.analysis_workbook import SHEETS, AnalysisWorkbook

BACKTEST = date(2021, 1, 1)
LIVE = date(2026, 11, 7)


def load(name):
    return json.loads((FIXTURES / name).read_text())


def make_ctx(as_of=BACKTEST):
    cfg = RunConfig(disease="COPD", disease_id="EFO_0000341", as_of=as_of,
                    mode="single_shot", profile="dev")
    return RunContext(config=cfg, retrieval=StubRetrieval(as_of), llm=StubLLM())


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ schemas
def test_fixtures_validate():
    RunConfig.model_validate(load("a1_disease_in.json"))
    WhyNow.model_validate(load("a1_disease_out.json"))
    WhyNow.model_validate(load("a2_mechanism_in.json"))
    [HypothesisCard.model_validate(c) for c in load("a2_mechanism_out.json")]
    HypothesisCard.model_validate(load("a3_profile_in.json"))
    [ProductProfile.model_validate(p) for p in load("a3_profile_out.json").values()]
    RedTeamInput.model_validate(load("a6_redteam_in.json"))
    [Verdict.model_validate(v) for v in load("a6_redteam_out.json").values()]


# ------------------------------------------------------------------ leakage
@pytest.mark.parametrize("method,args", [
    ("search", ("COPD",)), ("trials", ()), ("targets", ("EFO_0000341",)), ("drugs", ()),
    ("approvals", ()), ("retractions", ()),
])
def test_stub_retrieval_never_returns_after_as_of(method, args):
    r = StubRetrieval(BACKTEST)
    for rec in run(getattr(r, method)(*args)):
        assert rec["source_date"] < "2021-01-01", rec["id"]


def test_trial_status_is_as_of_cutoff():
    t = {x["id"]: x for x in run(StubRetrieval(BACKTEST).trials())}
    assert t["fx:nct-boreas"]["status"] == "recruiting"  # completed only in 2023
    assert "fx:nct-aerify1" not in t  # first posted after cutoff


def test_post_check_strips_post_cutoff_and_unknown_ids():
    pack = EvidencePack(BACKTEST)
    pack.add([{"id": "ok", "source_date": "2020-01-01"}, {"id": "late", "source_date": "2023-01-01"}])
    c1 = Claim(id="x", agent="x", text="t", kind="evidence", confidence=0.95, source_ids=["ok", "late", "made-up"])
    c2 = Claim(id="x", agent="x", text="t", kind="evidence", confidence=0.95, source_ids=["late"])
    seg = WhyNow(shifts=[], segments=[{"name": "s", "inclusion_criteria": ["a", "b"], "claims": [c1, c2]}])
    log = post_check(seg, "a1_disease", pack, cap=0.75)
    assert c1.source_ids == ["ok"] and c1.confidence == 0.75
    assert c2.kind == "reasoning" and c2.source_ids == [] and c2.confidence <= 0.5
    assert {l["action"] for l in log} == {"stripped_sources", "downgraded_to_reasoning"}
    assert c1.agent == "a1_disease" and c1.id.startswith("a1-")


# ------------------------------------------------------------------ agents
def test_a1_meets_ps_bars():
    ctx = make_ctx()
    out = run(a1_disease.run(ctx.config, ctx))
    assert a1_disease.check(out) == []
    assert any(s.type == "negative" for s in out.shifts)


def test_a2_grades_and_gates():
    ctx = make_ctx()
    cards = run(a2_mechanism.run(WhyNow.model_validate(load("a1_disease_out.json")), ctx))
    ids = [c.id for c in cards]
    assert 3 <= len(cards) <= 5
    assert "h6" not in ids  # HHIP: strong genetics but untractable -> rejected
    by = {c.id: c for c in cards}
    assert by["h1"].evidence_tier == 1 and by["h1"].validation_level == "gold"
    assert by["h3"].evidence_tier == 2  # IL33/ST2: moderate genetics, phase 2
    assert by["h5"].evidence_tier == 3 and by["h5"].validation_level == "bronze"  # failed Ph3 precedent
    assert ids[-1] == "h5"
    assert [c.evidence_tier for c in cards] == sorted(c.evidence_tier for c in cards)
    assert "IL33" in ctx.signals["pub_counts"]
    assert any(e[1] == "card_rejected" for e in ctx.bus.log)


def test_a3_rejects_vague_must_have():
    card = HypothesisCard.model_validate(load("a3_profile_in.json"))
    bad = ProductProfile.model_validate(load("a3_profile_out.json")["h1"])
    bad.must_haves = ["better than standard of care"]
    assert a3_profile.check(bad, card)


def test_a3_runs():
    ctx = make_ctx()
    card = HypothesisCard.model_validate(load("a3_profile_in.json"))
    out = run(a3_profile.run(card, ctx))
    assert out.hypothesis_id == "h1" and 1 <= len(out.must_haves) <= 2


def test_a6_kill_emits_kill_event():
    ctx = make_ctx()
    cards = {c["id"]: c for c in load("a2_mechanism_out.json")}
    inp = RedTeamInput(card=cards["h5"], profile=load("a3_profile_out.json")["h5"],
                       why_now=load("a1_disease_out.json"))
    v = run(a6_redteam.run(inp, ctx))
    assert v.verdict == "kill"
    kills = [p for _, e, p in ctx.bus.log if e == "kill"]
    assert kills and kills[0]["hypothesis_id"] == "h5" and kills[0]["kill_criterion"]
    # deterministic checklist found the retraction of the cited mouse paper
    assert any("fx:rw-2020-001" in x for x in ctx.signals["a6_checklist"]["h5"]["retractions"])


def test_a6_revise_targets_a3():
    ctx = make_ctx()
    cards = {c["id"]: c for c in load("a2_mechanism_out.json")}
    inp = RedTeamInput(card=cards["h3"], profile=load("a3_profile_out.json")["h3"],
                       why_now=load("a1_disease_out.json"))
    v = run(a6_redteam.run(inp, ctx))
    assert v.verdict == "revise" and v.revise_target == "a3_profile"


def test_feedback_reaches_prompt():
    ctx = make_ctx()
    ctx.feedback = "Restrict to former smokers"
    run(a3_profile.run(HypothesisCard.model_validate(load("a3_profile_in.json")), ctx))
    assert "Restrict to former smokers" in ctx.llm.calls[-1]["messages"][-1]["content"]


# ------------------------------------------------------------------ chain + workbook
def test_chain_with_revise_and_kill_and_live_workbook(tmp_path):
    ctx = make_ctx()
    path = tmp_path / "analysis.xlsx"
    writes = []
    wb = AnalysisWorkbook(ctx, path).attach()
    orig = wb.write
    wb.write = lambda: writes.append(1) or orig()

    res = run(run_chain(ctx))
    assert res["killed"] == ["h5"]
    assert res["results"]["h3"]["revised"] is True
    assert len(writes) > 5  # updated live, not once at the end

    x = load_workbook(path)
    assert x.sheetnames == list(SHEETS)
    hyp = list(x["Hypotheses"].iter_rows(min_row=2, values_only=True))
    assert len(hyp) == len(res["cards"])
    hdr = SHEETS["Hypotheses"]
    assert all(r[hdr.index("hypothesis_id")] for r in hyp)
    tpp = {r[0]: r for r in x["TPP"].iter_rows(min_row=2, values_only=True)}
    assert tpp["h3"][SHEETS["TPP"].index("version")] == 2  # rerun after revise
    claims = list(x["Claims"].iter_rows(min_row=2, values_only=True))
    assert claims and all(r[1] for r in claims)


def test_live_date_sees_post_2021_evidence():
    r = StubRetrieval(LIVE)
    ids = {d["id"] for d in run(r.approvals())}
    assert "fx:fda-dupilumab-copd-2024" in ids


# ------------------------------------------------------------------ spend cap
def test_budget_caps_calls_and_tokens():
    from agents.analysis.context import Budget, BudgetExceeded
    b = Budget(max_calls=2, max_output_tokens=1000, max_total_tokens=10_000, max_tokens_per_call=800)
    assert b.before_call(5000) == 800  # clamped to per-call cap
    b.after_call(100, 700)
    assert b.before_call(5000) == 300  # clamped to remaining output budget
    b.after_call(100, 300)
    with pytest.raises(BudgetExceeded):
        b.before_call(100)


def test_post_check_repairs_dropped_prefix():
    pack = EvidencePack(BACKTEST)
    pack.add([{"id": "fx:pm-gold2019", "source_date": "2018-11-15"}])
    c = Claim(id="x", agent="x", text="t", kind="evidence", confidence=0.8, source_ids=["pm-gold2019"])
    seg = WhyNow(shifts=[], segments=[{"name": "s", "inclusion_criteria": ["a", "b"], "claims": [c]}])
    log = post_check(seg, "a1_disease", pack)
    assert c.source_ids == ["fx:pm-gold2019"] and c.kind == "evidence"
    assert log[0]["action"] == "repaired_ids"


def test_budget_off_by_default_still_counts():
    from agents.analysis.context import Budget
    b = Budget(max_calls=0, max_output_tokens=0, max_total_tokens=0, max_tokens_per_call=0)
    for _ in range(100):
        assert b.before_call(16000) == 16000
        b.after_call(1000, 1000)
    assert b.calls == 100 and b.total_tokens == 200_000
