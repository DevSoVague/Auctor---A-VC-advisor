"""Agent 8: the memo writer (owner: person 5).

    async def run(inp: MemoInput, ctx: RunContext) -> MemoDraft

Code does everything that has a right answer: which pursuits to recommend (draft.choose), every
score and rating (scoring), the caution flag (caution), the catalysts. The model only improves the
wording of that draft, and a second pass of code rejects any wording that breaks the rules:

  - it may not use retrieval, search or the web (ctx.retrieval is never touched);
  - pursuits, names, forms, ratings and order are exactly the code's;
  - an evidence line cites at least one claim, from its own pursuit, that the Citation Checker did
    not remove; reasoning lines cite nothing;
  - every number must already appear upstream (or be a single digit, a count of upstream items);
  - every capitalised name or acronym must already appear upstream.

One retry with the problems listed. If that also fails, or the model's JSON is invalid twice, the
memo is the template draft and `fallback_reason` says why. In stub mode (no model) it is the
template draft too.
"""

import json
import re

from pydantic import BaseModel, Field

from frontier.memo.draft import MemoDraft, Statement, draft_from_numbers
from frontier.memo.models import HypothesisBundle, MemoInput
from frontier.memo.scoring import _claim_ids

AGENT = "a8_orchestrator"
MAX_ITEMS = 4  # statements per list the memo will keep


# ---- what the model returns ------------------------------------------------------------------
class PursuitText(BaseModel):
    candidate_id: str
    hook: str
    the_move: str
    next_step: str
    why_good: list[Statement] = Field(min_length=1)
    what_could_go_wrong: list[Statement] = Field(min_length=1)
    return_vs_risk: Statement
    gap_to_profile: str
    what_would_change_the_call: str


class MemoText(BaseModel):
    bottom_line: list[Statement] = Field(min_length=1)
    pursuits: list[PursuitText]


# ---- the brief: everything the model may say -------------------------------------------------
def _claims_for(h: HypothesisBundle, extra: list[str], inp: MemoInput) -> list[dict]:
    """The claims one pursuit may cite, with the checker's verdict; removed ones are left out."""
    status, known = inp.claim_status(), inp.all_claims()
    ids = [*(_claim_ids(h) | set(extra))]
    out = []
    for cid in sorted(ids):
        claim = known.get(cid)
        if claim is None or status.get(cid) == "removed":
            continue
        out.append(
            {
                "id": cid,
                "text": claim.text,
                "agent": claim.agent,
                "kind": claim.kind,
                "confidence": round(claim.confidence, 2),
                "check": status.get(cid, "unchecked"),
            }
        )
    return out


def build_brief(inp: MemoInput, base: MemoDraft) -> dict:
    from frontier.memo.candidates import candidates
    from frontier.memo.scoring import scorecards

    hyp = {h.id: h for h in inp.hypotheses}
    cards = {s.hypothesis_id: s for s in scorecards(inp)}
    menu = {c.id: c for cs in candidates(inp).values() for c in cs}
    pursuits = []
    for p in base.pursuits:
        h, s, cand = hyp[p.hypothesis_id], cards[p.hypothesis_id], menu.get(p.candidate_id)
        pursuits.append(
            {
                "candidate_id": p.candidate_id,
                "form": p.form,
                "name": p.name,
                "mechanism": p.mechanism,
                "why_listed": cand.why_listed if cand else "",
                "stage": cand.stage if cand else None,
                "scores": {
                    "upside": p.upside,
                    "risk": p.risk,
                    "return_vs_risk": s.return_vs_risk,
                    "upside_drivers": s.upside_drivers,
                    "risk_drivers": s.risk_drivers,
                    "validation_level": p.validation_level,
                    "crowding": p.crowding,
                },
                "product_must_haves": h.profile.must_haves if h.profile else [],
                "unmet_need_at_launch": h.profile.soc_gap_at_launch if h.profile else None,
                "risks": [
                    {
                        "text": r.text,
                        "kill_criterion": r.kill_criterion,
                        "cheapest_test": r.cheapest_test,
                        "claim_ids": [c.id for c in r.claims],
                    }
                    for r in (h.verdict.risks if h.verdict else [])
                ],
                "judge_upheld": [
                    {"attack": r.risk, "severity": r.severity}
                    for r in (h.judgment.rulings if h.judgment else [])
                    if r.holds
                ],
                "claims": _claims_for(h, cand.claim_ids if cand else [], inp),
            }
        )
    pitch = inp.pitch.profile if inp.pitch and inp.pitch.accepted else None
    return {
        "disease": inp.config.disease,
        "as_of": inp.config.as_of.isoformat(),
        "audience": inp.config.audience,
        "pursuits": pursuits,
        "caution": {
            "flag": base.proceed_with_caution,
            "reasons": base.caution_reasons,
            "basis": base.caution_basis,
        },
        "catalysts": [{"on": c.on.isoformat(), "what": c.what} for c in base.catalysts],
        "pitch": pitch.model_dump() if pitch else None,
        "gaps": inp.gaps,
        "draft": base.model_dump(mode="json", include={"bottom_line", "pursuits"}),
    }


# ---- the checks ------------------------------------------------------------------------------
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
NAME = re.compile(r"\b(?:[A-Z][A-Za-z0-9\-/]{2,}|[A-Z0-9]{2,}[A-Za-z0-9\-/]*)\b")


def _corpus(inp: MemoInput, base: MemoDraft, brief: dict) -> str:
    """Everything the agents produced, plus the code's own draft and the brief, lowercased."""
    return " ".join(
        [inp.model_dump_json(), base.model_dump_json(), json.dumps(brief, default=str)]
    ).lower()


def _plain_numbers(text: str) -> list[str]:
    return [m.group().rstrip("%").replace(",", "") for m in NUMBER.finditer(text)]


def _names(text: str) -> list[str]:
    """Capitalised words that are not the first word of a sentence, and acronyms anywhere."""
    out = []
    for m in NAME.finditer(text):
        word, before = m.group(), text[: m.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?:;\n-•*" or before.endswith('"')
        if word.isupper() or not sentence_start:
            out.append(word)
    return out


def _texts(p: PursuitText) -> list[str]:
    return [
        p.hook,
        p.the_move,
        p.next_step,
        p.gap_to_profile,
        p.what_would_change_the_call,
        p.return_vs_risk.text,
        *(s.text for s in p.why_good),
        *(s.text for s in p.what_could_go_wrong),
    ]


def check(text: MemoText, inp: MemoInput, base: MemoDraft, brief: dict) -> list[str]:
    """Every rule the model must follow. Returns the problems found (empty means it passed)."""
    problems: list[str] = []
    expected = [p.candidate_id for p in base.pursuits]
    got = [p.candidate_id for p in text.pursuits]
    if got != expected:
        return [f"pursuits must be exactly {expected} in that order, got {got}"]

    status, known = inp.claim_status(), inp.all_claims()
    allowed = {p["candidate_id"]: {c["id"] for c in p["claims"]} for p in brief["pursuits"]}
    corpus = _corpus(inp, base, brief)
    known_numbers = set(_plain_numbers(corpus))  # whole numbers only: "73" is not inside "1730"

    def statements(where: str, items: list[Statement], ids: set[str] | None) -> None:
        if len(items) > MAX_ITEMS:
            problems.append(f"{where}: at most {MAX_ITEMS} bullets, got {len(items)}")
        for s in items:
            if s.kind == "evidence":
                if not s.claim_ids:
                    problems.append(f"{where}: evidence line cites no claim: {s.text[:60]!r}")
                for cid in s.claim_ids:
                    if status.get(cid) == "removed":
                        problems.append(f"{where}: claim {cid} was removed by the citation check")
                    elif cid in known and known[cid].kind != "evidence":
                        problems.append(f"{where}: claim {cid} is reasoning with no source")
                    elif ids is not None and cid not in ids:
                        problems.append(f"{where}: claim {cid} is not one of this pursuit's claims")
            elif s.claim_ids:
                problems.append(f"{where}: a reasoning line must not cite claims: {s.text[:60]!r}")

    statements("bottom_line", text.bottom_line, None)
    for p in text.pursuits:
        ids = allowed[p.candidate_id]
        statements(f"{p.candidate_id} why_good", p.why_good, ids)
        statements(f"{p.candidate_id} what_could_go_wrong", p.what_could_go_wrong, ids)
        statements(f"{p.candidate_id} return_vs_risk", [p.return_vs_risk], ids)

    everything = [s.text for s in text.bottom_line]
    for p in text.pursuits:
        everything += _texts(p)
    for chunk in everything:
        for num in _plain_numbers(chunk):
            if (len(num) > 1 or not num.isdigit()) and num not in known_numbers:
                problems.append(f"number {num!r} does not appear in the brief: {chunk[:70]!r}")
        for name in _names(chunk):
            if name.lower() not in corpus:
                problems.append(f"name {name!r} does not appear in the brief: {chunk[:70]!r}")
    return list(dict.fromkeys(problems))  # each problem once


# ---- merge -----------------------------------------------------------------------------------
def merge(text: MemoText, base: MemoDraft) -> MemoDraft:
    """The model's wording on top of the code's draft; names, scores and flags stay the code's."""
    by_id = {p.candidate_id: p for p in text.pursuits}
    pursuits = []
    for p in base.pursuits:
        t = by_id[p.candidate_id]
        pursuits.append(
            p.model_copy(
                update={
                    "hook": t.hook,
                    "the_move": t.the_move,
                    "next_step": t.next_step,
                    "why_good": t.why_good,
                    "what_could_go_wrong": t.what_could_go_wrong,
                    "return_vs_risk": t.return_vs_risk,
                    "gap_to_profile": t.gap_to_profile,
                    "what_would_change_the_call": t.what_would_change_the_call,
                }
            )
        )
    return base.model_copy(
        update={"bottom_line": text.bottom_line, "pursuits": pursuits, "written_by": "model"}
    )


# ---- the agent -------------------------------------------------------------------------------
async def run(inp: MemoInput, ctx) -> MemoDraft:
    """Write the memo. Never touches ctx.retrieval; calls ctx.llm once, twice at most."""
    from frontier.core.context import SchemaError, StubLLM, load_prompt

    base = draft_from_numbers(inp)
    if len(base.pursuits) < 1:
        return base.model_copy(update={"fallback_reason": "no pursuit could be formed"})
    if isinstance(getattr(ctx, "backend", None), StubLLM):
        return base.model_copy(update={"fallback_reason": "stub mode: no model was called"})

    brief = build_brief(inp, base)
    messages = [
        {"role": "system", "content": load_prompt(AGENT)},
        {"role": "user", "content": "BRIEF:\n" + json.dumps(brief, default=str, indent=1)},
    ]
    problems: list[str] = []
    for _attempt in (1, 2):
        try:
            text = await ctx.llm(agent=AGENT, messages=messages, schema=MemoText)
        except SchemaError as err:  # ctx.llm already retried once with the validation error
            reason = f"model reply was not valid memo JSON: {str(err)[:160]}"
            return base.model_copy(update={"fallback_reason": reason})
        problems = check(text, inp, base, brief)
        if not problems:
            return merge(text, base)
        messages = [
            *messages,
            {"role": "assistant", "content": text.model_dump_json()},
            {
                "role": "user",
                "content": "Your memo broke these rules. Fix them and reply with the full "
                "JSON again, changing nothing else:\n- " + "\n- ".join(problems[:12]),
            },
        ]
    reason = "model wording rejected twice: " + "; ".join(problems[:3])
    return base.model_copy(update={"fallback_reason": reason})
