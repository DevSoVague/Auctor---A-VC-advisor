"""One Scorecard per hypothesis: the numbers behind the memo (owner: person 5).

Plain code, no model, so the same run always scores the same. Every input comes from MemoInput,
which means from agents 1 to 7. A8 explains these numbers; it never sets them.

    evidence     A2 grade: tier (1 best to 4), validation level, converging human lines
    crowding     A5 crowding score, 0 (open field) to 1 (crowded or priced in)
    fit          best A4 asset or A5 program fit to A3's product profile, 0 to 1
    risk         A6 risks, the A7 Judge's rulings, whether it needed a revise round
    verified     share of its claims the Citation Checker marked supports == "yes"

The upside and risk ratings answer "is the return worth the risk?". The rules are below, written
out so a reader can check every rating by hand.
"""

from typing import Literal

from pydantic import BaseModel, Field

from frontier.memo.models import HypothesisBundle, MemoInput

Rating = Literal["low", "medium", "high"]
RATINGS: tuple[Rating, ...] = ("low", "medium", "high")
FIT = {"yes": 1.0, "partly": 0.5, "no": 0.0}  # A5 program fit, on A4's 0-1 scale


class Scorecard(BaseModel):
    hypothesis_id: str
    mechanism: str
    rank: int  # A2 rank
    status: str
    tier: int
    level: str  # gold | silver | bronze
    evidence_lines: int | None = None
    crowding: float | None = None
    pub_count: int | None = None
    best_fit: float | None = None  # None when A4/A5 found nothing
    approved: list[str] = Field(default_factory=list)  # approved for the disease, same mechanism
    revised: bool = False
    risks: int = 0
    risks_without_cheap_test: int = 0
    fatal_attacks_holding: int = 0  # A7 Judge rulings
    major_attacks_holding: int = 0
    claims_checked: int = 0
    claims_supported: int = 0
    upside: Rating = "low"
    risk: Rating = "high"
    upside_drivers: list[str] = Field(default_factory=list)
    risk_drivers: list[str] = Field(default_factory=list)

    @property
    def supported_share(self) -> float:
        return self.claims_supported / self.claims_checked if self.claims_checked else 0.0

    @property
    def conviction(self) -> int:
        """Upside minus risk, -2 to +2. Ranks the survivors for the memo."""
        return RATINGS.index(self.upside) - RATINGS.index(self.risk)

    @property
    def return_vs_risk(self) -> str:
        if self.conviction > 0:
            return "return outweighs risk"
        if self.conviction == 0:
            return "balanced"
        return "risk outweighs return"


def approved_for(stage: str | None, disease: str) -> bool:
    """True when a program's stage says it is already approved for this disease.

    A5 writes stages such as "Approved", "Approved (COPD)" or "Approved (plaque psoriasis)". The
    last is approved for another disease, so for COPD it is a repurposing chance, not priced in.
    """
    text = (stage or "").strip().lower()
    if not text.startswith("approved"):
        return False
    rest = text.removeprefix("approved").strip()
    return not rest or disease.lower() in rest or "copd" in rest


def approved_on_mechanism(h: HypothesisBundle, disease: str) -> list[str]:
    """Programs on this hypothesis's mechanism that are already approved for the disease.

    Their commercial story is public, so the memo treats the mechanism as consensus: it never
    offers the approved drug itself as a pursuit, only follow-on plays.
    """
    if not h.landscape:
        return []
    return sorted(
        {f"{p.asset} ({p.owner})" for p in h.landscape.programs if approved_for(p.stage, disease)}
    )


def _best_fit(h: HypothesisBundle, disease: str) -> float | None:
    fits = []
    if h.inactive:
        fits += [a.profile_fit for a in (*h.inactive.repurposing, *h.inactive.shelved)]
    if h.landscape:  # an already-approved program is priced in: it is not a way in
        fits += [
            FIT[p.profile_fit] for p in h.landscape.programs if not approved_for(p.stage, disease)
        ]
    return max(fits) if fits else None


def _claim_ids(h: HypothesisBundle) -> set[str]:
    """Every claim made about this hypothesis by A2, A3, A4, A5 and A6."""
    ids = {c.id for c in h.card.claims}
    if h.profile:
        ids |= {c.id for c in h.profile.claims}
    if h.inactive:
        ids |= {c.id for a in (*h.inactive.repurposing, *h.inactive.shelved) for c in a.claims}
    if h.landscape:
        ids |= {c.id for p in (*h.landscape.programs, *h.landscape.orthogonal) for c in p.claims}
    if h.verdict:
        ids |= {c.id for r in h.verdict.risks for c in r.claims}
    return ids


def _verification(h: HypothesisBundle, inp: MemoInput) -> tuple[int, int]:
    """(claims checked, claims with at least one source the checker says fully supports it)."""
    mine = _claim_ids(h)
    checked: dict[str, bool] = {}
    for row in inp.ledger:
        if row.claim_id in mine and row.hypothesis_id in (h.id, None):
            checked[row.claim_id] = checked.get(row.claim_id, False) or row.supports == "yes"
    return len(checked), sum(checked.values())


def _upside(s: Scorecard) -> tuple[Rating, list[str]]:
    """Points: evidence 0-2, open field 0-2, a ready asset 0-1. 4+ high, 2-3 medium."""
    points, why = 0, []
    if s.tier == 1:
        points += 2
        why.append("the strongest tier of evidence (human genetics or late-stage clinical results)")
    elif s.tier == 2:
        points += 1
        why.append("good evidence (tier 2: early clinical or genetic support)")
    if s.approved:
        why.append(f"already approved on this mechanism ({s.approved[0]}): the field is priced in")
    elif s.crowding is not None and s.crowding <= 0.4:
        points += 2
        why.append(f"few competitors (crowding {s.crowding:.2f} on a 0 to 1 scale)")
    elif s.crowding is not None and s.crowding <= 0.7:
        points += 1
        why.append(f"a moderately busy field (crowding {s.crowding:.2f} of 1)")
    if s.best_fit is not None and s.best_fit >= 0.7:
        points += 1
        why.append(
            f"an existing asset already matches the ideal product (fit {s.best_fit:.1f} of 1)"
        )
    return ("high" if points >= 4 else "medium" if points >= 2 else "low"), why


def _risk(s: Scorecard) -> tuple[Rating, list[str]]:
    """A fatal attack that holds is high risk outright; otherwise 1 point per concern."""
    if s.fatal_attacks_holding:
        return "high", [
            "the judge upheld a fatal objection from the red team (an independent critic)"
        ]
    points, why = 0, []
    if s.major_attacks_holding:
        points += s.major_attacks_holding
        why.append(
            f"{s.major_attacks_holding} major objection(s) from the red team "
            "(an independent critic) upheld by the judge"
        )
    if s.revised:
        points += 1
        why.append("had to be revised after the red-team critique")
    if s.risks_without_cheap_test:
        points += 1
        why.append(f"{s.risks_without_cheap_test} risk(s) with no inexpensive way to test them")
    if s.claims_checked and s.supported_share < 0.8:
        points += 1
        why.append(f"only {s.supported_share:.0%} of checked claims fully supported")
    if s.level == "bronze":
        points += 1
        why.append("bronze validation level (the weakest)")
    return ("high" if points >= 3 else "medium" if points >= 1 else "low"), why


def score(h: HypothesisBundle, inp: MemoInput) -> Scorecard:
    checked, supported = _verification(h, inp)
    rulings = h.judgment.rulings if h.judgment else []
    risks = h.verdict.risks if h.verdict else []
    s = Scorecard(
        hypothesis_id=h.id,
        mechanism=h.card.mechanism,
        rank=h.rank,
        status=h.status,
        tier=int(h.grade.get("tier") or h.card.evidence_tier),
        level=str(h.grade.get("level") or h.card.validation_level),
        evidence_lines=h.grade.get("lines"),
        crowding=h.landscape.crowding_score if h.landscape else None,
        pub_count=h.grade.get("pub_count"),
        best_fit=_best_fit(h, inp.config.disease),
        approved=approved_on_mechanism(h, inp.config.disease),
        revised=h.revised,
        risks=len(risks),
        risks_without_cheap_test=sum(1 for r in risks if not r.cheapest_test.strip()),
        fatal_attacks_holding=sum(1 for r in rulings if r.holds and r.severity == "fatal"),
        major_attacks_holding=sum(1 for r in rulings if r.holds and r.severity == "major"),
        claims_checked=checked,
        claims_supported=supported,
    )
    s.upside, s.upside_drivers = _upside(s)
    s.risk, s.risk_drivers = _risk(s)
    return s


def scorecards(inp: MemoInput) -> list[Scorecard]:
    """Survivors, best conviction first; ties keep A2's rank. Deterministic."""
    cards = [score(h, inp) for h in inp.survivors]
    return sorted(cards, key=lambda s: (-s.conviction, s.rank))
