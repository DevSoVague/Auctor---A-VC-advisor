"""The memo's content as data: MemoDraft, and a writer that needs no model (owner: person 5).

MemoDraft is what A8 produces and what the renderers (sections.py, render_docx.py) read.
draft_from_numbers() fills it from MemoInput, the scorecards and the candidate menu using only
templates. It is used in three ways:
- to render a real memo before A8's model step exists, and in stub runs;
- as the fallback if the model step fails twice;
- as the structure the model step rewrites. The model changes wording only: picks, names,
  numbers and citations are fixed by code and checked again afterwards (memo/a8_memo.py).

Every Statement carries the claim ids it rests on, so the memo can cite each line. Only claims the
Citation Checker did not remove can be cited; a line whose claims were all removed becomes an
analysis line (kind "reasoning") instead of a cited one.
"""

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field

from frontier.agents.schemas import Catalyst, Claim, Memo, Pursuit
from frontier.memo.candidates import PursuitCandidate, candidates
from frontier.memo.caution import CautionResult, PursuitSignals, evaluate_caution
from frontier.memo.models import HypothesisBundle, MemoInput
from frontier.memo.scoring import Rating, Scorecard, scorecards

MAX_PURSUITS = 3
MIN_PURSUITS = 2


class Statement(BaseModel):
    text: str
    kind: Literal["evidence", "reasoning"] = "evidence"
    claim_ids: list[str] = Field(default_factory=list)  # empty only for reasoning


def cited(
    text: str, claim_ids: list[str], status: dict[str, str], claims: dict[str, Claim]
) -> Statement:
    """An evidence line citing only claims that have a source and that the checker kept.

    A claim that is the agent's own reasoning (no source) or that the checker removed cannot back
    an evidence line; with none left, the line is analysis instead.
    """
    keep = [
        c
        for c in claim_ids
        if status.get(c) != "removed" and c in claims and claims[c].kind == "evidence"
    ]
    if keep:
        return Statement(text=text, claim_ids=keep)
    note = " (not confirmed by the citation check)" if claim_ids else ""
    return Statement(text=text + note, kind="reasoning")


class PursuitWriteup(BaseModel):
    candidate_id: str
    form: Literal["invest", "access", "build"]
    name: str
    hypothesis_id: str
    mechanism: str
    hook: str  # one italic line: "Imagine if ..."
    the_move: str
    next_step: str  # a concrete action this year
    why_good: list[Statement]
    what_could_go_wrong: list[Statement]
    upside: Rating
    risk: Rating
    return_vs_risk: Statement
    gap_to_profile: str
    what_would_change_the_call: str
    validation_level: str
    crowding: float | None = None


class MemoDraft(BaseModel):
    title: str
    bottom_line: list[Statement]
    pursuits: list[PursuitWriteup] = Field(max_length=MAX_PURSUITS)
    proceed_with_caution: bool
    caution_reasons: list[str]
    caution_basis: str
    catalysts: list[Catalyst]
    written_by: Literal["template", "model"] = "template"
    fallback_reason: str | None = None  # why the model's wording was not used, when it wasn't

    def to_memo(self) -> Memo:
        """The team contract (agents/schemas.Memo). Raises if fewer than 2 pursuits."""
        return Memo(
            executive_summary=" ".join(s.text for s in self.bottom_line),
            pursuits=[
                Pursuit(
                    form=p.form,
                    name=p.name,
                    hypothesis_id=p.hypothesis_id,
                    gap_to_profile=p.gap_to_profile,
                    main_risk=p.what_could_go_wrong[0].text if p.what_could_go_wrong else "",
                    what_would_change_the_call=p.what_would_change_the_call,
                )
                for p in self.pursuits
            ],
            proceed_with_caution=self.proceed_with_caution,
            caution_reasons=self.caution_reasons,
            catalysts=self.catalysts,
        )


def choose(
    inp: MemoInput, cards: list[Scorecard], menu: dict[str, list[PursuitCandidate]]
) -> list[tuple[Scorecard, PursuitCandidate]]:
    """2-3 picks: the best candidate of each top hypothesis, one per hypothesis first.

    If fewer than 2 hypotheses have candidates, the best one's second candidate fills in, so
    a run with one survivor still gives the reader two ways in.
    """
    picks: list[tuple[Scorecard, PursuitCandidate]] = []
    taken: set[str] = set()  # the same asset can sit under two hypotheses; recommend it once

    def fresh(c: PursuitCandidate) -> bool:
        return c.form == "build" or c.name.lower() not in taken

    for s in cards:
        best = next((c for c in menu.get(s.hypothesis_id, []) if fresh(c)), None)
        if best and len(picks) < MAX_PURSUITS:
            picks.append((s, best))
            taken.add(best.name.lower())
    if len(picks) < MIN_PURSUITS:
        for s, _ in list(picks):
            for extra in menu[s.hypothesis_id]:
                if len(picks) >= MIN_PURSUITS:
                    break
                if fresh(extra):
                    picks.append((s, extra))
                    taken.add(extra.name.lower())
    return picks


def _sentence(text: str) -> str:
    """Trim and end with exactly one full stop (agent text may or may not have one)."""
    text = text.strip().rstrip(".")
    return text + "." if text else ""


def _an(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _by_id(inp: MemoInput) -> dict[str, HypothesisBundle]:
    return {h.id: h for h in inp.hypotheses}


def _the_move(c: PursuitCandidate) -> str:
    if c.follow_on_to and c.form == "build":
        return (
            f"Build a follow-on to {c.follow_on_to[0]}, already approved; "
            "it must beat it on the product profile."
        )
    if c.form == "invest":
        return f"Invest in {c.owner}, whose {c.stage or 'program'} asset is the closest fit."
    if c.form == "access":
        return f"In-license, option or acquire {c.name.split(' (')[0]} ({c.stage or 'stage n/a'})."
    return "Build a new asset to the product profile; nothing existing is close enough."


def _next_step(c: PursuitCandidate, h: HypothesisBundle) -> str:
    test = h.verdict.risks[0].cheapest_test if h.verdict and h.verdict.risks else ""
    first = {
        "invest": f"Open diligence with {c.owner}",
        "access": f"Approach {c.owner or 'the owner'} about licensing terms",
        "build": "Scope a proof-of-concept program",
    }[c.form]
    return f"{first}; run the cheapest test first: {test}" if test else first + "."


def _write(
    s: Scorecard,
    c: PursuitCandidate,
    h: HypothesisBundle,
    status: dict[str, str],
    claims: dict[str, Claim],
) -> PursuitWriteup:
    profile, verdict = h.profile, h.verdict
    gap = profile.soc_gap_at_launch if profile else "No product profile in this run."
    why = []
    if profile:
        why.append(
            cited(
                _sentence(f"Unmet need at launch: {profile.soc_gap_at_launch}"),
                [x.id for x in profile.claims[:2]],
                status,
                claims,
            )
        )
    why.append(
        cited(
            f"Evidence: tier {s.tier}, {s.level} validation"
            + (f", {s.evidence_lines} converging human lines" if s.evidence_lines else "")
            + f". {h.card.mechanism} ({h.card.direction}).",
            [x.id for x in h.card.claims[:2]],
            status,
            claims,
        )
    )
    why.append(cited(_sentence(f"Path in: {c.why_listed}"), c.claim_ids[:2], status, claims))

    wrong = []
    for r in (verdict.risks if verdict else [])[:2]:
        wrong.append(
            cited(
                f"{_sentence(r.text)} Kill if: {_sentence(r.kill_criterion)} "
                f"Cheapest test: {_sentence(r.cheapest_test)}",
                [x.id for x in r.claims],
                status,
                claims,
            )
        )
    if s.approved:
        wrong.append(
            Statement(
                kind="reasoning",
                text=f"Consensus risk: {s.approved[0]} is already approved on this mechanism, "
                "so the opportunity is a follow-on and the approved drug's success is priced in.",
            )
        )
    held = [r for r in (h.judgment.rulings if h.judgment else []) if r.holds]
    if held:
        wrong.append(
            Statement(
                kind="reasoning",
                text="The judge upheld "
                + ", ".join(f"a {r.severity} attack ({r.risk})" for r in held[:2])
                + ".",
            )
        )
    # Agent text is quoted as written (no re-casing, which would break names like BOREAS).
    change = (
        f"Drop it if: {_sentence(verdict.risks[0].kill_criterion)} "
        f"Raise conviction if this test comes back positive: "
        f"{_sentence(verdict.risks[0].cheapest_test)}"
        if verdict and verdict.risks
        else "No red-team risks in this run."
    )
    mech = h.card.mechanism
    hook = f"Imagine {_an(h.card.modality)} working through {mech} that delivers " + _sentence(
        profile.must_haves[0] if profile else "the product profile"
    )
    return PursuitWriteup(
        candidate_id=c.id,
        form=c.form,
        name=c.name,
        hypothesis_id=s.hypothesis_id,
        mechanism=mech,
        hook=hook,
        the_move=_the_move(c),
        next_step=_next_step(c, h),
        why_good=why,
        what_could_go_wrong=wrong,
        upside=s.upside,
        risk=s.risk,
        return_vs_risk=Statement(
            kind="reasoning",
            text=f"Upside {s.upside}, risk {s.risk}: {s.return_vs_risk}. "
            f"Upside: {'; '.join(s.upside_drivers) or 'none found'}. "
            f"Risk: {'; '.join(s.risk_drivers) or 'none found'}.",
        ),
        gap_to_profile=gap,
        what_would_change_the_call=change,
        validation_level=s.level,
        crowding=s.crowding,
    )


def caution_for(inp: MemoInput, picks: list[tuple[Scorecard, PursuitCandidate]]) -> CautionResult:
    result = evaluate_caution(
        [PursuitSignals.from_pursuit(s, c) for s, c in picks], survivors=len(inp.survivors)
    )
    if len(picks) < MIN_PURSUITS:
        reasons = [*result.reasons, f"only {len(picks)} pursuit(s) could be formed"]
        return CautionResult(proceed_with_caution=True, reasons=reasons, basis=result.basis)
    return result


def catalysts(inp: MemoInput) -> list[Catalyst]:
    """A5 catalysts for the survivors in the 12 months after the as-of date, soonest first."""
    horizon = inp.config.as_of + timedelta(days=365)
    seen, out = set(), []
    for h in inp.survivors:
        for c in h.landscape.catalysts if h.landscape else []:
            key = (c.on, c.what)
            if key not in seen and inp.config.as_of <= c.on <= horizon:
                seen.add(key)
                out.append(c)
    return sorted(out, key=lambda c: c.on)


def draft_from_numbers(inp: MemoInput) -> MemoDraft:
    cards = scorecards(inp)
    menu = candidates(inp)
    picks = choose(inp, cards, menu)
    hyp = _by_id(inp)
    status, claims = inp.claim_status(), inp.all_claims()
    writeups = [_write(s, c, hyp[s.hypothesis_id], status, claims) for s, c in picks]
    caution = caution_for(inp, picks)

    forms = ", ".join(f"{p.form} in {p.name}" for p in writeups) or "no pursuit"
    pitch = inp.pitch.profile if inp.pitch and inp.pitch.accepted else None
    bottom = []
    if pitch:
        bottom.append(
            Statement(
                kind="reasoning",
                text=f"We tested {pitch.company}'s pitch: {pitch.mechanism} for "
                f"{pitch.indication or inp.config.disease}.",
            )
        )
    bottom += [
        Statement(
            kind="reasoning",
            text=f"{len(inp.survivors)} of {len(inp.hypotheses)} {inp.config.disease} "
            f"hypotheses survived red-teaming; we recommend: {forms}.",
        )
    ]
    if writeups:
        top = writeups[0]
        bottom.append(
            Statement(
                kind="reasoning",
                text=f"Top pick: {top.name} on {top.mechanism}. "
                f"Upside {top.upside}, risk {top.risk}.",
            )
        )
    bottom.append(
        Statement(
            kind="reasoning",
            text=(
                "Proceed with caution: " + caution.reasons[0] + "."
                if caution.proceed_with_caution
                else "All pursuits clear our quality bars."
            ),
        )
    )
    return MemoDraft(
        title=f"{inp.config.disease}: frontier hypotheses as of {inp.config.as_of:%B %-d, %Y}",
        bottom_line=bottom,
        pursuits=writeups,
        proceed_with_caution=caution.proceed_with_caution,
        caution_reasons=caution.reasons,
        caution_basis=caution.basis,
        catalysts=catalysts(inp),
    )
