"""The menu of possible pursuits A8 chooses from (owner: person 5).

A8 may only recommend a candidate from this list, which is how the memo stays actionable and never
names a company or asset the agents didn't find (docs/memo-plan.md, sections 2a and 5).

    invest  a startup (private company) program from A5, fit yes or partly, not being dropped
    access  an A4 repurposing or shelved asset that clears the fit bar, or an A5 program its
            owner is likely to drop (a buying or licensing opportunity)
    build   only when no invest or access candidate clears the fit bar; carries A3's must-haves

Each candidate keeps the claim ids behind it, so every statement A8 writes about it can be cited.
"""

from typing import Literal

from pydantic import BaseModel, Field

from frontier.memo.caution import CautionBars
from frontier.memo.models import HypothesisBundle, MemoInput
from frontier.memo.scoring import FIT, approved_for, approved_on_mechanism

Form = Literal["invest", "access", "build"]


class PursuitCandidate(BaseModel):
    id: str  # stable: '<hypothesis>:<form>:<asset>'
    form: Form
    hypothesis_id: str
    name: str  # company and asset, or the must-have features for build
    owner: str | None = None
    stage: str | None = None
    fit: float | None = None  # to A3's product profile, 0 to 1
    why_listed: str
    must_haves: list[str] = Field(default_factory=list)  # build only
    follow_on_to: list[str] = Field(default_factory=list)  # approved drugs on the same mechanism
    claim_ids: list[str] = Field(default_factory=list)


def _for(h: HypothesisBundle, min_fit: float, disease: str = "COPD") -> list[PursuitCandidate]:
    out: list[PursuitCandidate] = []
    approved = approved_on_mechanism(h, disease)
    if h.landscape:
        for p in h.landscape.programs:
            if approved_for(p.stage, disease):
                continue  # already approved for this disease: priced in, not a way in
            fit = FIT[p.profile_fit]
            ids = [c.id for c in p.claims]
            if p.deprioritized and fit >= min_fit:
                out.append(
                    PursuitCandidate(
                        id=f"{h.id}:access:{p.asset}",
                        form="access",
                        hypothesis_id=h.id,
                        name=f"{p.asset} ({p.owner})",
                        owner=p.owner,
                        stage=p.stage,
                        fit=fit,
                        why_listed=f"owner likely to drop it: {p.flag_reason or 'flagged by A5'}",
                        claim_ids=ids,
                    )
                )
            elif p.owner_type == "startup" and fit >= min_fit:
                funding = f"; {p.funding_signal}" if p.funding_signal else ""
                out.append(
                    PursuitCandidate(
                        id=f"{h.id}:invest:{p.asset}",
                        form="invest",
                        hypothesis_id=h.id,
                        name=f"{p.owner} ({p.asset})",
                        owner=p.owner,
                        stage=p.stage,
                        fit=fit,
                        why_listed=f"startup program, fit {p.profile_fit}{funding}",
                        claim_ids=ids,
                    )
                )
    if h.inactive:
        for kind, assets in (
            ("repurposing", h.inactive.repurposing),
            ("shelved", h.inactive.shelved),
        ):
            for a in assets:
                if a.profile_fit >= min_fit:
                    out.append(
                        PursuitCandidate(
                            id=f"{h.id}:access:{a.name}",
                            form="access",
                            hypothesis_id=h.id,
                            name=f"{a.name} ({a.owner})",
                            owner=a.owner,
                            stage=a.stage,
                            fit=a.profile_fit,
                            why_listed=f"{kind} asset, fit {a.profile_fit:.1f}; {a.limitations}",
                            claim_ids=[c.id for c in a.claims],
                        )
                    )
    if not out and h.profile:
        out.append(
            PursuitCandidate(
                id=f"{h.id}:build",
                form="build",
                hypothesis_id=h.id,
                name="New asset: " + "; ".join(h.profile.must_haves),
                why_listed=(
                    "no existing asset or company clears the fit bar"
                    if not approved
                    else f"the mechanism is already approved ({'; '.join(approved)}); "
                    "the opening is a follow-on that beats it on the product profile"
                ),
                must_haves=list(h.profile.must_haves),
                claim_ids=[c.id for c in h.profile.claims],
            )
        )
    # One entry per id (an asset can appear twice in A4/A5 output); best fit first, with a
    # fixed tie-break so the order is the same on every run.
    if approved:
        out = [c.model_copy(update={"follow_on_to": approved}) for c in out]
    unique = {c.id: c for c in reversed(out)}
    return sorted(unique.values(), key=lambda c: (-(c.fit or 0.0), c.form, c.id))


def candidates(
    inp: MemoInput, bars: CautionBars | None = None
) -> dict[str, list[PursuitCandidate]]:
    """Survivor id -> its candidates, best fit first. Killed hypotheses get none."""
    min_fit = (bars or CautionBars.load()).min_profile_fit
    return {h.id: _for(h, min_fit, inp.config.disease) for h in inp.survivors}
