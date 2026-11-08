import asyncio
import json
from datetime import date

from frontier.agents.competitor.a4_inactive import STEPS, classify, inactive_reason, inputs, run
from frontier.agents.competitor.common import Candidate
from frontier.agents.competitor.steps import run_steps
from frontier.agents.schemas import A4Input, InactiveAssets, RunConfig
from frontier.core.context import load_fixture, make_context
from frontier.core.retrieval import Record

LIVE = date(2026, 11, 7)
BACKTEST = date(2021, 1, 1)
ANSWER = "a4_model_output.json"
PDE_CARD = A4Input.model_validate(load_fixture("a4_inactive.input"))


def run_a4(ctx, inp: A4Input = PDE_CARD) -> InactiveAssets:
    return asyncio.run(run(inp, ctx))


def by_name(out: InactiveAssets) -> dict:
    return {a.name: a for a in [*out.repurposing, *out.shelved]}


def test_inactive_reason(trial):
    business = trial(
        status="TERMINATED", why_stopped="Sponsor decision", last_update_posted="2020-06-01"
    )
    failed = trial(
        status="TERMINATED", why_stopped="Futility at interim", last_update_posted="2020-06-01"
    )
    running = trial(id="nct:FIXTURE-T2", status="RECRUITING", last_update_posted="2020-06-01")

    assert inactive_reason([business], BACKTEST).startswith(
        "nct:FIXTURE-T1 terminated for business"
    )
    assert inactive_reason([failed], BACKTEST) == (
        "nct:FIXTURE-T1 terminated (efficacy): Futility at interim"
    )
    assert inactive_reason([failed, running], BACKTEST) is None  # still developed elsewhere
    assert inactive_reason([], BACKTEST) is None


def drug(**fields) -> Record:
    return Record(
        id="chembl:FIXTURE-X", source="chembl", source_date=date(2015, 1, 1), fields=fields
    )


def test_chembl_max_phase_as_text_counts_as_approved():
    status = classify(
        Candidate("chembl:FIXTURE-X", "x", drug(max_phase="4.0")), "COPD", set(), LIVE
    )
    assert status.group == "repurposing"


def test_active_disease_trial_missed_by_the_disease_query_still_excludes(trial):
    """A capped disease query can miss a trial; its conditions still mark it as in the disease."""
    copd = trial(
        conditions=["Pulmonary Disease, COPD"], status="RECRUITING", last_update_posted="2020-10-01"
    )
    candidate = Candidate("chembl:FIXTURE-X", "x", drug(max_phase=2), trials=[copd])
    assert classify(candidate, "COPD", set(), BACKTEST) is None


def test_business_stop_then_a_newer_trial_is_not_shelved(trial):
    stopped = trial(
        status="TERMINATED", why_stopped="Portfolio review", last_update_posted="2018-06-01"
    )
    later = trial(
        id="nct:FIXTURE-T2", status="RECRUITING", phase="PHASE2", last_update_posted="2020-10-01"
    )
    candidate = Candidate("chembl:FIXTURE-X", "x", drug(max_phase=2), trials=[stopped, later])
    assert classify(candidate, "asthma", set(), BACKTEST).group == "repurposing"


def test_live_lists_keep_code_facts_and_model_judgment(competitor_ctx):
    out = run_a4(competitor_ctx(LIVE, ANSWER))

    # Ranked by profile fit; FIX-404 (misfiled by the model) is re-filed as shelved.
    assert [a.name for a in out.repurposing] == ["fixremilast", "fixostazol"]
    # fixozolast (fit 0.8) was placed by the model, so it ranks at 0.8 x 0.5 = 0.4.
    assert [a.name for a in out.shelved] == ["FIX-404", "fixozolast", "FIX-505"]

    assets = by_name(out)
    approved = assets["fixremilast"]
    assert (approved.stage, approved.owner, approved.last_activity) == (
        "Approved (plaque psoriasis)",
        "Fixture Derm Inc",
        date(2014, 3, 21),
    )
    assert approved.limitations.startswith("Oral dosing") and approved.profile_fit == 0.5
    chembl_only = assets["fixostazol"]  # max phase 4 in ChEMBL, no approval record
    assert (chembl_only.stage, chembl_only.owner, chembl_only.last_activity) == (
        "Approved (intermittent claudication)",
        "Unknown",
        None,
    )
    assert chembl_only.claims[0].source_ids == ["chembl:FIXTURE-D703"]

    assert assets["FIX-404"].stage == "Phase 2, terminated (COPD)"
    assert assets["FIX-404"].last_activity == date(2020, 3, 15)
    stalled = assets["FIX-505"]
    assert (stalled.stage, stalled.owner) == ("Phase 2, stalled (COPD)", "University of Fixture")
    # The model left the limitation empty, so code's reason fills it.
    assert stalled.limitations == "No registry update since 2018-01-10"


def test_model_cannot_invent_assets_or_citations(competitor_ctx, records):
    out = run_a4(competitor_ctx(LIVE, ANSWER))
    assets = by_name(out)

    assert "FIX-998" not in assets  # invented by the model
    assert "fixtafilast" not in assets  # approved in COPD: active, agent 5's
    assert "FIX-702" not in assets  # back in a COPD trial since 2023: active

    claims = [c for a in assets.values() for c in a.claims]
    known = {r.id for r in records if r.source_date < LIVE}
    assert all(set(c.source_ids) <= known for c in claims)
    assert all(c.agent == "a4_inactive" for c in claims)
    assert len({c.id for c in claims}) == len(claims)
    assert all(c.id.startswith("a4-h1-c") for c in claims)
    for asset in assets.values():  # every row carries a cited stage
        assert any(c.kind == "evidence" and c.source_ids for c in asset.claims)


def test_backtest_sees_nothing_after_the_cutoff(competitor_ctx, records):
    ctx = competitor_ctx(BACKTEST, ANSWER)
    out = run_a4(ctx)

    assert "fixtafilast" not in by_name(out)  # in a recruiting Phase 3 in 2021: active
    assert [a.name for a in out.shelved] == ["FIX-404", "FIX-702", "fixozolast", "FIX-505"]
    shelved = by_name(out)["FIX-702"]  # its 2023 COPD trial does not exist yet
    assert shelved.stage == "Phase 2, terminated (atopic dermatitis)"
    assert shelved.claims[1].text.endswith("(efficacy): Lack of efficacy at interim analysis.")

    prompt = json.dumps(ctx.backend.calls[0]["messages"])
    assert all(r.id not in prompt for r in records if r.source_date >= BACKTEST)


def test_no_candidates_means_no_model_call(competitor_ctx):
    ctx = competitor_ctx(date(2000, 1, 1), ANSWER)
    out = run_a4(ctx)
    assert (out.repurposing, out.shelved) == ([], [])
    assert ctx.backend.calls == []


def test_runs_in_the_team_stub_mode(monkeypatch):
    """Person 5's flow runs every agent with LLM_MODE=stub and the shared fixture store."""
    monkeypatch.setenv("LLM_MODE", "stub")
    config = RunConfig.model_validate({**load_fixture("a1_disease.input"), "as_of": LIVE})
    assert run_a4(make_context(config)).hypothesis_id == "h1"


def test_matching_and_model_placement(competitor_ctx):
    state = asyncio.run(run_steps(STEPS, inputs(PDE_CARD, competitor_ctx(LIVE, ANSWER))))
    assets = by_name(state["inactive"])

    assert "FIX-505" in assets  # the model gave a wrong entity ID; matched by name
    assert any(c.kind == "reasoning" and "by model judgment" in c.text
               for c in assets["fixozolast"].claims)  # fmt: skip
    assert state["matching"] == {
        "dropped": ["fixocort"],  # code could not place it; the model judged it off-mechanism
        "unmatched": ["FIX-998", "FIX-702", "fixtafilast"],  # invented, or active in COPD
    }

    even = asyncio.run(run(PDE_CARD, competitor_ctx(LIVE, ANSWER), {"code": 1.0, "model": 1.0}))
    assert [a.name for a in even.shelved][0] == "fixozolast"  # at full weight, 0.8 ranks first
