"""Deterministic parts of the competitor landscape (agent 5).

Pure functions: no I/O, no model calls, and every date rule is relative to as_of.
Constants marked PROPOSAL are defaults awaiting a team decision.
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from frontier.agents.competitor.common import (
    PLACEMENT_WEIGHTS,
    STAGE_LABELS,
    STAGES,
    last_update,
    months_before,
    parse_date,
    placement_weights,
    trial_stage,
)
from frontier.agents.schemas import Catalyst, Landscape
from frontier.core.retrieval import Record

StopCategory = Literal["safety", "efficacy", "business", "enrollment", "other"]

STOPPED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
STALE_MONTHS = 18  # PROPOSAL: the PRD leaves the no-update window open
CATALYST_HORIZON = timedelta(days=365)


class CrowdingParams(BaseModel):
    """The tunable crowding-score formula. Defaults are a PROPOSAL pending team agreement.

    score = sum(share[s] * scaled(s)) / sum(share), where scaled(s) =
    log(1 + value) / log(1 + reference), capped at 1, and the reference is the
    largest value of signal s across the disease's mechanisms. To add a signal,
    give it a share here and pass its value and reference to crowding_score.
    """

    model_config = ConfigDict(frozen=True)

    shares: dict[str, float] = {"programs": 0.7, "publications": 0.3}
    # The "programs" signal sums these over a mechanism's programs (PRD: P1=1, P2=2, P3=3).
    stage_weights: dict[str, float] = {
        "preclinical": 0.5,
        "phase1": 1,
        "phase2": 2,
        "phase3": 3,
        "filed": 4,
        "approved": 4,
    }
    # A program counts at this weight by who placed it on the mechanism (code or model).
    placement_weights: dict[str, float] = dict(PLACEMENT_WEIGHTS)

    @field_validator("shares")
    @classmethod
    def _shares_are_weights(cls, shares: dict[str, float]) -> dict[str, float]:
        if any(v < 0 for v in shares.values()) or sum(shares.values()) <= 0:
            raise ValueError("shares must be non-negative with a positive total")
        return shares

    @field_validator("stage_weights")
    @classmethod
    def _every_stage_weighted(cls, weights: dict[str, float]) -> dict[str, float]:
        if missing := set(STAGES) - weights.keys():
            raise ValueError(f"stage_weights missing {sorted(missing)}")
        return weights

    @field_validator("placement_weights")
    @classmethod
    def _both_placements_weighted(cls, weights: dict[str, float]) -> dict[str, float]:
        return placement_weights(weights)


DEFAULT_CROWDING = CrowdingParams()

# Checked in order, so a safety or efficacy reason wins over a business one.
_STOP_KEYWORDS: tuple[tuple[StopCategory, tuple[str, ...]], ...] = (
    ("safety", ("safety", "adverse", "toxicity", "side effect")),
    ("efficacy", ("efficacy", "futility", "lack of benefit", "primary endpoint")),
    ("business", ("business", "strategic", "portfolio", "sponsor decision", "company decision",
                  "commercial", "funding", "priorit")),
    ("enrollment", ("enrol", "recruit", "accrual")),
)  # fmt: skip
# Registries often say "business decision, not due to safety concerns": drop the negated part.
_NEGATED = re.compile(r"\bnot (?:due to|related to|because of|for|a result of)\b[^.;]*")


def trial_status(trial: Record) -> str:
    return str(trial.fields.get("status", "")).upper()


def stop_category(why_stopped: str | None) -> StopCategory:
    text = _NEGATED.sub("", (why_stopped or "").lower())
    for category, keywords in _STOP_KEYWORDS:
        if any(k in text for k in keywords):
            return category
    return "other"


def deprioritization(
    trials: Iterable[Record], as_of: date, stale_months: int = STALE_MONTHS
) -> tuple[bool, str | None]:
    """Whether the owner has likely deprioritized a program, judged from its trials.

    Flags a trial stopped for business reasons with no newer registry activity
    on any trial (a later trial means development went on), or no registry
    update on any trial within stale_months before as_of. Call it only for
    programs still in development: approved drugs naturally stop updating their trials.

    A trial seen only as registered (no status: person 1's store holds back a
    record updated on or after as_of) has an unknown last update, so it never
    makes a program stale.
    """
    trials = list(trials)
    if not trials:
        return False, None
    latest = max(last_update(t, as_of) for t in trials)
    for trial in trials:
        why = trial.fields.get("why_stopped")
        stopped = trial_status(trial) in STOPPED and stop_category(why) == "business"
        if stopped and last_update(trial, as_of) == latest:
            return True, f"{trial.id} {trial_status(trial).lower()} for business reasons: {why}"
    seen_in_full = all(trial_status(t) for t in trials)
    if seen_in_full and latest < months_before(as_of, stale_months):
        return True, f"No registry update since {latest.isoformat()}"
    return False, None


def catalysts(trials_by_asset: dict[str, list[Record]], as_of: date) -> list[Catalyst]:
    """Primary completions expected in the 12 months after as_of, soonest first."""
    found = []
    for asset, trials in trials_by_asset.items():
        for trial in trials:
            on = parse_date(trial.fields.get("primary_completion"))
            if on and as_of < on <= as_of + CATALYST_HORIZON and trial_status(trial) not in STOPPED:
                stage = STAGE_LABELS.get(trial_stage(trial) or "", "Trial")
                what = f"{asset}: {stage} primary completion"
                found.append(Catalyst(on=on, what=what, source_ids=[trial.id]))
    return sorted(found, key=lambda c: c.on)


def stage_weight(stages: Iterable[str], params: CrowdingParams = DEFAULT_CROWDING) -> float:
    """The "programs" crowding signal: stage-weighted count of a mechanism's programs."""
    return sum(params.stage_weights[s] for s in stages)


def crowding_score(
    signals: Mapping[str, float],
    references: Mapping[str, float],
    params: CrowdingParams = DEFAULT_CROWDING,
) -> float:
    """0 (empty) to 1 (the most crowded mechanism on every signal). See CrowdingParams.

    A signal missing from `signals`, or whose reference is 0, scores 0.
    """

    def scaled(name: str) -> float:
        reference = references.get(name, 0)
        if reference <= 0:
            return 0.0
        return min(1.0, math.log1p(signals.get(name, 0)) / math.log1p(reference))

    total = sum(params.shares.values())
    return round(sum(share * scaled(name) for name, share in params.shares.items()) / total, 3)


def landscape_matrix(landscape: Landscape) -> dict[str, dict[str, int]]:
    """Program counts by mechanism and stage, over programs and orthogonal programs.

    The disease-level view for the memo appendix (person 5).
    """
    matrix: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for program in [*landscape.programs, *landscape.orthogonal]:
        matrix[program.mechanism][program.stage] += 1
    return {mechanism: dict(stages) for mechanism, stages in matrix.items()}
