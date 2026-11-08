"""Scorecards, pursuit candidates and the caution bridge, on the real stub run.

Stub facts used below (tests/fixtures/memo/p3_stub_state.json):
- h1 is tier 1 gold, crowding 0.975, revised, with one major attack upheld by the Judge.
- h4 has the lowest crowding, 0.195.
- h5 is killed.
- A4/A5 claims in the stub cite fake ids the checker can't find, so verification is low.
"""

import copy
import json
from pathlib import Path

import pytest

from frontier.memo.candidates import candidates
from frontier.memo.caution import CautionBars, PursuitSignals, evaluate_caution
from frontier.memo.inputs import from_graph_state
from frontier.memo.scoring import Scorecard, scorecards

STATE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/memo/p3_stub_state.json").read_text()
)


@pytest.fixture
def inp():
    return from_graph_state(copy.deepcopy(STATE))


def by_id(cards: list[Scorecard]) -> dict[str, Scorecard]:
    return {s.hypothesis_id: s for s in cards}


def test_one_scorecard_per_survivor_none_for_killed(inp):
    cards = scorecards(inp)
    assert sorted(s.hypothesis_id for s in cards) == ["h1", "h2", "h3", "h4"]


def test_scorecard_reads_the_agents_outputs(inp):
    h1 = by_id(scorecards(inp))["h1"]
    assert (h1.tier, h1.level, h1.crowding) == (1, "gold", 0.975)
    assert h1.revised and h1.major_attacks_holding == 1 and h1.fatal_attacks_holding == 0
    assert h1.best_fit == 1.0  # A5 program fixtafilast fits 'yes'
    assert 0 < h1.claims_checked and h1.claims_supported <= h1.claims_checked


def test_ratings_follow_the_written_rules(inp):
    h1 = by_id(scorecards(inp))["h1"]
    # Upside: tier 1 (+2), crowded 0.975 (+0), asset fit 1.0 (+1) = 3 -> medium.
    assert h1.upside == "medium"
    # Risk: 1 major attack, revised, low verification = 3 points -> high.
    assert h1.risk == "high" and h1.return_vs_risk == "risk outweighs return"
    assert any("major objection" in d for d in h1.risk_drivers)


def test_uncrowded_hypothesis_scores_higher_upside(inp):
    cards = by_id(scorecards(inp))
    assert cards["h4"].crowding < 0.4
    assert any("few competitors" in d for d in cards["h4"].upside_drivers)


def test_ranking_is_by_conviction_then_a2_rank(inp):
    cards = scorecards(inp)
    keys = [(-s.conviction, s.rank) for s in cards]
    assert keys == sorted(keys)
    assert scorecards(inp) == cards  # deterministic


def test_fatal_attack_upheld_means_high_risk(inp):
    h2 = next(h for h in inp.hypotheses if h.id == "h2")
    h2.judgment.rulings[0].severity = "fatal"
    assert by_id(scorecards(inp))["h2"].risk == "high"


def test_candidates_name_only_what_the_agents_found(inp):
    menu = candidates(inp)
    assert set(menu) == {"h1", "h2", "h3", "h4"}
    h1 = menu["h1"]
    invest = [c for c in h1 if c.form == "invest"]
    access = [c for c in h1 if c.form == "access"]
    assert any("Fixture Bio Inc" in c.name for c in invest)  # A5 startup, fit yes
    assert any(c.name.startswith("FIX-404") for c in access)  # A4 shelved, fit 0.7
    assert all(c.fit >= CautionBars.load().min_profile_fit for c in invest + access)
    assert all(c.claim_ids for c in h1)
    assert len({c.id for c in h1}) == len(h1)


def test_build_only_when_nothing_clears_the_fit_bar(inp):
    assert not any(c.form == "build" for c in candidates(inp)["h1"])

    h1 = next(h for h in inp.hypotheses if h.id == "h1")
    h1.inactive = None
    h1.landscape = None
    (build,) = candidates(inp)["h1"]
    assert build.form == "build"
    assert build.must_haves == h1.profile.must_haves
    assert build.claim_ids


def test_caution_bridge_from_scorecard_and_candidate(inp):
    h1 = by_id(scorecards(inp))["h1"]
    pick = candidates(inp)["h1"][0]
    signals = PursuitSignals.from_pursuit(h1, pick)
    assert signals.crowding_score == 0.975 and signals.best_profile_fit == pick.fit

    result = evaluate_caution([signals], survivors=len(inp.survivors))
    # Stub: low verification is a hard failure; crowded + revised are two soft ones.
    assert result.proceed_with_caution
    assert any("claims fully supported" in r for r in result.reasons)
    assert any("crowded mechanism" in r for r in result.reasons)


def test_missing_landscape_skips_the_crowding_bar(inp):
    h4 = by_id(scorecards(inp))["h4"]
    h4 = h4.model_copy(update={"crowding": None})
    pick = candidates(inp)["h4"][0]
    signals = PursuitSignals.from_pursuit(h4, pick)
    result = evaluate_caution([signals], survivors=4)
    assert not any("crowded" in r for r in result.reasons)
