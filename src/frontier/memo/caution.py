"""The "Proceed with Caution" check for the memo (agent 8).

We run one disease, so pursuits cannot be compared with other diseases. Per docs/PRD.md they are
checked against absolute bars instead, and the memo says so. Plain code makes the call so it is
repeatable; agent 8 only turns the returned reasons into prose.

Agent 8 builds one PursuitSignals per chosen pursuit with PursuitSignals.from_pursuit(scorecard,
candidate) (memo/scoring.py, memo/candidates.py). The fields come from:
- validation_level, evidence_tier: the hypothesis card (agent 2)
- crowding_score: the landscape (agent 5)
- best_profile_fit: highest Asset.profile_fit (agent 4) or Program.profile_fit (agent 5, map
  yes=1.0, partly=0.5, no=0.0); None for build pursuits
- revised, risks_without_cheap_test: the verdict (agent 6)
- claims_total, claims_supported: the claim ledger (agent 7)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_BARS_PATH = Path(__file__).with_name("caution.yaml")


class CautionBars(BaseModel):
    weak_validation_levels: list[str] = Field(default_factory=lambda: ["bronze"])
    max_evidence_tier: int = 2
    min_supported_share: float = Field(0.8, ge=0, le=1)
    min_survivors: int = 2
    soft_failures_to_flag: int = 2
    max_crowding_score: float = 0.7
    min_profile_fit: float = Field(0.5, ge=0, le=1)

    @classmethod
    def load(cls, path: Path = DEFAULT_BARS_PATH) -> CautionBars:
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})


@dataclass(frozen=True)
class PursuitSignals:
    name: str
    form: str  # invest | access | build
    validation_level: str  # gold | silver | bronze
    evidence_tier: int  # 1 (strongest) to 4
    crowding_score: float | None  # None when A5 produced no landscape
    best_profile_fit: float | None  # None for build pursuits
    revised: bool  # survived the red team only after being revised
    risks_without_cheap_test: int
    claims_total: int
    claims_supported: int
    approved_on_mechanism: tuple[str, ...] = ()  # already approved for the disease: priced in

    @classmethod
    def from_pursuit(cls, scorecard, candidate) -> PursuitSignals:
        """From a scoring.Scorecard and the candidates.PursuitCandidate A8 chose for it.

        Duck-typed to avoid a circular import (candidates.py reads CautionBars from here).
        """
        return cls(
            name=candidate.name,
            form=candidate.form,
            validation_level=scorecard.level,
            evidence_tier=scorecard.tier,
            crowding_score=scorecard.crowding,
            best_profile_fit=candidate.fit,
            revised=scorecard.revised,
            risks_without_cheap_test=scorecard.risks_without_cheap_test,
            claims_total=scorecard.claims_checked,
            claims_supported=scorecard.claims_supported,
            approved_on_mechanism=tuple(scorecard.approved),
        )


@dataclass(frozen=True)
class CautionResult:
    proceed_with_caution: bool
    reasons: list[str]  # facts for agent 8 to write up; empty when the flag is off
    basis: str = "fixed quality bars for a single disease, not a comparison with other diseases"


def _hard_failures(p: PursuitSignals, bars: CautionBars) -> list[str]:
    out = []
    if p.validation_level in bars.weak_validation_levels:
        out.append(f"{p.name}: validation level is {p.validation_level}")
    if p.evidence_tier > bars.max_evidence_tier:
        out.append(f"{p.name}: evidence tier {p.evidence_tier} (bar: {bars.max_evidence_tier})")
    share = p.claims_supported / p.claims_total if p.claims_total else 0.0
    if share < bars.min_supported_share:
        out.append(
            f"{p.name}: {p.claims_supported} of {p.claims_total} claims fully supported "
            f"({share:.0%}, bar: {bars.min_supported_share:.0%})"
        )
    return out


def _soft_failures(p: PursuitSignals, bars: CautionBars) -> list[str]:
    out = []
    if p.crowding_score is not None and p.crowding_score > bars.max_crowding_score:
        out.append(
            f"{p.name}: crowded mechanism, score {p.crowding_score:.2f} "
            f"(bar: {bars.max_crowding_score:.2f})"
        )
    if p.form != "build" and (
        p.best_profile_fit is None or p.best_profile_fit < bars.min_profile_fit
    ):
        fit = "none" if p.best_profile_fit is None else f"{p.best_profile_fit:.2f}"
        out.append(f"{p.name}: best asset fit to the product profile is {fit}")
    if p.approved_on_mechanism:
        out.append(
            f"{p.name}: consensus mechanism, already approved ({p.approved_on_mechanism[0]}); "
            "this is a follow-on play, not a new discovery"
        )
    if p.revised:
        out.append(f"{p.name}: survived the red team only after being revised")
    if p.risks_without_cheap_test:
        out.append(f"{p.name}: {p.risks_without_cheap_test} risk(s) with no cheap test")
    return out


def evaluate_caution(
    pursuits: list[PursuitSignals], survivors: int, bars: CautionBars | None = None
) -> CautionResult:
    """Return the caution flag and the reasons that triggered it."""
    bars = bars or CautionBars.load()
    reasons: list[str] = []

    if survivors < bars.min_survivors:
        reasons.append(
            f"only {survivors} hypothesis(es) survived the red team (bar: {bars.min_survivors})"
        )
    for p in pursuits:
        reasons += _hard_failures(p, bars)
        soft = _soft_failures(p, bars)
        if len(soft) >= bars.soft_failures_to_flag:
            reasons += soft

    return CautionResult(proceed_with_caution=bool(reasons), reasons=reasons)
