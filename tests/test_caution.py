"""The Proceed with Caution check flags weak pursuits against the absolute bars."""

from dataclasses import replace

import pytest

from frontier.memo.caution import CautionBars, PursuitSignals, evaluate_caution

STRONG = PursuitSignals(
    name="Access: asset A",
    form="access",
    validation_level="gold",
    evidence_tier=1,
    crowding_score=0.3,
    best_profile_fit=0.8,
    revised=False,
    risks_without_cheap_test=0,
    claims_total=10,
    claims_supported=10,
)


def test_shipped_bars_load():
    bars = CautionBars.load()
    assert bars.min_survivors == 2


def test_strong_pursuits_pass():
    result = evaluate_caution([STRONG, replace(STRONG, name="Build: X", form="build",
                                               best_profile_fit=None)], survivors=3)
    assert result.proceed_with_caution is False
    assert result.reasons == []
    assert "not a comparison with other diseases" in result.basis


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"validation_level": "bronze"}, "validation level is bronze"),
        ({"evidence_tier": 3}, "evidence tier 3"),
        ({"claims_supported": 7}, "7 of 10 claims"),
        ({"claims_total": 0, "claims_supported": 0}, "0 of 0 claims"),
    ],
)
def test_one_hard_bar_flags(change, expected):
    result = evaluate_caution([STRONG, replace(STRONG, **change)], survivors=2)
    assert result.proceed_with_caution is True
    assert any(expected in r for r in result.reasons)


def test_too_few_survivors_flags():
    result = evaluate_caution([STRONG], survivors=1)
    assert result.proceed_with_caution is True
    assert "only 1 hypothesis" in result.reasons[0]


def test_one_soft_failure_does_not_flag():
    result = evaluate_caution([replace(STRONG, crowding_score=0.9), STRONG], survivors=2)
    assert result.proceed_with_caution is False


def test_two_soft_failures_flag():
    weak = replace(STRONG, crowding_score=0.9, revised=True)
    result = evaluate_caution([weak, STRONG], survivors=2)
    assert result.proceed_with_caution is True
    assert len(result.reasons) == 2


def test_build_pursuit_is_not_judged_on_asset_fit():
    build = replace(STRONG, form="build", best_profile_fit=None, revised=True)
    result = evaluate_caution([build, STRONG], survivors=2)
    assert result.proceed_with_caution is False


def test_invest_with_no_matching_asset_counts_as_soft_failure():
    weak = replace(STRONG, form="invest", best_profile_fit=None, risks_without_cheap_test=1)
    result = evaluate_caution([weak, STRONG], survivors=2)
    assert result.proceed_with_caution is True
    assert any("fit to the product profile is none" in r for r in result.reasons)
