"""Your own workflow nodes. Each is  async def name(state, params)  and is spliced in by workflow.yaml.

Return one of:
    {"detail": "shown in the step trace", ...state updates...}
    Command(goto="a3_prompt", update={...})      # jump anywhere → loops, branches
    None                                          # just continue to the next node
`params` is the `params:` block from workflow.yaml plus _name, _after (the step you inserted after)
and _next (the node that follows). Read the current agent output with  out(state, params).

Three working examples are below. Copy one, rename it, point a workflow.yaml entry at it.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Command, interrupt

from auctor import engine as E
from auctor import nodes as N
from auctor import tools as T
from auctor.workflow import AGENT_OF


# ---------------------------------------------------------------------------- helpers
class GateError(RuntimeError):
    pass


def agent_of(p: dict) -> str:
    """'a3_postcheck' → 'a3_profile'."""
    return p.get("agent") or AGENT_OF[p["_after"].split("_")[0]]


def key_of(state: dict, p: dict) -> tuple[str, str | None]:
    """(agent, hypothesis id) for the step this node sits after. A3/A6 work on one card at a time."""
    agent = agent_of(p)
    hid = state.get("current") if agent in N.PER_CARD else None
    return agent, hid


def work_of(state: dict, p: dict) -> dict:
    agent, hid = key_of(state, p)
    return (state.get("work") or {}).get(N.fb_key(agent, hid), {})


def out(state: dict, p: dict) -> Any:
    """The agent's current output (JSON) at this point in the chain."""
    return work_of(state, p).get("out")


def claims_in(obj: Any):
    """Every claim dict inside an output."""
    if isinstance(obj, dict):
        if {"kind", "source_ids", "text"} <= obj.keys():
            yield obj
        for v in obj.values():
            yield from claims_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from claims_in(v)


def go_back(state: dict, p: dict, step: str, comment: str, why: str) -> Command:
    """Loop back to `<agent>_<step>` with a comment the model will see. Retry budgets are reset."""
    agent, hid = key_of(state, p)
    w = dict(work_of(state, p))
    for k in ("schema_tries", "sem_tries", "parse_error", "problems"):
        w.pop(k, None)
    short = p["_after"].split("_")[0]
    return Command(goto=f"{short}_{step}", update={
        "detail": why,
        "work": {N.fb_key(agent, hid): w},
        "feedback": {N.fb_key(agent, hid): comment},
        "loops": {p["_name"] + (f":{hid}" if hid else ""): loops_used(state, p) + 1},
        "decisions": [{"actor": p["_name"], "action": "loop", "target": N.fb_key(agent, hid), "reason": why}]})


def loops_used(state: dict, p: dict) -> int:
    _, hid = key_of(state, p)
    return (state.get("loops") or {}).get(p["_name"] + (f":{hid}" if hid else ""), 0)


# ---------------------------------------------------------------------------- 1. safety gate
async def safety_gate(state: dict, p: dict):
    """Checks an agent's output before it moves on.

    params: block_terms   words that must not appear unreviewed (default: safety red flags)
            max_confidence  evidence claims above this are suspicious (default 0.9)
            require_sources evidence claims must cite a source (default true)
            on_fail       review | loop | stop | flag        (default review)
            max_loops     for on_fail=loop (default 1)
    review = pause for a human (approve / rerun / reject). In single_shot mode it only flags.
    """
    o = out(state, p)
    terms = [t.lower() for t in p.get("block_terms", ["hepatotoxic", "boxed warning", "fatal", "black box",
                                                       "malignancy", "death"])]
    max_conf, need_src = p.get("max_confidence", 0.9), p.get("require_sources", True)
    problems = []
    for c in claims_in(o):
        if c["kind"] == "evidence" and need_src and not c["source_ids"]:
            problems.append(f"evidence claim without a source: {c['text'][:70]}")
        if c["kind"] == "evidence" and c.get("confidence", 0) > max_conf:
            problems.append(f"confidence {c['confidence']} above {max_conf}: {c['text'][:60]}")
        hit = [t for t in terms if t in c["text"].lower()]
        if hit:
            problems.append(f"safety term {hit}: {c['text'][:70]}")
    if not problems:
        return {"detail": "passed"}

    agent, hid = key_of(state, p)
    summary = "; ".join(problems[:3]) + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
    dec = {"actor": p["_name"], "action": "gate_failed", "target": N.fb_key(agent, hid), "reason": summary}
    on_fail = p.get("on_fail", "review")
    if on_fail == "stop":
        raise GateError(f"{p['_name']}: {summary}")
    if on_fail == "loop":
        if loops_used(state, p) < p.get("max_loops", 1):
            return go_back(state, p, "prompt", "Safety gate: fix these before continuing:\n- " + "\n- ".join(problems),
                           f"gate failed, looping back: {summary}")
        return {"detail": f"gate failed, loop cap reached → continuing: {summary}", "decisions": [dec]}
    if on_fail == "review" and state["config"]["mode"] != "single_shot":
        d = interrupt({"checkpoint": p["_name"], "hypothesis_id": hid, "summary": problems,
                       "output": o, "actions": ["approve", "rerun (comment)", "reject"]}) or {}
        act = d.get("action", "approve")
        dec["action"] = f"gate_{act}"
        if act == "reject":
            raise GateError(f"{p['_name']}: rejected by reviewer. {summary}")
        if act == "rerun":
            return go_back(state, p, "prompt", d.get("comment") or "Reviewer asked for a rerun at the safety gate.",
                           "reviewer asked for a rerun")
        return {"detail": f"gate failed, reviewer approved: {summary}", "decisions": [dec]}
    return {"detail": f"gate failed, flagged only: {summary}", "decisions": [dec]}


# ---------------------------------------------------------------------------- 2. loop
async def refine_loop(state: dict, p: dict):
    """Go back and ask the model again until a condition holds (with a loop cap).

    params: field        a list field in the output (default must_haves)
            min          minimum length of that list (default 2)
            max_loops    stop looping after this many rounds (default 2)
            goto         step to loop back to (default prompt)
            feedback     what to tell the model
    Edit the `ok` line below to make any condition you like.
    """
    o = out(state, p) or {}
    field, need = p.get("field", "must_haves"), p.get("min", 2)
    ok = len(o.get(field, [])) >= need  # <-- change this to loop on something else
    if ok:
        return {"detail": f"{field} has {len(o.get(field, []))} (need {need})"}
    if loops_used(state, p) >= p.get("max_loops", 2):
        return {"detail": f"{field} still < {need} after {loops_used(state, p)} loop(s), continuing"}
    return go_back(state, p, p.get("goto", "prompt"),
                   p.get("feedback", f"Provide at least {need} items in {field}."),
                   f"{field} has {len(o.get(field, []))} < {need}, looping back")


# ---------------------------------------------------------------------------- 3. tool call
async def run_tools(state: dict, p: dict):
    """Call tools (auctor/tools.py) and add what they return to the evidence pack, before the model runs.
    Insert it after a `*_retrieve` step.

    params: tools: [{name: corpus_search, query: "{mechanism} adverse events", k: 4}, ...]
    """
    agent, hid = key_of(state, p)
    ctx = N.AgentRun(state, agent, hid).ctx
    w = dict(work_of(state, p))
    pack = E.EvidencePack.from_dict(w["pack"])
    fill = {"disease": ctx.config.disease,
            "mechanism": N._card(state, hid).mechanism if hid and state.get("cards") else ""}
    added, log = 0, list(w.get("tool_log", []))
    for spec in p.get("tools", []):
        spec = dict(spec)
        name = spec.pop("name")
        args = {k: (v.format(**fill) if isinstance(v, str) else v) for k, v in spec.items()}
        docs = await T.REGISTRY[name](ctx, state, hid, **args)
        kept = pack.add(docs)
        added += len(kept)
        log.append({"tool": name, "args": args, "returned": len(docs), "kept": [d["id"] for d in kept]})
    w.update(pack=pack.to_dict(), tool_log=log)
    return {"detail": f"{added} new doc(s) from {len(p.get('tools', []))} tool call(s)",
            "work": {N.fb_key(agent, hid): w}}
