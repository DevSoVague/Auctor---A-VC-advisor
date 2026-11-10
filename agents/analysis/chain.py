"""Analysis chain: A1 -> A2 -> per card (A3 -> [A4||A5 hook] -> A6, one revise) .

This is our local stand-in for person 5's flow, and a reference for how to call our hooks.
Person 5 owns checkpoints, persistence and the decisions table; here we only:
  - honour Verdict.revise_target once (for a1/a2/a3; a4/a5 are delegated to the hook)
  - stop a killed card
  - return everything a flow needs
"""

from __future__ import annotations

from typing import Awaitable, Callable

from agents.analysis import a1_disease, a2_mechanism, a3_profile, a6_redteam
from agents.analysis.schemas import (
    HypothesisCard, InactiveAssets, Landscape, ProductProfile, RedTeamInput, Verdict, WhyNow,
)

# HOOK IN (person 4): async (card, profile, ctx) -> (InactiveAssets | None, Landscape | None)
AssetsHook = Callable[[HypothesisCard, ProductProfile, object],
                      Awaitable[tuple[InactiveAssets | None, Landscape | None]]]


async def no_assets(card, profile, ctx):
    return None, None


async def run_chain(ctx, assets_hook: AssetsHook | None = None) -> dict:
    """assets_hook=None runs agents 4 and 5 (sequentially); pass `no_assets` to skip them."""
    if assets_hook is None:
        from agents.competitor import assets_hook as default_hook
        assets_hook = default_hook
    why_now: WhyNow = await a1_disease.run(ctx.config, ctx)
    cards = await a2_mechanism.run(why_now, ctx)

    results: dict[str, dict] = {}
    for card in cards:  # one hypothesis at a time (PS)
        profile = await a3_profile.run(card, ctx)
        inactive, landscape = await assets_hook(card, profile, ctx)
        verdict: Verdict = await a6_redteam.run(
            RedTeamInput(card=card, profile=profile, why_now=why_now,
                         inactive=inactive, landscape=landscape), ctx)
        revised = False
        if verdict.verdict == "revise":  # exactly one revise round
            revised = True
            fb = f"Red Team requested a revision: {verdict.risks[0].text if verdict.risks else verdict.premortem}"
            target = verdict.revise_target
            ctx.feedback = fb
            try:
                if target == "a3_profile":
                    profile = await a3_profile.run(card, ctx)
                elif target == "a2_mechanism":
                    ctx.emit("warning", {"agent": "chain", "hypothesis_id": card.id,
                                         "msg": "a2 revise is card-set wide; handled by flow"})
                elif target in ("a4_inactive", "a5_active"):
                    inactive, landscape = await assets_hook(card, profile, ctx)
            finally:
                ctx.feedback = None
            verdict = await a6_redteam.run(
                RedTeamInput(card=card, profile=profile, why_now=why_now,
                             inactive=inactive, landscape=landscape), ctx)
            if verdict.verdict == "revise":  # no second round: survives, flagged for diligence
                verdict.verdict, verdict.revise_target = "survives", None
                verdict.premortem = f"[revise cap reached; unresolved] {verdict.premortem}"
                ctx.emit("warning", {"agent": "chain", "hypothesis_id": card.id,
                                     "msg": "second revise requested; capped at one round -> survives (flagged)"})
                ctx.emit("agent_done", {"agent": "a6_redteam", "hypothesis_id": card.id,
                                        "output": verdict.model_dump(mode="json")})
        results[card.id] = {"card": card, "profile": profile, "verdict": verdict,
                            "revised": revised, "inactive": inactive, "landscape": landscape}

    return {"why_now": why_now, "cards": cards, "results": results,
            "survivors": [h for h, r in results.items() if r["verdict"].verdict != "kill"],
            "killed": [h for h, r in results.items() if r["verdict"].verdict == "kill"],
            "signals": ctx.signals}
