"""What the memo agent (A8) reads: one MemoInput per run (owner: person 5).

A8 reads nothing else. No retrieval, no web search (docs/memo-plan.md, section 2a). Everything
upstream, from every parallel hypothesis branch, is gathered into a MemoInput by memo/inputs.py.

Agent outputs, including Person 3's A7 Judge (JudgeDecision), use the team's models in
agents/schemas.py. The models here are memo-only.

LoggedDecision accepts any actor or action string. It is deliberately looser than
schemas.Decision, so that a new kind of log row can never stop the memo from being written.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from frontier.agents.schemas import (
    HypothesisCard,
    InactiveAssets,
    JudgeDecision,
    Landscape,
    LedgerEntry,
    ProductProfile,
    RunConfig,
    Verdict,
    WhyNow,
)


class LedgerRow(LedgerEntry):
    """One Citation Checker result, tagged with the agent and hypothesis it came from."""

    agent: str
    hypothesis_id: str | None = None


class LoggedDecision(BaseModel):
    """One row of the decision log, from any branch, tagged with its hypothesis."""

    actor: str  # reviewer | red_teamer | judge | orchestrator
    action: str  # approve | approve_all | edit | rerun | revise | kill | override
    target: str  # e.g. 'a3_profile:h1' or 'h1'
    reason: str = ""
    hypothesis_id: str | None = None
    at: datetime | None = None


class KilledHypothesis(BaseModel):
    hypothesis_id: str
    mechanism: str = ""
    reason: str = ""
    kill_criterion: str = ""


Status = Literal["survives", "killed", "incomplete"]


class HypothesisBundle(BaseModel):
    """Everything known about one hypothesis, whichever parallel branch produced it.

    Any agent output may be None when that agent didn't run or its output failed validation;
    MemoInput.gaps then says so, and the memo reports it instead of guessing.
    """

    id: str
    rank: int  # A2 rank, 1 = best
    status: Status
    card: HypothesisCard
    grade: dict[str, Any] = Field(default_factory=dict)  # A2's code-computed grading
    profile: ProductProfile | None = None  # A3
    inactive: InactiveAssets | None = None  # A4
    landscape: Landscape | None = None  # A5
    verdict: Verdict | None = None  # A6
    judgment: JudgeDecision | None = None  # Person 3's A7 Judge
    revised: bool = False  # used its one red-team revise round


class MemoInput(BaseModel):
    """The single input to A8: the whole run after every hypothesis branch has joined."""

    run_id: str
    config: RunConfig
    why_now: WhyNow | None = None  # A1
    hypotheses: list[HypothesisBundle]  # A2 rank order, survivors and killed alike
    killed: list[KilledHypothesis] = Field(default_factory=list)
    ledger: list[LedgerRow] = Field(default_factory=list)  # Citation Checker, every agent
    decisions: list[LoggedDecision] = Field(default_factory=list)
    pub_counts: dict[str, int] = Field(default_factory=dict)  # A2: documents per target
    usage: dict[str, dict[str, Any]] = Field(default_factory=dict)  # tokens per agent
    gaps: list[str] = Field(default_factory=list)  # missing upstream pieces, stated in the memo

    @property
    def survivors(self) -> list[HypothesisBundle]:
        return [h for h in self.hypotheses if h.status == "survives"]

    def decisions_for(self, hypothesis_id: str) -> list[LoggedDecision]:
        return [d for d in self.decisions if d.hypothesis_id == hypothesis_id]
