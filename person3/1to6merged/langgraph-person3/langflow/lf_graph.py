"""Run the LangGraph step nodes (the parent folder, langgraph-person3) from Langflow components.

One implementation: every Langflow node here calls the SAME node function the LangGraph graph uses,
so prompts, retry rules, post-checks and the step trace are identical. Langflow passes the LangGraph
state dict between nodes inside a Data object.

Langflow has no loops, so loops are run inside the node that needs them, by following the same
edges LangGraph would follow:
    parse     schema error → fix → llm → parse                    (one retry)
    validate  problems     → fix → llm → parse [→ postcheck] → validate   (one retry)
    route     A6 `revise`  → rerun A3 and A6 for that card once
    custom    a node that jumps back (Command goto) → rerun the steps between, then the node again (capped)
Review checkpoints (pause for a human) only exist in the LangGraph CLI, not here.
"""

from __future__ import annotations

import asyncio
import copy
import json
import operator
import os
import sys
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin, get_type_hints

HERE = Path(__file__).resolve().parent
GRAPH_HOME = Path(os.getenv("P3_LANGGRAPH_HOME", HERE.parent)).resolve()
if str(GRAPH_HOME) not in sys.path:
    sys.path.insert(0, str(GRAPH_HOME))

from langgraph.types import Command  # noqa: E402

from p3graph import engine as E  # noqa: E402
from p3graph import nodes as N  # noqa: E402
from p3graph import steps as S  # noqa: E402
from p3graph import workflow as WF  # noqa: E402
from p3graph.state import P3State, merge, merge_usage  # noqa: E402

SPECS = {"a1": S.A1, "a2": S.A2, "a3": S.A3, "a6": S.A6}
from p3graph import competitor as C  # noqa: E402

COMPETITOR = {"a4": "a4_inactive", "a5": "a5_active"}  # Adam's agents, one node per step, A4 then A5
ORDER = {
    "a1": ["retrieve", "prompt", "llm", "parse", "postcheck", "validate", "commit"],
    "a2": ["retrieve", "prompt", "llm", "parse", "grade", "postcheck", "commit"],
    "a3": ["retrieve", "prompt", "llm", "parse", "validate", "postcheck", "commit"],
    "a4": C.AGENTS["a4"]["steps"],
    "a5": C.AGENTS["a5"]["steps"],
    "a6": ["retrieve", "prompt", "llm", "parse", "validate", "postcheck", "commit", "route"],
}
PER_CARD = {"a3", "a4", "a5", "a6"}
MAX_PARALLEL = int(os.getenv("P3_MAX_PARALLEL", "1"))  # cards run at once inside a node; 1 = one at a time (V1 is sequential)


# ---------------------------------------------------------------------------- state + node lookup
def _reducers() -> dict:
    out = {}
    for k, t in get_type_hints(P3State, include_extras=True).items():
        if get_origin(t) is Annotated:
            out[k] = get_args(t)[1]
    return out


REDUCERS = _reducers()


def apply(state: dict, update: dict) -> None:
    """Merge a node's update into state using the same reducers LangGraph applies."""
    for k, v in (update or {}).items():
        r = REDUCERS.get(k)
        if r is operator.add:
            state[k] = list(state.get(k) or []) + list(v)
        elif r in (merge, merge_usage):
            state[k] = r(state.get(k), v)
        else:
            state[k] = v


def node_fn(agent: str, step: str):
    if agent in COMPETITOR:
        return C.make_node(agent, step)
    spec = SPECS[agent]
    if step == "llm":
        return S.llm_step(spec)
    if step == "parse":
        return S.parse_step(spec)
    if step == "fix":
        return S.fix_step(spec)
    return getattr(S, f"{agent}_{step}")  # includes a6_route


def _next(state: dict, agent: str, step: str) -> str | None:
    """Where LangGraph goes after `step` (the same routers the graph uses)."""
    if agent in COMPETITOR:  # one step per node, no retry loops
        return None
    spec = SPECS[agent]
    order = ORDER[agent]
    w = S.work(state, spec)
    if step == "fix":
        return "llm"
    if step == "parse" and w.get("parse_error"):
        return "fix"
    if step == "validate" and w.get("problems"):
        return "fix"
    i = order.index(step)
    return order[i + 1] if i + 1 < len(order) else None


async def _call(state: dict, agent: str, step: str):
    res = await node_fn(agent, step)(state)
    goto = None
    if isinstance(res, Command):
        goto, res = res.goto, res.update
    apply(state, res)
    return goto


async def run_step(state: dict, agent: str, step: str, hid: str | None) -> None:
    """Run one step for one hypothesis, plus any retry loop that step starts."""
    if agent in PER_CARD:
        state["current"] = hid
    base = ORDER[agent].index(step)
    cur = step
    for _ in range(12):  # safety cap on loops
        goto = await _call(state, agent, cur)
        if (agent, cur) == ("a6", "route"):
            await _revise(state, goto, hid)
            return
        nxt = _next(state, agent, cur)
        if nxt is None or (nxt != "fix" and ORDER[agent].index(nxt) > base):
            return
        cur = nxt
    raise RuntimeError(f"{agent}_{step}: retry loop did not settle (12 iterations)")


async def _revise(state: dict, goto: Any, hid: str) -> None:
    """A6 asked for a revision: rerun A3 (if targeted) and A6 once, then route again (the cap overrides a 2nd revise)."""
    if goto == "review_a6" or goto is None:
        return
    if goto == "a3_retrieve":
        await run_range(state, "a3", hid, "retrieve", "commit")
    if goto in ("a3_retrieve", C.first("a4")):  # A4 chain then A5 chain again whenever A3 or A4/A5 was sent back
        await run_range(state, "a4", hid, "gather", "commit")
        await run_range(state, "a5", hid, "gather", "commit")
    await run_range(state, "a6", hid, "retrieve", "commit")
    await run_step(state, "a6", "route", hid)


async def run_range(state: dict, agent: str, hid: str | None, first: str, last: str) -> None:
    order = ORDER[agent]
    for step in order[order.index(first): order.index(last) + 1]:
        await run_step(state, agent, step, hid)


def _name(agent: str) -> str:
    """'a4' -> 'a4_inactive', 'a3' -> 'a3_profile'."""
    return COMPETITOR.get(agent) or SPECS[agent].agent


# ---------------------------------------------------------------------------- component entry points
def _hids(state: dict, agent: str) -> list[str | None]:
    if agent not in PER_CARD:
        return [None]
    if not state.get("cards"):
        raise ValueError(f"{agent.upper()} needs A2's cards: connect the A2 steps first")
    return [c["id"] for c in state["cards"]]


def _status(state: dict, start: int) -> str:
    rows = state["steps"][start:]
    def line(r: dict) -> str:  # the step detail already starts with the card id for A3/A6
        h = r.get("hid")
        return r["detail"] if (not h or r["detail"].startswith(h)) else f"{h} · {r['detail']}"

    return " | ".join(line(r) for r in rows)[:600]


def chat_text(before: dict, after: dict) -> str:
    """What this node did, in plain language (the messages it added to state['chat'])."""
    from p3graph import narrate as NR

    return NR.transcript((after.get("chat") or [])[len(before.get("chat") or []):])


async def execute(st: dict, agent: str, step: str, *, feedback: str | None = None, system_prompt: str | None = None,
                  model: str | None = None) -> tuple[dict, str]:
    st = copy.deepcopy(st)
    start = len(st.get("steps") or [])
    if feedback and step == "prompt":  # one key per card for A3/A6 so each card consumes it once
        keys = [N.fb_key(_name(agent), h) for h in _hids(st, agent)]
        st["feedback"] = {**(st.get("feedback") or {}), **{k: feedback for k in keys}}
    if step == "prompt" and system_prompt and system_prompt.strip() != default_prompt(_name(agent)).strip():
        st["prompts"] = {**(st.get("prompts") or {}), _name(agent): system_prompt}
    saved = (st.get("llm") or {}).get("model")
    if model and model != "inherit" and step in ("llm", "parse", "validate"):  # these steps can call the model
        st["llm"] = {**st["llm"], "model": model}
    await _run_cards(st, agent, step)
    if model and model != "inherit":
        st["llm"] = {**st["llm"], "model": saved}
    st["current"] = None
    if step in ("commit", "route"):
        N.write_workbook(st)
    return st, _status(st, start)


NO_INSTRUCTION = {"", "run", "go", "start", "run it", "run the analysis"}


# ---------------------------------------------------------------------------- cards in parallel
def _usage_delta(new: dict, old: dict) -> dict:
    out = {}
    for a, u in (new or {}).items():
        o = (old or {}).get(a, {})
        d = {k: (u[k] - o.get(k, 0)) if isinstance(u.get(k), (int, float)) else u.get(k) for k in u}
        if d.get("calls"):
            out[a] = d
    return out


def _deep_changes(cur: dict, new: dict, old: dict) -> dict:
    out = dict(cur)
    for k, v in new.items():
        o = old.get(k)
        if isinstance(v, dict) and isinstance(cur.get(k), dict):
            out[k] = _deep_changes(cur[k], v, o if isinstance(o, dict) else {})
        elif o != v:
            out[k] = v
    return out


def _merge_back(st: dict, sub: dict, base: dict) -> None:
    """Fold what one card's copy of the state changed back into the main state (reducer rules)."""
    for k, v in sub.items():
        if k == "current":
            continue
        r, b0 = REDUCERS.get(k), base.get(k)
        if r is operator.add:
            st[k] = list(st.get(k) or []) + list(v)[len(b0 or []):]
        elif r is merge_usage:
            st[k] = merge_usage(st.get(k), _usage_delta(v, b0))
        elif k == "signals":
            st[k] = _deep_changes(st.get(k) or {}, v, b0 or {})
        elif r is merge:
            st[k] = {**(st.get(k) or {}), **{kk: vv for kk, vv in v.items() if (b0 or {}).get(kk) != vv}}
        elif v != b0:
            st[k] = v


async def _run_cards(st: dict, agent: str, step: str) -> None:
    """One step for every card. A3/A6 cards are independent, so they run at once (up to MAX_PARALLEL)."""
    hids = _hids(st, agent)
    if agent not in PER_CARD or len(hids) < 2 or MAX_PARALLEL < 2:
        for hid in hids:
            await run_step(st, agent, step, hid)
        return
    base = copy.deepcopy(st)
    gate = asyncio.Semaphore(MAX_PARALLEL)

    async def one(hid: str) -> dict:
        async with gate:
            sub = copy.deepcopy(base)  # each card works on its own copy: `current` is per card
            await run_step(sub, agent, step, hid)
            return sub

    subs = await asyncio.gather(*(one(h) for h in hids))
    for sub in subs:  # merge in card order so steps, decisions and events stay deterministic
        _merge_back(st, sub, base)


async def setup(config: dict, llm: dict, retrieval: str, message: str = "", search: str = "corpus") -> tuple[dict, str]:
    st: dict = {"config": config, "llm": llm, "retrieval": retrieval, "search": search}
    note = ""
    msg = (message or "").strip()
    if msg.lower().rstrip(".!") not in NO_INSTRUCTION:  # a real instruction: every agent's prompt gets it
        st["feedback"] = {a: f"User instruction for this run: {msg}"
                          for a in ("a1_disease", "a2_mechanism", "a3_profile", "a6_redteam")}
        note = f" · instruction: {msg[:60]}"
    apply(st, await N.setup(st))
    return st, f"run {st['run_id'][:8]} · as_of {st['config']['as_of']} · llm {st['llm']['mode']} · search {st['search']}{note}"


async def report(st: dict) -> tuple[dict, str]:
    st = copy.deepcopy(st)
    apply(st, await N.report(st))
    from p3graph import narrate as NR

    st["report"] += "\n\n---\n\n## What happened, node by node\n\n```\n" + NR.transcript(st.get("chat") or []) + "\n```"
    return st, st["workbook_path"]


async def execute_custom(st: dict, *, name: str, after: str, node: str, params: str) -> tuple[dict, str]:
    """A node you added on the canvas: your function from custom_nodes.py, run where you put it."""
    st = copy.deepcopy(st)
    start = len(st.get("steps") or [])
    item = {"name": name, "after": after, "node": node,
            "params": json.loads(params) if params.strip() else {}}
    agent = after.split("_")[0] if after.split("_")[0] in ORDER else None
    step = after.split("_", 1)[1] if agent else None
    fn = WF.make_node(item, "__next__")
    for hid in (_hids(st, agent) if agent else [None]):
        if agent in PER_CARD:
            st["current"] = hid
        for _ in range(6):  # a loop-back node gets at most this many rounds
            res = await fn(st)
            goto = res.goto
            apply(st, res.update)
            if goto in ("__next__", None, "__end__"):
                break
            # jumped back: rerun the steps from the target up to the step this node sits after
            g_agent, g_step = goto.split("_", 1)
            if g_agent != agent or g_step not in ORDER[agent]:
                raise ValueError(f"{name}: can only jump back inside {agent}, got '{goto}'")
            await run_range(st, agent, hid, g_step, step)
    st["current"] = None
    return st, _status(st, start)


def default_prompt(agent: str) -> str:
    """The engine's system prompt, shown (editable) in the Prompt nodes."""
    return E.system_prompt(agent)
