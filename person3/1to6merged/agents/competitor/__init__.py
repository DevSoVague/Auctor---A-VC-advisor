"""Agents 4 and 5 (Adam's), fitted to our context. Sequential: A4, then A5.

HOOK: `assets_hook(card, profile, ctx)` is what `agents.analysis.chain.run_chain` takes.
It runs A4 and then A5 one after the other (never in parallel), emits the same events our
own agents emit, and returns `(InactiveAssets, Landscape)` for A6.
"""

from __future__ import annotations

from agents.analysis._common import emit_claims
from agents.analysis.schemas import A4Input, A5Input, HypothesisCard, InactiveAssets, Landscape, ProductProfile
from agents.competitor import a4_inactive, a5_active
from agents.competitor.context import for_agents


async def _run(ctx, agent: str, run, inp, cctx, card_id: str):
    ctx.emit("agent_started", {"agent": agent, "hypothesis_id": card_id})
    out = await run(inp, cctx)
    emit_claims(ctx, agent, out, card_id)
    ctx.emit("agent_done", {"agent": agent, "hypothesis_id": card_id,
                            "output": out.model_dump(mode="json")})
    return out


async def a4_hook(card: HypothesisCard, profile: ProductProfile, ctx) -> InactiveAssets:
    """Agent 4 alone: (card, profile, ctx) -> InactiveAssets. Used by the LangGraph a4_inactive node."""
    return await _run(ctx, "a4_inactive", a4_inactive.run,
                      A4Input(card=card, profile=profile), for_agents(ctx), card.id)


async def a5_hook(card: HypothesisCard, profile: ProductProfile, ctx) -> Landscape:
    """Agent 5 alone: (card, profile, ctx) -> Landscape. Used by the LangGraph a5_active node."""
    out = await _run(ctx, "a5_active", a5_active.run,
                     A5Input(card=card, profile=profile), for_agents(ctx), card.id)
    ctx.signals.setdefault("crowding", {})[card.id] = out.crowding_score
    return out


async def assets_hook(card: HypothesisCard, profile: ProductProfile, ctx) -> tuple[InactiveAssets, Landscape]:
    cctx = for_agents(ctx)
    inactive = await _run(ctx, "a4_inactive", a4_inactive.run,
                          A4Input(card=card, profile=profile), cctx, card.id)
    landscape = await _run(ctx, "a5_active", a5_active.run,
                           A5Input(card=card, profile=profile), cctx, card.id)
    ctx.signals.setdefault("crowding", {})[card.id] = landscape.crowding_score
    return inactive, landscape
