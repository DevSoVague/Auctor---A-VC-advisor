"""A3 Optimal solution -> ProductProfile (TPP) per card. Answers Q3.

HOOK OUT: async def run(card: HypothesisCard, ctx) -> ProductProfile
HOOK OUT: ProductProfile.must_haves is the scoring target for A4/A5 (person 4).

The SoC gap is judged AT LAUNCH: approvals plus Phase 3 programs as of as_of, i.e. what
will be on the market when this product would arrive, not just today's label.
"""

from __future__ import annotations

import re

from agents.analysis._common import (
    TIER_CAP, EvidencePack, build_messages, emit_claims, post_check,
)
from agents.analysis.schemas import HypothesisCard, ProductProfile

AGENT = "a3_profile"

ENDPOINT_TERMS = re.compile(
    r"\d+(\.\d+)?\s*(%|ml|mg|µg|ug|weeks?|months?|days?|cells|ppb|points?|units?)|"
    r">=|<=|≥|≤|once[- ]daily|twice[- ]daily|q\d+w|every \d+ weeks|"
    r"within \d+%|non-inferior", re.I)


class A3Error(RuntimeError):
    pass


SCHEMA = ProductProfile


# ---------------------------------------------------------------- steps
async def retrieve(card: HypothesisCard, ctx) -> tuple[EvidencePack, list[dict]]:
    """Approvals + Phase 3 programs (SoC expected AT LAUNCH) + mechanism/unmet-need literature."""
    cfg = ctx.config
    pack, log = EvidencePack(cfg.as_of), []
    for call, recs in (
        (f"approvals(indication={cfg.disease})", await ctx.retrieval.approvals(indication=cfg.disease)),
        (f"trials(condition={cfg.disease}, phase=3)", await ctx.retrieval.trials(condition=cfg.disease, phase="3")),
        (f"search('{cfg.disease} {card.mechanism}', k=8)",
         await ctx.retrieval.search(f"{cfg.disease} {card.mechanism}", k=8)),
        (f"search('{cfg.disease} unmet need standard of care', k=6)",
         await ctx.retrieval.search(f"{cfg.disease} unmet need standard of care", k=6)),
    ):
        log.append({"call": call, "kept": [r["id"] for r in pack.add(recs)]})
    return pack, log


def make_messages(card: HypothesisCard, pack: EvidencePack, feedback: str | None, cfg,
                  system: str | None = None) -> list[dict]:
    payload = {"hypothesis_card": card.model_dump(mode="json"), "hypothesis_id": card.id}
    return build_messages(AGENT, payload, pack, feedback, cfg, system)


def fix_prompt(problems: list[str]) -> dict:
    return {"role": "user", "content": "Fix these problems and return the full JSON:\n- " + "\n- ".join(problems)}


def finalize(out: ProductProfile, card: HypothesisCard, pack: EvidencePack) -> list[dict]:
    return post_check(out, AGENT, pack, cap=TIER_CAP[card.evidence_tier], prefix=f"{card.id}-")


def commit(ctx, card: HypothesisCard, out: ProductProfile, log: list[dict]) -> None:
    ctx.signals.setdefault("postcheck", {}).setdefault(AGENT, {})[card.id] = log
    emit_claims(ctx, AGENT, out, card.id)
    ctx.emit("agent_done", {"agent": AGENT, "hypothesis_id": card.id, "output": out.model_dump(mode="json")})


# ---------------------------------------------------------------- whole agent
async def run(card: HypothesisCard, ctx) -> ProductProfile:
    ctx.emit("agent_started", {"agent": AGENT, "hypothesis_id": card.id})
    pack, _ = await retrieve(card, ctx)
    msgs = make_messages(card, pack, ctx.feedback, ctx.config)
    out: ProductProfile = await ctx.llm(agent=AGENT, messages=msgs, schema=ProductProfile)
    if problems := check(out, card):
        msgs.append(fix_prompt(problems))
        out = await ctx.llm(agent=AGENT, messages=msgs, schema=ProductProfile)
        if problems := check(out, card):
            raise A3Error(f"{card.id}: " + "; ".join(problems))
    log = finalize(out, card, pack)
    commit(ctx, card, out, log)
    return out


def check(out: ProductProfile, card: HypothesisCard) -> list[str]:
    p = []
    if out.hypothesis_id != card.id:
        out.hypothesis_id = card.id  # deterministic fix, not worth a retry
    for m in out.must_haves:
        if not ENDPOINT_TERMS.search(m):
            p.append(f"must-have '{m}' is not measurable: name an endpoint, effect size, "
                     "dosing or safety threshold")
    if not out.minimum_specs or not out.holy_grail_specs:
        p.append("both minimum_specs and holy_grail_specs are required")
    if not out.soc_gap_at_launch.strip():
        p.append("soc_gap_at_launch is empty")
    return p
