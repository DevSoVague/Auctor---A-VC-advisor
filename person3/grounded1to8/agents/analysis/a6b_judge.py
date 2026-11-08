"""A7 Judge -> JudgeDecision. Is the red team (A6) right about the product (A3)?

HOOK OUT: async def run(profile: ProductProfile, verdict: Verdict, ctx) -> JudgeDecision

Unbiased by construction: the model sees ONLY A3's output and A6's output. No tools, no evidence pack,
no card, no A4/A5 context. Steps: make_messages -> [LLM] -> check -> commit.
"""

from __future__ import annotations

import json

from agents.analysis._common import system_prompt
from agents.analysis.schemas import JudgeDecision, ProductProfile, Verdict

AGENT = "a6b_judge"
SCHEMA = JudgeDecision


class A6bError(RuntimeError):
    pass


def make_messages(profile: ProductProfile, verdict: Verdict, system: str | None = None) -> list[dict]:
    payload = {"hypothesis_id": profile.hypothesis_id,
               "product_profile": profile.model_dump(mode="json"),
               "red_team": verdict.model_dump(mode="json")}
    return [{"role": "system", "content": system or system_prompt(AGENT)},
            {"role": "user", "content": "INPUT:\n" + json.dumps(payload, default=str, indent=1)}]


def check(out: JudgeDecision, profile: ProductProfile, verdict: Verdict) -> list[str]:
    p = []
    out.hypothesis_id = profile.hypothesis_id
    if verdict.risks and len(out.rulings) < len(verdict.risks):
        p.append(f"rule on every red-team risk ({len(verdict.risks)}), got {len(out.rulings)}")
    held = [r for r in out.rulings if r.holds]
    if out.decision == "kill" and not any(r.severity == "fatal" for r in held):
        p.append("decision 'kill' needs at least one fatal attack that holds")
    if out.red_team_is == "wrong" and held:
        p.append("red_team_is 'wrong' but some attacks hold")
    return p


def fix_prompt(problems: list[str]) -> dict:
    return {"role": "user", "content": "Fix these problems and return the full JSON:\n- " + "\n- ".join(problems)}


def commit(ctx, out: JudgeDecision) -> None:
    ctx.emit("agent_done", {"agent": AGENT, "hypothesis_id": out.hypothesis_id, "output": out.model_dump(mode="json")})


async def run(profile: ProductProfile, verdict: Verdict, ctx) -> JudgeDecision:
    ctx.emit("agent_started", {"agent": AGENT, "hypothesis_id": profile.hypothesis_id})
    msgs = make_messages(profile, verdict)
    out: JudgeDecision = await ctx.llm(agent=AGENT, messages=msgs, schema=JudgeDecision)
    if problems := check(out, profile, verdict):
        msgs.append(fix_prompt(problems))
        out = await ctx.llm(agent=AGENT, messages=msgs, schema=JudgeDecision)
        if problems := check(out, profile, verdict):
            raise A6bError(f"{profile.hypothesis_id}: " + "; ".join(problems))
    commit(ctx, out)
    return out
