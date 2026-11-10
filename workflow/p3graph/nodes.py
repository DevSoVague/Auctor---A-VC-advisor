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
            "cite_mode": state.get("cite_mode") or ("node" if state.get("cite") is not False else "off"),
            "pitch": state.get("pitch"), "store_pitch": bool(state.get("store_pitch")),
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
        return Command(goto="a7_final", update={"current": None, "chat": chat, "steps": [
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
async def final_citations(state: P3State) -> dict:
    """cite_mode 'end': one A7 pass over every committed output (no restarts). Unsupported sources are
    stripped from the outputs in state; the ledger goes to the Citations sheet."""
    from p3graph import steps as S

    if S.cite_mode(state) != "end":
        return {}
    run = AgentRun(state, "a7_citations")
    C = E.a7_citations
    upd: dict = {}
    jobs = []
    if state.get("why_now"):
        jobs.append(("a1_disease", None, E.WhyNow, state["why_now"], "why_now"))
    if state.get("cards"):
        jobs.append(("a2_mechanism", None, E.A2Output, {"cards": state["cards"]}, "cards"))
    for hid, v in (state.get("profiles") or {}).items():
        jobs.append(("a3_profile", hid, E.ProductProfile, v, "profiles"))
    for hid, v in (state.get("verdicts") or {}).items():
        jobs.append(("a6_redteam", hid, E.Verdict, v, "verdicts"))
    n_bad = n = 0
    for agent, hid, schema, data, key in jobs:
        out = schema.model_validate(data)
        res = await C.check(agent, out, None, run.ctx, hid)
        C.apply(out, res)
        n, n_bad = n + res["checked"], n_bad + len(res["hallucinated"])
        d = out.model_dump(mode="json")
        if key == "cards":
            upd["cards"] = d["cards"]
        elif hid:
            upd.setdefault(key, {})[hid] = d
        else:
            upd[key] = d
    out = run.update(**upd)
    out["steps"] = [{"node": "a7_final", "agent": "a7_citations", "hid": None, "ms": 0,
                     "detail": f"end-of-run citation check · {n} citations · {n_bad} unsupported (stripped)"}]
    return out


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
    # assets/judgments/revise_used/intake and the A7 ledger let the memo (person 5) rebuild a run
    # from this file alone; citation_checks are the 'citation_check' events, ledger rows only.
    result = {k: state.get(k) for k in ("run_id", "config", "llm", "intake", "why_now", "cards", "grades",
                                        "profiles", "assets", "verdicts", "judgments", "revise_used",
                                        "killed", "decisions", "usage", "signals", "steps", "chat")}
    result["citation_checks"] = [p for e, p in state.get("events") or [] if e == "citation_check"]
    (d / "result.json").write_text(json.dumps(result, indent=1, default=str))
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
    rows = ["# Step trace", "", "Per-step inputs and outputs are in `log.jsonl` (one line per step) next to this file.", "",
            "| # | node | card | ms | what happened |", "|---|---|---|---|---|"]
    for i, s in enumerate(state.get("steps") or [], 1):
        rows.append(f"| {i} | {s['node']} | {s.get('hid') or ''} | {s.get('ms', '')} | "
                    f"{str(s.get('detail', '')).replace('|', '/')[:140]} |")
    return "\n".join(rows) + "\n"


def _cell(text, limit: int = 0) -> str:
    """Safe inside a markdown table cell; shortened at a word boundary, never mid-word."""
    t = " ".join(str(text or "").split()).replace("|", "/")
    if limit and len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t


def _crowding(score) -> str:
    if score is None:
        return "not assessed"
    level = "low" if score < 0.34 else "medium" if score < 0.67 else "high"
    return f"{level} ({score:.2f})"


def _verdict_line(v: dict, j: dict, revised: bool) -> str:
    word = {"survives": "Survives", "killed": "Killed", "revise": "Needs revision"}.get(v.get("verdict"), v.get("verdict") or "not reviewed")
    capped = "revise cap reached" in str(v.get("premortem") or "")
    out = f"**{word}**" + (" · revision limit reached, the red team's top risk is still open" if capped
                           else " after one revision round" if revised else "")
    if j:
        out += f" · judge: red team is {str(j.get('red_team_is', '')).replace('_', ' ')}"
    return out


def report_md(state: P3State) -> str:
    """The run's answer, written for a reader: the hypotheses first (verdict, product bar, competition,
    licensable assets, biggest risk), then why now, then run details. Plumbing stays in the workbook/logs."""
    cfg, u = state["config"], state.get("usage", {})
    cards = state.get("cards") or []
    grades, profiles = state.get("grades") or {}, state.get("profiles") or {}
    verdicts, judgments = state.get("verdicts") or {}, state.get("judgments") or {}
    assets, revised = state.get("assets") or {}, state.get("revise_used") or {}
    failed = state.get("failed_cards") or {}
    killed = {k["hypothesis_id"]: k for k in state.get("killed") or []}
    done = [c for c in cards if c["id"] in verdicts or c["id"] in profiles or c["id"] in failed]
    survive = sum(1 for c in done if verdicts.get(c["id"], {}).get("verdict") == "survives")

    L = [f"# {cfg['disease']} · frontier hypotheses",
         f"As of **{cfg['as_of']}** · {len(done)} of {len(cards)} hypotheses analysed · "
         f"**{survive} survive** · {len(killed)} killed" + (f" · {len(failed)} failed" if failed else "")]
    if state["llm"]["mode"] == "stub":
        L += ["", "> **Stub run.** Fixture outputs, not real model judgments. Switch LLM mode to live for a real analysis."]

    # ---- at a glance
    L += ["", "## At a glance", "",
          "| # | Hypothesis | Evidence | Red team | Competition | Licensable assets |",
          "|---|---|---|---|---|---|"]
    for c in done:
        hid = c["id"]
        v, land, ina = verdicts.get(hid, {}), assets.get(f"{hid}:landscape"), assets.get(f"{hid}:inactive")
        verdict = ("failed" if hid in failed else
                   {"survives": "survives", "killed": "killed"}.get(v.get("verdict"), v.get("verdict") or "-"))
        if revised.get(hid) and verdict == "survives":
            verdict += " (revised)"
        comp = "-" if land is None else f"{_crowding(land.get('crowding_score'))} · {len(land.get('programs') or [])} active"
        lic = "-" if ina is None else (f"{len(ina.get('repurposing') or [])} repurpose · {len(ina.get('shelved') or [])} shelved"
                                       if (ina.get("repurposing") or ina.get("shelved")) else "none found")
        L.append(f"| {grades.get(hid, {}).get('rank', '')} | **{_cell(c['mechanism'])}** | "
                 f"tier {c['evidence_tier']} · {c['validation_level']} | {verdict} | {comp} | {lic} |")

    # ---- one section per hypothesis
    L += ["", "## The hypotheses"]
    for c in done:
        hid = c["id"]
        g, p, v, j = grades.get(hid, {}), profiles.get(hid), verdicts.get(hid, {}), judgments.get(hid, {})
        L += ["", f"### {g.get('rank', '')} · {c['mechanism']}",
              f"*{c.get('direction', '')} {', '.join(c.get('target_ids') or [])} · {c.get('modality', '')} · "
              f"tier {c['evidence_tier']} evidence ({c['validation_level']})*", ""]
        if hid in failed:
            L += [f"**Failed:** {_cell(failed[hid], 300)}"]
            continue
        L.append(_verdict_line(v, j, bool(revised.get(hid))))
        if j.get("reasoning"):
            L.append(f"> {_cell(j['reasoning'], 400)}")
        if hid in killed:
            L.append(f"\n**Killed:** {_cell(killed[hid].get('kill_criterion') or killed[hid].get('reason'), 300)}")
        if c.get("biggest_uncertainty"):
            L += ["", f"**Biggest uncertainty:** {_cell(c['biggest_uncertainty'])}"]
        if p:
            L += ["", "**What the product must do** (A3)"] + [f"- {_cell(m)}" for m in p.get("must_haves") or []]
            if p.get("soc_gap_at_launch"):
                L.append(f"- *Gap it fills at launch:* {_cell(p['soc_gap_at_launch'])}")
        risks = v.get("risks") or []
        if risks:
            r0 = risks[0]
            L += ["", "**Biggest risk** (A6 red team)", f"- {_cell(r0.get('text'))}"]
            if r0.get("kill_criterion"):
                L.append(f"- *Kills it if:* {_cell(r0['kill_criterion'])}")
            if r0.get("cheapest_test"):
                L.append(f"- *Cheapest test:* {_cell(r0['cheapest_test'])}")
            if len(risks) > 1:
                L.append("- *Other risks:* " + "; ".join(_cell(r.get("text"), 120) for r in risks[1:]))
        land = assets.get(f"{hid}:landscape")
        if land is not None:
            progs, cats = land.get("programs") or [], land.get("catalysts") or []
            L += ["", f"**Competition** (A5): crowding {_crowding(land.get('crowding_score'))} · "
                      f"{len(progs)} active program(s) on this mechanism, {len(land.get('orthogonal') or [])} on others"]
            for pr in progs[:4]:
                L.append(f"- {_cell(pr.get('asset'))} · {_cell(pr.get('owner'))} · {_cell(pr.get('stage'))}"
                         f" · fits the product: {pr.get('profile_fit', '-')}" + (f" · edge: {_cell(pr['novel_edge'], 90)}" if pr.get("novel_edge") else ""))
            for ct in cats[:3]:
                L.append(f"- *Readout {ct.get('on')}:* {_cell(ct.get('what'), 140)}")
        ina = assets.get(f"{hid}:inactive")
        if ina is not None:
            items = [("repurpose", a) for a in ina.get("repurposing") or []] + [("shelved", a) for a in ina.get("shelved") or []]
            if items:
                L += ["", "**Assets to license or revive** (A4)"]
                for kind, a in items[:5]:
                    L.append(f"- {_cell(a.get('name'))} ({kind}) · {_cell(a.get('owner'))} · {_cell(a.get('stage'))}"
                             f" · fit {float(a.get('profile_fit') or 0):.1f}" + (f" · {_cell(a['limitations'], 90)}" if a.get("limitations") else ""))
            else:
                L += ["", "**Assets to license or revive** (A4): none found in the evidence."]

    # ---- why now
    shifts = sorted((state.get("why_now") or {}).get("shifts", []), key=lambda s: str(s.get("on")))
    if shifts:
        L += ["", "## Why now (A1)", "", "| Date | Shift | What changed | Moves |", "|---|---|---|---|"]
        L += [f"| {s.get('on')} | {str(s.get('type', '')).replace('_', ' ')} | {_cell(s.get('summary'))} | "
              f"{', '.join(s.get('effect_on') or [])} |" for s in shifts]

    # ---- run details (condensed decision log)
    calls = sum(x["calls"] for x in u.values())
    tin = sum(x["input_tokens"] for x in u.values())
    tout = sum(x["output_tokens"] for x in u.values())
    model = state["llm"].get("model") or f"{state['llm']['profile']} profile"
    L += ["", "## Run details", "",
          f"- **Model:** {state['llm']['mode']} · {model} · {calls} calls · {tin:,} in / {tout:,} out tokens",
          f"- **Run:** `{state['run_id']}` · files in `runs/{state['run_id'][:8]}…/`: `analysis.xlsx` (workbook), "
          "`chat.md` (every step in plain language), `log.md` (full prompts and outputs)"]
    if state.get("decisions"):
        per: dict[str, list[str]] = {}
        for d in state["decisions"]:  # one line per card: who did what, in order
            card = str(d["target"]).split(":")[-1]
            step = f"{d['actor'].replace('_', ' ')} {d['action']}"
            if d["action"] == "override" and "cap" in str(d.get("reason", "")):
                step = "revision limit reached"
            per.setdefault(card, []).append(step)
        L += ["", "## Decision log", "", "| Card | What happened |", "|---|---|"]
        L += [f"| {card} | {' → '.join(steps)} |" for card, steps in per.items()]
    return "\n".join(L)


# ---------------------------------------------------------------------------- A0 intake (input gate)
async def intake(state: P3State):
    """Input gate for the startup pitch (state['pitch'] = path or URL). Rejected input ends the run here,
    before any agent spends tokens. No pitch: pass-through (the run scans for anything new, as before)."""
    src = state.get("pitch")
    if not src:
        return {"steps": [{"node": "intake", "agent": "a0_intake", "hid": None, "ms": 0, "detail": "no pitch: open scan"}]}
    import time as _t

    t0 = _t.time()
    run = AgentRun(state, "a0_intake")
    rep = await E.a0_intake.gate(src, run.ctx.config, run.ctx, store=bool(state.get("store_pitch")),
                                 run_dir=RUNS / state["run_id"])
    d = rep.model_dump(mode="json")
    run.ctx.emit("intake_done", {"agent": "a0_intake", "report": d})
    st = {}
    for c in rep.cited:
        st[c["status"]] = st.get(c["status"], 0) + 1
    detail = (f"accepted · {rep.kind} · {rep.chars:,} chars · cited {st or 'none'}"
              + (f" · {len(rep.injection_flags)} injection flag(s)" if rep.injection_flags else "")
              if rep.accepted else "REJECTED: " + "; ".join(rep.reasons))
    upd = run.update(intake=d)
    upd["steps"] = [{"node": "intake", "agent": "a0_intake", "hid": None, "ms": int((_t.time() - t0) * 1000),
                     "detail": detail}]
    if not rep.accepted:
        upd["report"] = f"# Run stopped at the input gate\n\nPitch `{src}` was rejected: " + "; ".join(rep.reasons)
    return upd
