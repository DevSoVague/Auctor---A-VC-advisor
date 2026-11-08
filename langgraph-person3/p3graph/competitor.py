"""A4 and A5 as LangGraph step nodes, one node per agent step (like A1/A2/A3/A6).

    A4: gather → triage → establish → prompt → llm → merge → commit
    A5: gather → shortlist → establish → prompt → llm → merge → score → commit

Each node runs exactly one step of the agent in ../agents/competitor (the same functions
`a4_inactive.run` / `a5_active.run` chain together), so behaviour is identical to the plain run.

The agents' working state holds live objects (records, candidates, the context), which the
graph checkpointer cannot store. It is kept in memory per (run, agent, card) between the step
nodes; graph state gets the JSON outputs, a step row, a chat line and a debug file per step.
A4/A5 never span a review pause (they sit between review_a3 and review_a6), so one process
always runs them start to finish. If the memory is missing anyway, the earlier steps are replayed.
"""

from __future__ import annotations

import json
import time
from typing import Any

from agents.analysis._common import emit_claims
from agents.analysis.schemas import A4Input, A5Input
from agents.competitor import a4_inactive, a5_active
from agents.competitor.context import for_agents
from agents.competitor.steps import jsonable

from p3graph import engine as E
from p3graph import narrate as NR
from p3graph import nodes as N
from p3graph import live
from p3graph.state import P3State

AGENTS = {
    "a4": {"agent": "a4_inactive", "module": a4_inactive, "input": A4Input, "kind": "inactive",
           "steps": ["gather", "triage", "establish", "prompt", "llm", "merge", "commit"]},
    "a5": {"agent": "a5_active", "module": a5_active, "input": A5Input, "kind": "landscape",
           "steps": ["gather", "shortlist", "establish", "prompt", "llm", "merge", "score", "commit"]},
}
_MEMORY: dict[tuple[str, str, str], dict] = {}  # (run_id, agent, hid) -> the agent's step state


def node_name(short: str, step: str) -> str:
    return f"{short}_{step}"


def first(short: str) -> str:
    return node_name(short, AGENTS[short]["steps"][0])


def last(short: str) -> str:
    return node_name(short, AGENTS[short]["steps"][-1])


# ---------------------------------------------------------------------------- one line per step
def _detail(short: str, step: str, s: dict, usage: dict) -> str:
    if step == "gather":
        if short == "a4":
            on = sum(v == "on" for v in s["placements"].values())
            return (f"{len(s['candidates'])} drugs on or near the mechanism ({on} placed by code, "
                    f"{len(s['candidates']) - on} left for the model to place)")
        return f"{len(s['candidates'])} assets in the disease (trials + approvals, named through ChEMBL)"
    if step == "triage":
        rep = sum(x.group == "repurposing" for x in s["statuses"])
        return (f"{rep} repurposing, {len(s['statuses']) - rep} shelved · "
                f"{len(s['candidates']) - len(s['statuses'])} active in the disease (left to A5)")
    if step == "shortlist":
        groups = [g for _, g in s["shortlisted"]]
        return (f"{groups.count('programs')} on-mechanism, {groups.count('orthogonal')} other-mechanism, "
                f"{groups.count('open')} for the model to place · {len(s['cut'])} over the caps")
    if step == "establish":
        cut = f" · {len(s['cut'])} over the cap" if short == "a4" and s.get("cut") else ""
        return f"facts fixed by code for {len(s['facts'])} assets (stage, owner, last activity, evidence){cut}"
    if step == "prompt":
        if s["messages"] is None:
            return "nothing to judge: no model call"
        chars = sum(len(m["content"]) for m in s["messages"])
        extra = " · with reviewer feedback" if s.get("feedback") else ""
        return f"{len(s['messages'])} messages, {chars:,} chars{extra}"
    if step == "llm":
        u = next(iter(usage.values()), None)
        call = (f"{u['model']} · {u['input_tokens']:,} in / {u['output_tokens']:,} out · {u['latency_ms']} ms"
                if u else "no model call")
        d = s["draft"]
        got = (f"{len(d.repurposing)} repurposing, {len(d.shelved)} shelved" if short == "a4"
               else f"{len(d.programs)} on-mechanism, {len(d.orthogonal)} other-mechanism")
        return f"{call} · model kept {got}"
    if step == "merge":
        m = s["matching"]
        if short == "a4":
            got = f"{len(s['inactive'].repurposing)} repurposing, {len(s['inactive'].shelved)} shelved"
        else:
            got = f"{len(s['kept']['programs'])} on-mechanism, {len(s['kept']['orthogonal'])} other-mechanism"
        return (f"code's facts re-imposed · {got} · dropped by model: {len(m['dropped'])} · "
                f"model replies matching no asset: {len(m['unmatched'])}")
    if step == "score":
        ls = s["landscape"]
        return f"crowding {ls.crowding_score} · {len(ls.catalysts)} readouts in the next 12 months"
    if step == "commit":
        if short == "a4":
            out = s["inactive"]
            return f"{len(out.repurposing)} repurposing, {len(out.shelved)} shelved"
        out = s["landscape"]
        return (f"{len(out.programs)} on-mechanism, {len(out.orthogonal)} other-mechanism, "
                f"crowding {out.crowding_score}, {len(out.catalysts)} readouts")
    return "done"


# ---------------------------------------------------------------------------- the node factory
def _fresh(short: str, state: P3State, run: N.AgentRun, hid: str) -> dict:
    cfg = AGENTS[short]
    inp = cfg["input"](card=N._card(state, hid), profile=E.ProductProfile.model_validate(state["profiles"][hid]))
    return cfg["module"].inputs(inp, for_agents(run.ctx))


async def _replay(short: str, state: P3State, run: N.AgentRun, hid: str, upto: str) -> dict:
    """Memory lost (e.g. a different process): rerun the earlier steps to rebuild the state."""
    s = _fresh(short, state, run, hid)
    steps = dict(AGENTS[short]["module"].STEPS)
    for step in AGENTS[short]["steps"]:
        if step == upto:
            break
        if step in steps:
            s |= await steps[step](s)
    run.events.append(["warning", {"agent": AGENTS[short]["agent"], "hypothesis_id": hid,
                                   "msg": f"step memory missing; replayed steps before {upto}"}])
    return s


def make_node(short: str, step: str):
    cfg = AGENTS[short]
    agent, module = cfg["agent"], cfg["module"]
    fns = dict(module.STEPS)
    name = node_name(short, step)

    async def node(state: P3State) -> dict:
        hid = state["current"]
        key = (state["run_id"], agent, hid)
        run = N.AgentRun(state, agent, hid)
        model = ("stub" if isinstance(run.ctx.llm, E.StubLLM) else
                 getattr(run.ctx.llm, "model_override", None) or
                 E.model_for(agent, getattr(run.ctx.llm, "profile", "dev")))
        send = live.observer(state, name, agent, hid, model if step == "llm" else None)
        send("start")
        if hasattr(run.ctx.llm, "on_stream"):
            run.ctx.llm.on_stream = send
        try:
            t0 = time.time()
            if step == "gather":
                run.ctx.emit("agent_started", {"agent": agent, "hypothesis_id": hid})
                s = _fresh(short, state, run, hid)
            else:
                s = _MEMORY.get(key) or await _replay(short, state, run, hid, step)
            if step in ("prompt", "llm") and s.get("messages"):
                send("messages", {"messages": s["messages"]})
            if step == "gather":
                send("inputs", {"inputs": s["inp"].model_dump(mode="json")})
            s["ctx"] = for_agents(run.ctx)  # this node's model + bus, so its tokens are counted here
            s["feedback"] = run.feedback
            s["system"] = (state.get("prompts") or {}).get(agent)
            extra: dict[str, Any] = {}
            if step == "commit":
                out = s[cfg["kind"]]
                emit_claims(run.ctx, agent, out, hid)
                if short == "a5":
                    run.ctx.signals.setdefault("crowding", {})[hid] = out.crowding_score
                run.ctx.emit("agent_done", {"agent": agent, "hypothesis_id": hid, "output": out.model_dump(mode="json")})
                extra["assets"] = {f"{hid}:{cfg['kind']}": out.model_dump(mode="json")}
                _MEMORY.pop(key, None)
                changed = {cfg["kind"]: out}
            else:
                changed = await fns[step](s)
                s |= changed
                _MEMORY[key] = s
            if step == "prompt" and s.get("messages"):
                send("messages", {"messages": s["messages"]})
            ms = int((time.time() - t0) * 1000)
            usage = run.usage_delta()
            row = {"node": name, "agent": agent, "hid": hid, "ms": ms, "detail": f"{hid} · {_detail(short, step, s, usage)}"}
            data = {"detail": row["detail"], "result": jsonable(changed)}
            if step == "llm" and isinstance(run.ctx.llm, E.StubLLM) and s.get("messages"):
                data["fixture"] = s["draft"].model_dump_json(indent=2)
            send("end", data)
            _dump(state, name, hid, row, changed, s if step == "prompt" else None)
            upd = run.update(consume=(step == "commit"), **extra)
            try:  # narration and the full log are best effort, like the other agents' steps
                upd["chat"] = [NR.say(N._writer(), state["run_id"], NR.cap(agent, hid, step),
                                      NR.STEP_ICON.get(step, "▸"), name, row["detail"], ms, hid)]
                NR.log_full(state["run_id"], name, hid, row, jsonable(changed),
                            {"messages": s["messages"]} if step == "prompt" and s.get("messages") else {})
            except Exception:  # noqa: BLE001
                pass
            upd["steps"] = [row]
            return upd
        except BaseException as exc:
            send("error", {"error": f"{type(exc).__name__}: {exc}"})
            raise

    node.__name__ = name
    return node


def _dump(state: P3State, name: str, hid: str, row: dict, changed: dict, s: dict | None) -> None:
    try:
        d = N.RUNS / state["run_id"] / "steps"
        d.mkdir(parents=True, exist_ok=True)
        n = len(state.get("steps") or []) + 1
        body = {**row, "changed": jsonable(changed)}
        if s is not None and s.get("messages"):
            body["messages"] = s["messages"]
        (d / f"{n:03d}_{name}_{hid}.json").write_text(json.dumps(body, indent=1, default=str))
    except OSError:
        pass  # debug files are best effort


def add_to_graph(g, W, after: str, before: str) -> None:
    """Register A4's and A5's step nodes and wire after → A4 steps → A5 steps → before (sequential)."""
    prev = after
    for short in ("a4", "a5"):
        for step in AGENTS[short]["steps"]:
            name = node_name(short, step)
            g.add_node(name, make_node(short, step))
            if prev != after:  # `after` (a review node) routes itself with Command(goto=...)
                g.add_edge(prev, W.to(prev, name))
            prev = name
    g.add_edge(prev, W.to(prev, before))
