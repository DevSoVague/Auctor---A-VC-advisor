"""Tool calling for any agent, in two phases (Nebius probe, 2026-11-08: DeepSeek-V4-Flash, Qwen3.5 and
GLM-5.1 skip tools when `tools` and a json_schema response_format are sent together, so they can't share
one call).

    1. gather   chat.completions with tools=..., no response_format. The model calls tools; each call runs
                through a ToolExecutor (db_tools.py), which adds what it found to the EvidencePack. Stops
                when the model stops calling, or on max_rounds / max_calls / max_seconds.
    2. answer   the usual structured call (complete_raw / ctx.llm) on the ORIGINAL messages plus one user
                turn listing the evidence gathered (id | published | date_kind | title | text). The tool
                transcript is not replayed, so every provider accepts phase 2 and post_check sees the new
                ids in the pack.

Stub mode: StubLLM.tool_script = {agent: [(tool, args), ...]} replays fixed calls (0 tokens).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from agents.analysis.context import StubLLM, model_for

MAX_ROUNDS = int(os.getenv("TOOL_MAX_ROUNDS", 4))
MAX_CALLS = int(os.getenv("TOOL_MAX_CALLS", 12))
MAX_SECONDS = float(os.getenv("TOOL_MAX_SECONDS", 180))
RESULT_CHARS = 7000  # per tool message back to the model


def tools_on() -> bool:
    return os.getenv("AGENT_TOOLS", "0").strip().lower() in ("1", "true", "on", "yes")


def gather_brief(executor: Any, brief: str = "") -> str:
    return (
        "TOOLS: before answering, gather evidence with the tools. "
        f"Data window: {executor.floor.isoformat()} <= published < {executor.as_of.isoformat()}. "
        "Every result carries `published` (its date) and `date_kind`; prefer the most recent evidence and say "
        "when a finding is old or superseded. Call only what you need (a few targeted calls). "
        "When you have enough, stop calling tools and reply with a short note of what you found "
        "(no JSON yet; the final answer is requested next)."
        + (f"\nFOCUS: {brief}" if brief else ""))


def render_gathered(pack: Any, ids: list[str], as_of) -> str:
    lines = []
    for i in ids:
        r = pack.items.get(i)
        if not r:
            continue
        dates = f" | dates {json.dumps(r['dates'])}" if r.get("dates") else ""
        body = (r.get("body") or "")[:700]
        lines.append(f"[{r['id']} | published {r['source_date'][:10]} ({r.get('date_kind', 'source_date')}) | "
                     f"{r.get('title', '')}]{dates}\n{body}")
    return "\n\n".join(lines)


def final_messages(messages: list[dict], gathered: dict, pack: Any) -> list[dict]:
    """Original messages + one user turn with what the tools found (cite these ids too)."""
    if not gathered["new_ids"] and not gathered.get("note"):
        return list(messages)
    parts = []
    if gathered["new_ids"]:
        parts.append("EVIDENCE YOU GATHERED WITH TOOLS (also citable; each has its publication date):\n"
                     + render_gathered(pack, gathered["new_ids"], pack.as_of))
    if gathered.get("note"):
        parts.append("YOUR NOTES FROM GATHERING:\n" + gathered["note"][:2000])
    parts.append("Now write the final answer as the required JSON. Back it with claims: every factual "
                 "statement you rely on becomes a claim of kind \"evidence\" whose source_ids are ids from the "
                 "evidence above or the pack (copy them exactly; ids not listed here are removed). Put the "
                 "source's publication year in the claim text when it matters (e.g. \"(2024)\"). Only "
                 "judgement with no source is kind \"reasoning\".")
    return list(messages) + [{"role": "user", "content": "\n\n".join(parts)}]


async def gather(llm: Any, *, agent: str, messages: list[dict], executor: Any, tools: list[dict],
                 brief: str = "", max_rounds: int = MAX_ROUNDS, max_calls: int = MAX_CALLS,
                 max_seconds: float = MAX_SECONDS, temperature: float = 0.2,
                 max_tokens: int = 4000, first_choice: str = "auto", raw_brief: bool = False) -> dict:
    """Phase 1. Returns {new_ids, calls, rounds, note, stopped, input_tokens, output_tokens, latency_ms}."""
    pack = executor.pack
    before = set(pack.items) if pack is not None else set()
    n_log = len(executor.log)
    out = {"rounds": 0, "note": "", "stopped": "no_tools", "input_tokens": 0, "output_tokens": 0}
    t0 = time.time()

    if not tools:
        pass
    elif isinstance(llm, StubLLM):
        for name, args in (getattr(llm, "tool_script", {}) or {}).get(agent, []):
            await executor(name, args)
        out.update(rounds=1, stopped="stub_script")
    else:
        model = getattr(llm, "model_override", None) or model_for(agent, llm.profile)
        convo = list(messages) + [{"role": "user", "content": brief if raw_brief else gather_brief(executor, brief)}]
        calls = 0
        out["stopped"] = "max_rounds"
        for rnd in range(max_rounds):
            if time.time() - t0 > max_seconds:
                out["stopped"] = "time"
                break
            cap = llm.budget.before_call(max_tokens)
            ts = time.time()
            rec: dict[str, Any] = {"agent": agent, "model": model, "attempt": 0, "mode": "tools",
                                   "round": rnd + 1, "max_tokens": cap}
            llm.trace.append(rec)
            try:
                choice = first_choice if rnd == 0 else "auto"
                try:
                    resp = await llm.client.chat.completions.create(
                        model=model, messages=convo, tools=tools, tool_choice=choice,
                        temperature=temperature, max_tokens=cap)
                except Exception as e:  # noqa: BLE001 - provider rejects 'required': fall back to auto
                    if choice == "auto" or "tool_choice" not in str(e):
                        raise
                    resp = await llm.client.chat.completions.create(
                        model=model, messages=convo, tools=tools, tool_choice="auto",
                        temperature=temperature, max_tokens=cap)
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {str(e)[:500]}"
                out["stopped"] = "error"
                out["error"] = rec["error"]
                break
            inp = (resp.usage.prompt_tokens or 0) if resp.usage else 0
            otk = (resp.usage.completion_tokens or 0) if resp.usage else 0
            llm.budget.after_call(inp, otk)
            u = llm.usage[agent]
            u.calls += 1
            u.model = model
            u.input_tokens += inp
            u.output_tokens += otk
            msg = resp.choices[0].message
            rec.update(raw=msg.content or "", input_tokens=inp, output_tokens=otk,
                       finish_reason=resp.choices[0].finish_reason,
                       tool_calls=[{"name": c.function.name, "args": c.function.arguments}
                                   for c in (msg.tool_calls or [])],
                       latency_ms=int((time.time() - ts) * 1000))
            u.latency_ms += rec["latency_ms"]
            out["input_tokens"] += inp
            out["output_tokens"] += otk
            out["rounds"] = rnd + 1
            if not msg.tool_calls:
                out["note"] = (msg.content or "").strip()
                out["stopped"] = "done"
                break
            convo.append(msg.model_dump(exclude_none=True))
            for c in msg.tool_calls:
                if calls >= max_calls:
                    result = {"error": "tool call limit reached; stop calling tools and summarise"}
                else:
                    result = await executor(c.function.name, c.function.arguments)
                    calls += 1
                convo.append({"role": "tool", "tool_call_id": c.id,
                              "content": json.dumps(result, default=str)[:RESULT_CHARS]})
            if calls >= max_calls:
                out["stopped"] = "max_calls"
                break

    after = list(pack.items) if pack is not None else []
    out["new_ids"] = [i for i in after if i not in before]
    out["calls"] = executor.log[n_log:]
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


# ----------------------------------------------------------------------------- one call from an agent
BRIEFS = {
    "a1_disease": "What is new: check store_coverage, then look for the most recent COPD evidence (trials, "
                  "failures, approvals) that changes why now is the time. Newer evidence outranks older.",
    "a2_mechanism": "Mechanism evidence for COPD targets: papers and trial results per mechanism, shelved "
                    "programs on the same target (why they failed), drugs already in market on that pathway.",
    "a3_profile": "Product comparators: in-market drugs on or near this mechanism, their market stats "
                  "(cost, usage, coverage) and FDA label safety facts; phase 3 trial designs and endpoints.",
    "a4_inactive": "Pre-existing assets: same-mechanism drugs, repurposing candidates, shelved programs and why.",
    "a5_active": "Competitor programs in COPD on this mechanism or for the same patients: stage, owner, status.",
    "a6_redteam": "Build the case AGAINST the product: failures on the same mechanism, safety signals in "
                  "labels, shelved programs, newer evidence contradicting cited sources. Re-read cited docs.",
}


def llm_of(ctx: Any) -> Any:
    return getattr(ctx, "llm_obj", None) or ctx.llm


async def with_tools(ctx: Any, agent: str, messages: list[dict], pack: Any, brief: str | None = None,
                     extra: dict | None = None, extra_tools: list[dict] | None = None, **limits) -> list[dict]:
    """Phase 1 for an agent's run(): returns the messages for the structured call.
    No-op unless AGENT_TOOLS=1 (or ctx.signals['tools'] is True)."""
    on = ctx.signals.get("tools") if isinstance(getattr(ctx, "signals", None), dict) else None
    if not (tools_on() if on is None else on):
        return messages
    from agents.analysis.db_tools import ToolExecutor, tools_for

    ex = ToolExecutor(agent, ctx.config.as_of, pack, extra=extra or {},
                      on_call=lambda e: ctx.emit("tool_call", e))
    g = await gather(llm_of(ctx), agent=agent, messages=messages, executor=ex,
                     tools=tools_for(agent, extra_tools), brief=BRIEFS.get(agent, "") if brief is None else brief,
                     **limits)
    ctx.signals.setdefault("tool_calls", []).extend(g["calls"])
    ctx.emit("tools_done", {"agent": agent, "calls": len(g["calls"]), "new_ids": g["new_ids"],
                            "rounds": g["rounds"], "stopped": g["stopped"]})
    return final_messages(messages, g, pack)


# ----------------------------------------------------------------------------- A6 checker subagents
CHECKERS = {
    "safety": "SAFETY CHECKER: find safety signals against this product: FDA label warnings, contraindications and "
              "adverse-reaction rates (drug % vs placebo %) for drugs on the same or nearby mechanism; safety-driven "
              "shelved programs; trial safety findings.",
    "exists": "ALREADY-EXISTS CHECKER: is this product already on the market or tried before? In-market drugs on the "
              "same pathway, shelved programs on the same target (and why), trials of the same mechanism.",
    "market": "MARKET CHECKER: how crowded and how valuable: in-market comparators with cost, usage, coverage and "
              "total spend; active competitor trials on this mechanism or for the same patients.",
    "evidence": "EVIDENCE CHECKER: re-read the documents cited by Agent 3 (get_document) and look for newer or "
                "contradicting evidence: failed primary endpoints, mixed results, small or single-site studies.",
}


async def with_checkers(ctx: Any, agent: str, messages: list[dict], pack: Any,
                        names: list[str] | None = None) -> list[dict]:
    """Red-team checkers: one focused tool gather per checker, in parallel, sharing the evidence pack.
    Their notes and finds go into one final turn for the structured call. A6_CHECKERS=safety,exists,...
    ('' or 'none' = a single gather, as with_tools)."""
    import asyncio

    on = ctx.signals.get("tools") if isinstance(getattr(ctx, "signals", None), dict) else None
    if not (tools_on() if on is None else on):
        return messages
    names = names if names is not None else [
        n.strip() for n in os.getenv("A6_CHECKERS", ",".join(CHECKERS)).split(",") if n.strip() in CHECKERS]
    if not names:
        return await with_tools(ctx, agent, messages, pack)
    from agents.analysis.db_tools import ToolExecutor, tools_for

    before = set(pack.items)

    async def one(name: str) -> dict:
        ex = ToolExecutor(agent, ctx.config.as_of, pack,
                          on_call=lambda e, n=name: ctx.emit("tool_call", {**e, "checker": n}))
        g = await gather(llm_of(ctx), agent=agent, messages=messages, executor=ex, tools=tools_for(agent),
                         brief=CHECKERS[name], max_rounds=int(os.getenv("CHECKER_ROUNDS", 3)),
                         max_calls=int(os.getenv("CHECKER_CALLS", 6)))
        ctx.signals.setdefault("tool_calls", []).extend({**c, "checker": name} for c in g["calls"])
        return {"name": name, **g}

    results = await asyncio.gather(*(one(n) for n in names))
    new_ids = [i for i in pack.items if i not in before]
    note = "\n\n".join(f"[{r['name']} checker] {r['note']}" for r in results if r.get("note"))
    ctx.emit("tools_done", {"agent": agent, "checkers": names, "calls": sum(len(r["calls"]) for r in results),
                            "new_ids": new_ids})
    return final_messages(messages, {"new_ids": new_ids, "note": note}, pack)
