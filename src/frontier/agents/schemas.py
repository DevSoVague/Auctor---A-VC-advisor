"""The contract every agent codes against (owner: person 2).

Part 1 is the PRD's models (docs/PRD.md, Interfaces), unchanged except for the
evidence-source check on Claim. Part 2 adds what the PRD's agent contract,
`async def run(inp: <Input>, ctx: RunContext) -> <Output>`, needs but never
defines: one input model per agent, list wrappers so every output is a single
model, and the AGENTS registry.

Changes to this file go through person 2 in a reviewed pull request.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Part 1: models from the PRD
# ---------------------------------------------------------------------------


class RunConfig(BaseModel):
    disease: str  # 'COPD'
    disease_id: str  # MONDO or EFO ID
    as_of: date  # today for live, 2021-01-01 for the backtest
    mode: Literal["guided", "single_shot"]
    profile: Literal["dev", "final"]
    modality: Literal["therapeutic", "device", "diagnostic"] = "therapeutic"
    risk_level: Literal["low", "medium", "high"] = "medium"
    audience: Literal["specialist", "generalist"] = "generalist"
    constraints: str = ""  # resource limits typed at intake


class Claim(BaseModel):
    id: str
    agent: str
    text: str
    kind: Literal["evidence", "reasoning"]
    confidence: float = Field(ge=0, le=1)
    source_ids: list[str]  # document IDs; required when kind == 'evidence'

    # Addition: the PRD states this rule as a comment; this enforces it.
    @model_validator(mode="after")
    def _evidence_needs_sources(self) -> "Claim":
        if self.kind == "evidence" and not self.source_ids:
            raise ValueError(f"claim {self.id}: an evidence claim needs at least one source_id")
        return self


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


class LedgerEntry(BaseModel):  # agent 7
    claim_id: str
    source_id: str
    source_date: date | None
    supports: Literal["yes", "partly", "no", "not_found", "after_cutoff"]
    action: Literal["keep", "downgrade", "remove"]


class Pursuit(BaseModel):  # agent 8
    form: Literal["invest", "access", "build"]
    name: str  # company, asset, or the must-have features
    hypothesis_id: str
    gap_to_profile: str
    main_risk: str
    what_would_change_the_call: str


class Memo(BaseModel):
    executive_summary: str
    pursuits: list[Pursuit] = Field(min_length=2, max_length=3)
    proceed_with_caution: bool
    caution_reasons: list[str]
    catalysts: list[Catalyst]


# ---------------------------------------------------------------------------
# Part 2: additions the PRD's agent contract needs
# ---------------------------------------------------------------------------


class HypothesisCards(BaseModel):
    """Agent 2 output: 3 to 5 ranked cards, best first."""

    cards: list[HypothesisCard] = Field(min_length=3, max_length=5)


class ClaimLedger(BaseModel):
    """Agent 7 output: one entry per (claim, source) pair checked."""

    entries: list[LedgerEntry]


class AttackRuling(BaseModel):
    """Person 3's A7 Judge: its ruling on one of the red team's (A6) risks."""

    risk: str  # the red-team risk, as A6 stated it
    holds: bool  # does the attack hold against A3's profile?
    severity: Literal["fatal", "major", "minor"]
    why: str


class JudgeDecision(BaseModel):
    """Person 3's A7 Judge: reads only A3's profile and A6's verdict, and decides.

    Not the Citation Checker (a7_citations). Copied unchanged from
    person3/1to6merged/agents/analysis/_schemas_draft.py.
    """

    hypothesis_id: str
    red_team_is: Literal["right", "partly_right", "wrong"]
    decision: Literal["advance", "revise", "kill"]
    rulings: list[AttackRuling]
    reasoning: str


class Decision(BaseModel):
    """One row of the decisions table: the memo's decision log.

    'judge' and 'approve_all' are logged by Person 3's pipeline (the A7 Judge, and the
    reviewer's "approve the rest for this agent").
    """

    actor: Literal["reviewer", "red_teamer", "judge", "orchestrator"]
    action: Literal["approve", "approve_all", "edit", "rerun", "revise", "kill", "override"]
    target: str  # e.g. 'a3_profile:h1'
    reason: str
    at: datetime | None = None


class HypothesisDossier(BaseModel):
    """Everything agents 2 to 6, and the A7 Judge, produced for one hypothesis."""

    card: HypothesisCard
    profile: ProductProfile
    inactive: InactiveAssets
    landscape: Landscape
    verdict: Verdict
    judgment: JudgeDecision | None = None  # None when the run had the judge off


# Agent 1 takes the RunConfig itself (disease, cutoff, intake settings).


class A2Input(BaseModel):
    why_now: WhyNow


class A3Input(BaseModel):
    card: HypothesisCard
    why_now: WhyNow


class A4Input(BaseModel):
    card: HypothesisCard
    profile: ProductProfile


class A5Input(BaseModel):
    card: HypothesisCard
    profile: ProductProfile


class A6Input(BaseModel):
    why_now: WhyNow
    card: HypothesisCard
    profile: ProductProfile
    inactive: InactiveAssets
    landscape: Landscape


class A7Input(BaseModel):
    claims: list[Claim]


class A8Input(BaseModel):
    why_now: WhyNow
    survivors: list[HypothesisDossier]
    killed: list[HypothesisDossier]
    ledger: list[LedgerEntry]
    decisions: list[Decision]


AGENTS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "a1_disease": (RunConfig, WhyNow),
    "a2_mechanism": (A2Input, HypothesisCards),
    "a3_profile": (A3Input, ProductProfile),
    "a4_inactive": (A4Input, InactiveAssets),
    "a5_active": (A5Input, Landscape),
    "a6_redteam": (A6Input, Verdict),
    "a7_citations": (A7Input, ClaimLedger),
    "a8_orchestrator": (A8Input, Memo),
}
"""Agent name -> (input model, output model). Names match core/profiles.yaml."""
