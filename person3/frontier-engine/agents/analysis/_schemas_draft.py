"""HOOK IN (person 2): draft copy of agents/schemas.py, verbatim from the PRD.

When person 2 pushes agents/schemas.py, `agents/analysis/schemas.py` switches to it
automatically (see that module). Only the models person 3 needs are here, plus the
person-3-specific models at the bottom (RedTeamInput, A2Output).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class RunConfig(BaseModel):
    disease: str  # 'COPD'
    disease_id: str  # MONDO or EFO ID
    as_of: date  # today for live, 2021-01-01 for the backtest
    mode: Literal["guided", "single_shot"]
    profile: Literal["dev", "final"]
    modality: Literal["therapeutic", "device", "diagnostic"] = "therapeutic"
    risk_level: Literal["low", "medium", "high"] = "medium"
    audience: Literal["specialist", "generalist"] = "generalist"
    constraints: str = ""


class Claim(BaseModel):
    id: str
    agent: str
    text: str
    kind: Literal["evidence", "reasoning"]
    confidence: float = Field(ge=0, le=1)
    source_ids: list[str]  # document IDs; required when kind == 'evidence'


class Shift(BaseModel):  # agent 1
    on: date
    type: Literal["endpoint", "segment", "trial_tool", "negative"]
    effect_on: list[Literal["time", "cost", "selection"]]
    summary: str
    claims: list[Claim]


class Segment(BaseModel):
    name: str
    inclusion_criteria: list[str]
    claims: list[Claim]


class WhyNow(BaseModel):
    shifts: list[Shift]
    segments: list[Segment]


class HypothesisCard(BaseModel):  # agent 2
    id: str
    mechanism: str
    target_ids: list[str]
    direction: Literal["inhibit", "activate", "modulate"]
    evidence_tier: Literal[1, 2, 3, 4]  # 1 = human genetics or precedent clinical data
    validation_level: Literal["gold", "silver", "bronze"]
    modality: str
    tractability: str
    safety_clues: list[str]
    biggest_uncertainty: str
    rationale_other_conditions: str
    claims: list[Claim]


class ProductProfile(BaseModel):  # agent 3
    hypothesis_id: str
    soc_gap_at_launch: str
    minimum_specs: list[str]
    holy_grail_specs: list[str]
    must_haves: list[str] = Field(min_length=1, max_length=2)
    expansion_opportunity: str
    claims: list[Claim]


class Asset(BaseModel):  # agent 4
    name: str
    entity_id: str
    owner: str
    stage: str
    last_activity: date | None
    limitations: str
    profile_fit: float = Field(ge=0, le=1)
    claims: list[Claim]


class InactiveAssets(BaseModel):
    hypothesis_id: str
    repurposing: list[Asset]
    shelved: list[Asset]


class Program(BaseModel):  # agent 5
    asset: str
    owner: str
    owner_type: Literal["startup", "mid_size", "big_pharma", "academic"]
    mechanism: str
    modality: str
    stage: str
    lead_trial: str | None
    novel_edge: str
    profile_fit: Literal["yes", "partly", "no"]
    deprioritized: bool
    flag_reason: str | None
    funding_signal: str | None
    claims: list[Claim]


class Catalyst(BaseModel):
    on: date
    what: str
    source_ids: list[str]


class Landscape(BaseModel):
    hypothesis_id: str
    programs: list[Program]
    orthogonal: list[Program]
    crowding_score: float
    catalysts: list[Catalyst]


class Risk(BaseModel):  # agent 6
    text: str
    kill_criterion: str
    cheapest_test: str
    claims: list[Claim]


class Verdict(BaseModel):
    hypothesis_id: str
    verdict: Literal["survives", "revise", "kill"]
    revise_target: str | None  # which agent to send back to, once
    risks: list[Risk]
    premortem: str


# ---------------------------------------------------------------- person 3 additions
# These are proposed additions to agents/schemas.py (send to person 2 as a PR).


class A2Output(BaseModel):
    """What the A2 model call returns. The agent hook still returns list[HypothesisCard]."""

    cards: list[HypothesisCard]


class RedTeamInput(BaseModel):
    """HOOK OUT: input to a6_redteam.run. inactive/landscape come from person 4 (A4/A5)."""

    card: HypothesisCard
    profile: ProductProfile
    why_now: WhyNow
    inactive: InactiveAssets | None = None
    landscape: Landscape | None = None
