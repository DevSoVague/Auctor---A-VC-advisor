import asyncio
import json
from datetime import date

import pytest

from agents.competitor import a5_active
from agents.competitor.a5_active import STEPS, inputs, run
from agents.competitor.common import STAGE_LABELS, mechanism_tokens
from agents.competitor.landscape import DEFAULT_CROWDING, CrowdingParams
from agents.competitor.steps import run_steps
from agents.analysis.schemas import A5Input, Landscape, RunConfig
from tests.competitor.helpers import load_fixture, make_context

LIVE = date(2026, 11, 7)
BACKTEST = date(2021, 1, 1)
ANSWER = "a5_model_output.json"
PDE_CARD = A5Input.model_validate(load_fixture("a5_active.input"))


def run_a5(ctx, inp: A5Input = PDE_CARD) -> Landscape:
    return asyncio.run(run(inp, ctx))


def by_asset(landscape: Landscape) -> dict:
    return {p.asset: p for p in [*landscape.programs, *landscape.orthogonal]}


@pytest.mark.parametrize(
    ("mechanism", "tokens"),
    [
        ("PDE3/PDE4 inhibition", {"pde3", "pde4"}),
        ("Anti-IL-33 monoclonal antibody", {"il33"}),
        ("IL-4R alpha antagonist", {"il4r"}),
        ("Interleukin-4 receptor subunit alpha antagonist", {"il4"}),
        ("Interleukin-5 receptor subunit alpha antagonist", {"il5"}),  # no shared "alpha"
        ("Phosphodiesterase type 4 inhibitor", {"phosphodiesterase4"}),
        ("IL 33 antagonist", {"il33"}),
        ("Inhibitor", set()),
    ],
)
def test_mechanism_tokens(mechanism, tokens):
    assert mechanism_tokens(mechanism) == tokens


def test_live_landscape_keeps_code_facts_and_model_judgment(competitor_ctx):
    out = run_a5(competitor_ctx(LIVE, ANSWER))

    # Placed by code (fixolumab, misfiled by the model, stays orthogonal: its known target differs);
    # fixodilast has no ChEMBL target, so the model's placement stands. Ranked by fit, stage,
    # then code-placed before model-placed.
    assert [p.asset for p in out.programs] == [
        "fixtafilast", "fixomilast", "FIX-505", "fixodilast", "FIX-404"
    ]  # fmt: skip
    assert [p.asset for p in out.orthogonal] == ["fixolumab", "FIX-303"]

    lead = out.programs[0]
    assert (lead.stage, lead.owner, lead.owner_type) == ("Approved", "Fixture Bio Inc", "startup")
    # Two trials spell the asset differently; the Phase 3 one leads.
    assert lead.lead_trial == "nct:FIXTURE-C101"
    assert lead.funding_signal == "Form D filed 2019-04-02"
    assert lead.profile_fit == "yes" and lead.novel_edge.startswith("Inhaled dual")

    programs = by_asset(out)
    assert programs["fixomilast"].owner_type == "mid_size"  # 10-K on file
    assert programs["FIX-505"].owner_type == "academic"
    assert programs["FIX-404"].flag_reason == (
        "nct:FIXTURE-C404 terminated for business reasons: "
        "Business decision - not due to safety concerns"
    )
    assert programs["FIX-505"].flag_reason == "No registry update since 2018-01-10"
    assert not programs["fixtafilast"].deprioritized


def test_model_cannot_invent_programs_citations_or_numbers(competitor_ctx, records):
    out = run_a5(competitor_ctx(LIVE, ANSWER))
    programs = by_asset(out)

    assert "FIX-999" not in programs  # invented by the model
    assert "FIX-606 breathing trainer" not in programs  # a device: code never lists it
    assert out.crowding_score != 0.99
    assert [(c.on, c.what) for c in out.catalysts] == [
        (date(2027, 5, 31), "FIX-303: Phase 3 primary completion")
    ]

    claims = [c for p in programs.values() for c in p.claims]
    known = {r.id for r in records if r.source_date < LIVE}
    assert all(set(c.source_ids) <= known for c in claims)
    assert all(c.agent == "a5_active" for c in claims)
    assert len({c.id for c in claims}) == len(claims)
    assert all(c.id.startswith("a5-h1-c") for c in claims)
    for program in programs.values():  # every row carries a cited stage
        assert any(c.kind == "evidence" and c.source_ids for c in program.claims)
        assert program.stage in STAGE_LABELS.values()


def test_backtest_sees_nothing_after_the_cutoff(competitor_ctx, records):
    ctx = competitor_ctx(BACKTEST, ANSWER)
    out = run_a5(ctx)
    programs = by_asset(out)

    assert programs["fixtafilast"].stage == "Phase 3"  # approved only in 2024
    assert programs["fixolumab"].stage == "Phase 3"
    assert programs["fixomilast"].stage == "Approved"
    assert "FIX-303" not in programs  # first registered in 2024
    assert [(c.on, c.source_ids) for c in out.catalysts] == [
        (date(2021, 6, 30), ["nct:FIXTURE-C101"])
    ]

    dates = {r.id: r.source_date for r in records}
    cited = {s for p in programs.values() for c in p.claims for s in c.source_ids}
    assert all(dates[s] < BACKTEST for s in cited)
    prompt = json.dumps(ctx.backend.calls[0]["messages"])
    assert all(r.id not in prompt for r in records if r.source_date >= BACKTEST)


def test_crowding_is_relative_to_the_busiest_mechanism(competitor_ctx):
    il33 = PDE_CARD.model_copy(deep=True)
    il33.card.mechanism, il33.card.target_ids = "IL-33 antagonism", ["hgnc:FIXTURE-IL33"]

    pde = run_a5(competitor_ctx(LIVE, ANSWER)).crowding_score
    sparse = run_a5(competitor_ctx(LIVE, ANSWER), il33).crowding_score
    assert pde == 0.7  # busiest mechanism on programs; no papers in the 3-year window
    assert 0 < sparse < pde


def test_no_candidates_means_no_model_call(competitor_ctx):
    ctx = competitor_ctx(date(2000, 1, 1), ANSWER)
    out = run_a5(ctx)
    assert (out.programs, out.orthogonal, out.catalysts, out.crowding_score) == ([], [], [], 0.0)
    assert ctx.backend.calls == []


def test_runs_in_the_team_stub_mode(monkeypatch):
    """Person 5's flow runs every agent with LLM_MODE=stub and the shared fixture store."""
    monkeypatch.setenv("LLM_MODE", "stub")
    config = RunConfig.model_validate({**load_fixture("a1_disease.input"), "as_of": LIVE})
    out = run_a5(make_context(config))
    assert out.hypothesis_id == "h1"


def test_model_placement_is_marked_and_counts_at_reduced_weight(competitor_ctx):
    def state(params: CrowdingParams = DEFAULT_CROWDING):
        ctx = competitor_ctx(LIVE, ANSWER)
        return asyncio.run(run_steps(STEPS, inputs(PDE_CARD, ctx, params)))

    default = state()
    placed = by_asset(default["landscape"])["fixodilast"]
    marked = [c for c in placed.claims if "placed on the hypothesis mechanism by model" in c.text]
    assert len(marked) == 1 and marked[0].kind == "reasoning"
    assert not any(
        "by model judgment" in c.text for c in by_asset(default["landscape"])["FIX-505"].claims
    )

    ignored = state(CrowdingParams(placement_weights={"code": 1.0, "model": 0.0}))
    extra = default["crowding"]["signals"]["programs"] - ignored["crowding"]["signals"]["programs"]
    assert extra == 0.5 * 2  # one Phase 2 program (weight 2) at the model's weight


def test_matching_finds_synonyms_and_reports_drops_and_inventions(competitor_ctx):
    state = asyncio.run(run_steps(STEPS, inputs(PDE_CARD, competitor_ctx(LIVE, ANSWER))))
    assert "fixolumab" in by_asset(state["landscape"])  # the model called it "FIX-202"
    assert state["matching"] == {
        "dropped": ["FIX-702"],  # left out by the model
        "unmatched": ["FIX-999"],  # names no program: invented
    }


def test_assets_over_the_caps_are_named_not_lost(competitor_ctx, monkeypatch):
    monkeypatch.setitem(a5_active.CAPS, "orthogonal", 1)
    state = asyncio.run(run_steps(STEPS, inputs(PDE_CARD, competitor_ctx(LIVE, ANSWER))))
    assert state["cut"] == ["FIX-303"]  # fixolumab is further along
    assert "FIX-303" not in by_asset(state["landscape"])
