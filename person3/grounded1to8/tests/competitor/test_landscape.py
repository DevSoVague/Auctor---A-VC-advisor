from datetime import date

import pytest
from pydantic import ValidationError

from agents.competitor.landscape import (
    DEFAULT_CROWDING,
    CrowdingParams,
    catalysts,
    crowding_score,
    deprioritization,
    landscape_matrix,
    stage_weight,
    stop_category,
)
from agents.analysis.schemas import Landscape
from tests.competitor.helpers import load_fixture

BACKTEST = date(2021, 1, 1)


@pytest.mark.parametrize(
    ("why", "category"),
    [
        ("Business decision - not due to safety concerns", "business"),
        ("Sponsor decision", "business"),
        ("Portfolio prioritization", "business"),
        ("Safety concerns in the treatment arm", "safety"),
        ("Futility at interim analysis", "efficacy"),
        ("Slow enrollment", "enrollment"),
        ("COVID-19", "other"),
        (None, "other"),
    ],
)
def test_stop_category(why, category):
    assert stop_category(why) == category


def test_business_termination_is_flagged_with_its_reason(trial):
    stopped = trial(
        status="TERMINATED", why_stopped="Business decision", last_update_posted="2020-10-01"
    )
    flagged, reason = deprioritization([stopped], as_of=BACKTEST)
    assert flagged
    assert reason == "nct:FIXTURE-T1 terminated for business reasons: Business decision"


def test_business_stop_followed_by_a_newer_trial_is_not_flagged(trial):
    stopped = trial(
        status="TERMINATED", why_stopped="Strategic decision", last_update_posted="2018-06-01"
    )
    later = trial(id="nct:FIXTURE-T2", status="RECRUITING", last_update_posted="2020-10-01")
    assert deprioritization([stopped, later], as_of=BACKTEST) == (False, None)


def test_safety_termination_is_not_deprioritization(trial):
    stopped = trial(
        status="TERMINATED", why_stopped="Safety signal", last_update_posted="2020-10-01"
    )
    assert deprioritization([stopped], as_of=BACKTEST) == (False, None)


def test_stale_program_is_flagged(trial):
    quiet = trial(status="ACTIVE_NOT_RECRUITING", last_update_posted="2019-01-15")
    assert deprioritization([quiet], BACKTEST) == (True, "No registry update since 2019-01-15")
    assert deprioritization([quiet], date(2020, 1, 1)) == (False, None)


def test_staleness_ignores_updates_after_the_cutoff(trial):
    # The record was first posted in 2018; an update dated 2022 must not count in a 2021 backtest.
    record = trial(
        source_date=date(2018, 3, 1), status="RECRUITING", last_update_posted="2022-02-01"
    )
    assert deprioritization([record], BACKTEST) == (True, "No registry update since 2018-03-01")


def test_a_trial_seen_only_as_registered_never_makes_a_program_stale(trial):
    # Person 1's store holds back a record updated after as_of: no status, unknown last update.
    registered = trial(source_date=date(2015, 3, 1), phase="PHASE2")
    assert deprioritization([registered], BACKTEST) == (False, None)


def test_no_trials_no_flag():
    assert deprioritization([], as_of=BACKTEST) == (False, None)


def test_catalysts_cover_the_twelve_months_after_as_of(trial):
    by_asset = {
        "FIX-101": [
            trial("nct:FIXTURE-A", phase="PHASE3", primary_completion="2021-06"),
            trial("nct:FIXTURE-B", phase="PHASE2", primary_completion="2022-01-01"),  # last day in
            trial("nct:FIXTURE-C", phase="PHASE2", primary_completion="2022-01-02"),  # too late
            trial("nct:FIXTURE-D", phase="PHASE2", primary_completion="2021-01-01"),  # not after
        ],
        "FIX-202": [
            trial("nct:FIXTURE-E", status="TERMINATED", primary_completion="2021-03-01"),
            trial("nct:FIXTURE-F", primary_completion="2021-02-01"),
        ],
    }
    found = catalysts(by_asset, as_of=BACKTEST)
    assert [(c.on, c.what, c.source_ids) for c in found] == [
        (date(2021, 2, 1), "FIX-202: Trial primary completion", ["nct:FIXTURE-F"]),
        (date(2021, 6, 30), "FIX-101: Phase 3 primary completion", ["nct:FIXTURE-A"]),
        (date(2022, 1, 1), "FIX-101: Phase 2 primary completion", ["nct:FIXTURE-B"]),
    ]


REFERENCES = {"programs": 12, "publications": 300}


def test_stage_weight():
    assert stage_weight(["phase1", "phase3", "approved"]) == 8
    assert stage_weight([]) == 0
    heavy = CrowdingParams(stage_weights={**DEFAULT_CROWDING.stage_weights, "approved": 10})
    assert stage_weight(["approved"], heavy) == 10


def test_crowding_score_is_scaled_to_the_most_crowded_mechanism():
    assert crowding_score(REFERENCES, REFERENCES) == 1.0
    assert crowding_score({}, REFERENCES) == 0.0
    fewer, more = {"programs": 3, "publications": 40}, {"programs": 6, "publications": 40}
    assert 0 < crowding_score(fewer, REFERENCES) < crowding_score(more, REFERENCES) < 1
    assert crowding_score({"programs": 5}, {"programs": 0}) == 0.0
    assert crowding_score({"programs": 50}, REFERENCES) == 0.7  # capped at the reference


def test_crowding_takes_new_signals_through_params():
    params = CrowdingParams(shares={"programs": 1, "publications": 1, "deals": 2})
    signals = {"programs": 12, "publications": 0, "deals": 3}
    assert crowding_score(signals, {**REFERENCES, "deals": 3}, params) == 0.75


@pytest.mark.parametrize(
    "bad",
    [
        {"shares": {"programs": -1, "publications": 1}},
        {"shares": {"programs": 0}},
        {"stage_weights": {"phase1": 1}},
        {"placement_weights": {"code": 1}},
        {"placement_weights": {"code": 1, "model": 2}},
    ],
)
def test_crowding_params_are_validated(bad):
    with pytest.raises(ValidationError):
        CrowdingParams(**bad)


def test_landscape_matrix_counts_programs_and_orthogonal():
    landscape = Landscape.model_validate(load_fixture("a5_active.output"))
    assert landscape_matrix(landscape) == {
        "PDE3/PDE4 inhibitor": {"Phase 3": 1},
        "IL-33 antagonist": {"Phase 3": 1},
    }
