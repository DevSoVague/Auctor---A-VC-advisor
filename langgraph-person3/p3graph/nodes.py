"""LangGraph nodes for person 3's agents.

Agent nodes run the tested agents from ../frontier-engine and stream every agent event
(stream_mode="custom"). Review nodes are PRD checkpoints: in guided mode they pause the
graph with interrupt(); the reviewer resumes with Command(resume={"action": ...}).
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from langgraph.types import Command, interrupt
from pydantic import TypeAdapter

from p3graph import engine as E
from p3graph import narrate as NR
from p3graph import live
from p3graph.state import P3State

RUNS = Path(__file__).resolve().parents[1] / "runs"
MAX_RERUNS = 2  # PRD: rerun at most twice per agent output
PER_CARD = {"a3_profile", "a6_redteam"}

# Tests can swap the LLM: nodes.LLM_FACTORY = lambda state: StubLLM()
LLM_FACTORY: Callable[[dict], Any] | None = None


# ---------------------------------------------------------------------------- helpers
def make_llm(state: P3State):
    if LLM_FACTORY:
        return LLM_FACTORY(state)
    cfg = state["llm"]
    if cfg["mode"] == "stub":
        return E.StubLLM()
    return E.NebiusLLM(cfg.get("profile", "dev"), model_override=cfg.get("model"))


def _writer():
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:  # noqa: BLE001 - outside a graph run
        return None


def fb_key(agent: str, hid: str | None) -> str:
    return f"{agent}:{hid}" if hid else agent


class AgentRun:
    """Builds a RunContext for one node, captures events/usage, returns a state update."""

    def __init__(self, state: P3State, agent: str, hid: str | None = None):
        cfg = E.RunConfig.model_validate(state["config"])
        retrieval = E.make_retrieval(cfg.as_of, state.get("retrieval", "stub"), state.get("search", "corpus"))
        fb = state.get("feedback", {})
        self.key = fb_key(agent, hid)
        self.feedback = fb.get(self.key) or fb.get(agent)
        self.ctx = E.RunContext(config=cfg, retrieval=retrieval, llm=make_llm(state),
                                run_id=state["run_id"], feedback=self.feedback,
                                signals=copy.deepcopy(state.get("signals", {})))
        self.events: list = []
        self._usage0 = {a: (u.calls, u.input_tokens, u.cached_tokens, u.output_tokens, u.latency_ms)
                        for a, u in getattr(self.ctx.llm, "usage", {}).items()}
        w = _writer()

        def sink(e: str, p: dict) -> None:
            self.events.append([e, p])
            if w:
                w({"event": e, **{k: v for k, v in p.items() if k != "output"}})

        self.ctx.bus.subscribe(sink)

    def usage_delta(self) -> dict:
        """Tokens spent by THIS node only (a shared LLM object keeps running totals)."""
        out = {}
        for a, u in getattr(self.ctx.llm, "usage", {}).items():
            c0, i0, k0, o0, l0 = self._usage0.get(a, (0, 0, 0, 0, 0))
            if u.calls - c0:
                out[a] = {"model": u.model, "calls": u.calls - c0, "input_tokens": u.input_tokens - i0,
                          "cached_tokens": u.cached_tokens - k0, "output_tokens": u.output_tokens - o0,
                          "latency_ms": u.latency_ms - l0}
        return out

    def update(self, *, consume: bool = False, **extra) -> dict:
        out = {"events": self.events, "signals": self.ctx.signals, "usage": self.usage_delta(), **extra}
        if consume and self.feedback:  # reviewer feedback is single-use: marked used by the step that sent it
            out["feedback"] = {**extra.get("feedback", {}), self.key: None}
        return out


def _card(state: P3State, hid: str) -> E.HypothesisCard:
    return E.HypothesisCard.model_validate(next(c for c in state["cards"] if c["id"] == hid))


# ---------------------------------------------------------------------------- setup
async def setup(state: P3State) -> dict:
    cfg = dict(state.get("config") or {})
    cfg.setdefault("disease", "COPD")
    cfg.setdefault("disease_id", "EFO_0000341")
    cfg.setdefault("mode", "single_shot")
    cfg.setdefault("profile", "dev")
    llm = {"mode": "stub", "model": None, "profile": cfg["profile"], **(state.get("llm") or {})}
    # Use today's date by default; explicit dates and the backtest alias remain available.
    cfg["as_of"] = E.resolve_as_of(cfg.get("as_of"), llm["mode"]).isoformat()
    cfg = E.RunConfig.model_validate(cfg).model_dump(mode="json")
    run_id = state.get("run_id") or str(uuid.uuid4())
    live.ensure_server()
    send = live.observer({"run_id": run_id}, "setup")
    send("start")
    send("end", {"detail": f"Run {run_id} · {cfg['disease']} · {llm['mode']}",
                 "result": {"config": cfg, "llm": llm}})
    row0 = {"node": "setup", "ms": 0, "detail": f"as_of {cfg['as_of']} · {cfg['mode']} · llm {llm['mode']}"
            + (" · tools on" if state.get("tools") else "") + (" · judge on" if state.get("judge") else "")}
    chat = [NR.say(_writer(), run_id, "Setup", "▸", "setup", NR.narrate_node("setup", row0), 0)]
    NR.log_full(run_id, "setup", None, row0, {"config": cfg, "llm": llm}, {})
    return {"chat": chat, "run_id": run_id, "config": cfg, "llm": llm,
            "retrieval": state.get("retrieval") or "stub",
            "search": state.get("search") or "corpus",
            "tools": bool(state.get("tools")), "judge": bool(state.get("judge")),
            "cite": state.get("cite") is not False,
            "max_cards": int(state.get("max_cards") or 0),
            "workbook_path": str(RUNS / run_id / "analysis.xlsx"),
            "steps": [{"node": "setup", "agent": None, "hid": None, "ms": 0,
                       "detail": f"as_of {cfg['as_of']} · {cfg['mode']} · llm {llm['mode']}"}],
            "events": [["run_started", {"run_id": run_id, "as_of": cfg["as_of"],
                                        "mode": cfg["mode"], "llm": llm}]]}


# ---------------------------------------------------------------------------- agents
async def next_card(state: P3State) -> Command:
    q = list(state.get("queue") or [])
    msg = (f"Picking the next hypothesis: {q[0]} ({len(q) - 1} left after it)." if q
           else "All hypotheses are done. Moving on to the report.")
    chat = [NR.say(_writer(), state["run_id"], "Next hypothesis", "▸", "next_card", msg, 0, q[0] if q else None)]
    if not q:
        return Command(goto="report", update={"current": None, "chat": chat, "steps": [
            {"node": "next_card", "agent": None, "hid": None, "ms": 0, "detail": "queue empty → report"}]})
    return Command(goto="a3_retrieve", update={"current": q[0], "queue": q[1:], "chat": chat, "steps": [
        {"node": "next_card", "agent": None, "hid": q[0], "ms": 0,
         "detail": f"next hypothesis {q[0]} ({len(q) - 1} left after it)"}]})


# ---------------------------------------------------------------------------- review checkpoints
EDIT_SCHEMA = {
    "a1_disease": ("why_now", E.WhyNow),
    "a2_mechanism": ("cards", list[E.HypothesisCard]),
    "a3_profile": ("profiles", E.ProductProfile),
    "a6_redteam": ("verdicts", E.Verdict),
}


def _checkpoint_payload(state: P3State, agent: str, hid: str | None) -> dict:
    key, _ = EDIT_SCHEMA[agent]
    output = state[key][hid] if hid else state[key]
    pc = state.get("signals", {}).get("postcheck", {}).get(agent, [])
    if isinstance(pc, dict):
        pc = pc.get(hid, [])
    summary = {
        "a1_disease": lambda: [f"{s['on']} {s['type']}: {s['summary']}" for s in output["shifts"]],
        "a2_mechanism": lambda: [f"{c['id']} t{c['evidence_tier']}/{c['validation_level']} {c['mechanism']}"
                                 for c in output],
        "a3_profile": lambda: output["must_haves"],
        "a6_redteam": lambda: [f"{output['verdict']}: " + "; ".join(r["text"] for r in output["risks"][:3])],
    }[agent]()
    return {"checkpoint": agent, "hypothesis_id": hid, "summary": summary,
            "citation_check": pc, "output": output,
            "actions": ["approve", "approve_all", "edit (edited_output)", "rerun (comment)"]}


def make_review(agent: str, next_node: str | list[str], rerun_node: str) -> Callable:
    async def review(state: P3State) -> Command:
        hid = state.get("current") if agent in PER_CARD else None
        if state["config"]["mode"] == "single_shot" or state.get("auto_approve", {}).get(agent):
            return Command(goto=next_node)

        d = interrupt(_checkpoint_payload(state, agent, hid)) or {}
        action = d.get("action", "approve")
        target = fb_key(agent, hid)
        dec = {"actor": "reviewer", "action": action, "target": target, "reason": d.get("comment", "")}
        update: dict[str, Any] = {"decisions": [dec]}

        if action == "approve_all":
            update["auto_approve"] = {agent: True}
        elif action == "edit":
            key, schema = EDIT_SCHEMA[agent]
            edited = TypeAdapter(schema).validate_python(d["edited_output"])
            dumped = ([c.model_dump(mode="json") for c in edited] if isinstance(edited, list)
                      else edited.model_dump(mode="json"))
            if hid:
                update[key] = {hid: dumped}
            else:
                update[key] = dumped
                if agent == "a2_mechanism":
                    update["queue"] = [c["id"] for c in dumped]
            if agent == "a6_redteam" and dumped["verdict"] == "kill" and \
                    not any(k["hypothesis_id"] == hid for k in state.get("killed", [])):
                update["killed"] = [{"hypothesis_id": hid, "mechanism": _card(state, hid).mechanism,
                                     "reason": "reviewer override", "kill_criterion": d.get("comment", "")}]
            update["events"] = [["agent_done", {"run_id": state["run_id"], "agent": agent,
                                                "hypothesis_id": hid, "output": dumped, "edited": True}]]
        elif action == "rerun":
            n = state.get("reruns", {}).get(target, 0)
            if n >= MAX_RERUNS:
                dec["reason"] += f" [rerun cap {MAX_RERUNS} reached; approved as is]"
            else:
                update["reruns"] = {target: n + 1}
                update["feedback"] = {target: d.get("comment") or "Reviewer asked for a rerun."}
                return Command(goto=rerun_node, update=update)
        return Command(goto=next_node, update=update)

    review.__name__ = f"review_{agent}"
    return review


# ---------------------------------------------------------------------------- report
async def report(state: P3State) -> dict:
    send = live.observer(state, "report")
    send("start")
    path = write_workbook(state)
    md = report_md(state)
    NR.say(_writer(), state["run_id"], "Report", "📄", "report",
           f"Run finished. Wrote the workbook, report.md, steps.md, chat.md, log.md and result.json to {Path(path).parent}.", 0)
    d = Path(path).parent
    (d / "report.md").write_text(md)
    (d / "steps.md").write_text(steps_md(state))
    (d / "result.json").write_text(json.dumps(
        {k: state.get(k) for k in ("run_id", "config", "llm", "why_now", "cards", "grades", "profiles",
                                   "verdicts", "killed", "decisions", "usage", "signals", "steps", "chat")},
        indent=1, default=str))
    send("end", {"detail": "Report and workbook saved", "result": {"report": md, "workbook_path": path}})
    return {"report": md, "workbook_path": path}


class _ReplayCtx:
    def __init__(self, state: P3State):
        self.run_id = state["run_id"]
        self.config = E.RunConfig.model_validate(state["config"])
        self.bus = E.EventBus()
        self.llm = type("LLM", (), {})()
        self.llm.usage = {a: E.Usage(**u) for a, u in (state.get("usage") or {}).items()}


def write_workbook(state: P3State) -> str:
    """Rebuild analysis.xlsx from every event in state (same workbook as the engine)."""
    wb = E.AnalysisWorkbook(_ReplayCtx(state), Path(state["workbook_path"]))
    wb.write = lambda: None
    for e, p in state.get("events", []):
        if "agent" in p or e == "claim_added":
            wb.on_event(e, p)
    del wb.write
    return str(wb.write())


def steps_md(state: P3State) -> str:
    """Every graph step in order: what ran, how long it took, what it did."""
    rows = ["# Step trace", "", "Per-step inputs and outputs are in `steps/NNN_<node>.json` next to this file.", "",
            "| # | node | card | ms | what happened |", "|---|---|---|---|---|"]
    for i, s in enumerate(state.get("steps") or [], 1):
        rows.append(f"| {i} | {s['node']} | {s.get('hid') or ''} | {s.get('ms', '')} | "
                    f"{str(s.get('detail', '')).replace('|', '/')[:140]} |")
    return "\n".join(rows) + "\n"


def report_md(state: P3State) -> str:
    cfg, u = state["config"], state.get("usage", {})
    calls = sum(x["calls"] for x in u.values())
    tin = sum(x["input_tokens"] for x in u.values())
    tout = sum(x["output_tokens"] for x in u.values())
    lines = [f"# Person 3 analysis · {cfg['disease']} · as of {cfg['as_of']}",
             f"Run `{state['run_id']}` · mode {cfg['mode']} · LLM {state['llm']['mode']} "
             f"({state['llm'].get('model') or state['llm']['profile'] + ' profile'}) · "
             f"{calls} calls · {tin:,} in / {tout:,} out tokens", "", "## Q1 Why now"]
    for s in (state.get("why_now") or {}).get("shifts", []):
        lines.append(f"- **{s['on']} · {s['type']}** ({', '.join(s['effect_on'])}): {s['summary']}")
    lines += ["", "## Q2 Mechanisms and Q5 verdicts", "",
              "| # | Hypothesis | Tier / level | Verdict | Top risk | Cheapest test |",
              "|---|---|---|---|---|---|"]
    for c in state.get("cards", []):
        hid, g = c["id"], state.get("grades", {}).get(c["id"], {})
        v = state.get("verdicts", {}).get(hid, {})
        r0 = (v.get("risks") or [{}])[0]
        rev = " (revised)" if state.get("revise_used", {}).get(hid) else ""
        lines.append(f"| {g.get('rank', '')} | {c['mechanism']} | {c['evidence_tier']} / {c['validation_level']} "
                     f"| {v.get('verdict', '-')}{rev} | {r0.get('text', '')[:80]} | {r0.get('cheapest_test', '')[:60]} |")
    lines += ["", "## Q3 Must-haves (TPP)"]
    for hid, p in (state.get("profiles") or {}).items():
        lines.append(f"- **{hid}**: " + "; ".join(p["must_haves"]))
    if state.get("killed"):
        lines += ["", "## Killed"] + [f"- **{k['hypothesis_id']}** {k['mechanism']}: {k['kill_criterion']}"
                                      for k in state["killed"]]
    if state.get("decisions"):
        lines += ["", "## Decision log", "", "| actor | action | target | reason |", "|---|---|---|---|"]
        lines += [f"| {d['actor']} | {d['action']} | {d['target']} | {str(d.get('reason', ''))[:90]} |"
                  for d in state["decisions"]]
    lines += ["", f"Workbook: `{state.get('workbook_path')}`",
              "", "_Next (other persons): A7 citation gate over every surviving claim, then A8 memo._"]
    return "\n".join(lines)
