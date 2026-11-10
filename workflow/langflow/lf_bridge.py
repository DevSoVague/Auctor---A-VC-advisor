"""Bridge between Langflow nodes and person 3's agents in ../frontier-engine.

Langflow passes one `state` dict (inside a Data object) from node to node:
    config, llm{mode, model, profile}, retrieval, run_id,
    why_now, cards, grades, profiles{hid}, verdicts{hid}, revised[], killed[],
    events[(event, payload)], signals, usage{agent: {...}}, workbook_path

Each node rebuilds a RunContext from the state, runs its agent(s), appends the events,
merges usage, and rewrites the live workbook (runs/<run_id>/analysis.xlsx).
"""

from __future__ import annotations

import copy
import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = Path(os.getenv("FRONTIER_ENGINE_PATH", HERE.parent.parent)).resolve()
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE / ".env")

from agents.analysis.context import (  # noqa: E402
    EventBus, NebiusLLM, RunContext, StubLLM, Usage, resolve_as_of,
)
from agents.analysis.retrieval import HttpRetrieval, StubRetrieval  # noqa: E402
from agents.analysis.schemas import RunConfig  # noqa: E402
from exports.analysis_workbook import AnalysisWorkbook  # noqa: E402

NEBIUS_MODELS = [
    "profile default",
    "openai/gpt-oss-120b",
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "Qwen/Qwen3.5-397B-A17B",
    "zai-org/GLM-5.1",
]


def new_state(*, disease: str, disease_id: str, as_of: str, llm_mode: str, model: str,
              profile: str, retrieval: str, audience: str) -> dict:
    d = resolve_as_of(as_of, llm_mode)
    cfg = RunConfig(disease=disease, disease_id=disease_id, as_of=d, mode="single_shot",
                    profile=profile, audience=audience)
    run_id = str(uuid.uuid4())
    return {"run_id": run_id, "config": cfg.model_dump(mode="json"),
            "llm": {"mode": llm_mode, "model": None if model == "profile default" else model,
                    "profile": profile},
            "retrieval": retrieval.strip() or "stub",
            "why_now": None, "cards": [], "grades": {}, "profiles": {}, "verdicts": {},
            "revised": [], "killed": [], "events": [], "signals": {}, "usage": {},
            "workbook_path": str(ENGINE / "runs" / run_id / "analysis.xlsx")}


def make_ctx(state: dict, feedback: str | None = None) -> RunContext:
    cfg = RunConfig.model_validate(state["config"])
    llm_cfg = state["llm"]
    llm = StubLLM() if llm_cfg["mode"] == "stub" else NebiusLLM(
        llm_cfg["profile"], model_override=llm_cfg["model"])
    retrieval = (StubRetrieval(cfg.as_of) if state["retrieval"] == "stub"
                 else HttpRetrieval(cfg.as_of, state["retrieval"]))
    ctx = RunContext(config=cfg, retrieval=retrieval, llm=llm, run_id=state["run_id"],
                     feedback=feedback or None, signals=copy.deepcopy(state["signals"]))
    ctx.bus.subscribe(lambda e, p: state["events"].append([e, p]))
    return ctx


def finish(state: dict, ctx: RunContext) -> dict:
    """Merge usage + signals into state and rewrite the workbook from all events so far."""
    state["signals"] = ctx.signals
    for agent, u in getattr(ctx.llm, "usage", {}).items():
        prev = state["usage"].get(agent, {})
        state["usage"][agent] = {
            "model": u.model, "calls": prev.get("calls", 0) + u.calls,
            "input_tokens": prev.get("input_tokens", 0) + u.input_tokens,
            "cached_tokens": prev.get("cached_tokens", 0) + u.cached_tokens,
            "output_tokens": prev.get("output_tokens", 0) + u.output_tokens,
            "latency_ms": prev.get("latency_ms", 0) + u.latency_ms}
    write_workbook(state)
    return state


class _ReplayCtx:
    """Minimal ctx the workbook needs: run_id, config, llm.usage, bus."""

    def __init__(self, state: dict):
        self.run_id = state["run_id"]
        self.config = RunConfig.model_validate(state["config"])
        self.bus = EventBus()
        self.llm = type("LLM", (), {})()
        self.llm.usage = {a: Usage(**u) for a, u in state["usage"].items()}


def write_workbook(state: dict) -> str:
    """Replay every event so far into a fresh workbook, then write it once."""
    wb = AnalysisWorkbook(_ReplayCtx(state), Path(state["workbook_path"]))
    wb.write = lambda: None  # suppress per-event writes during replay
    for e, p in state["events"]:
        wb.on_event(e, p)
    del wb.write  # restore the class method
    return str(wb.write())


def usage_line(state: dict) -> str:
    u = state["usage"]
    calls = sum(x["calls"] for x in u.values())
    tin = sum(x["input_tokens"] for x in u.values())
    tout = sum(x["output_tokens"] for x in u.values())
    return f"{calls} calls · {tin:,} in · {tout:,} out tokens"


def report_md(state: dict) -> str:
    cfg = state["config"]
    lines = [f"## Person 3 analysis: {cfg['disease']} · as of {cfg['as_of']}",
             f"Run `{state['run_id']}` · LLM: {state['llm']['mode']} "
             f"({state['llm']['model'] or state['llm']['profile'] + ' profile'}) · {usage_line(state)}",
             "", "### Q1 Why now"]
    for s in (state["why_now"] or {}).get("shifts", []):
        lines.append(f"- **{s['on']} · {s['type']}** ({', '.join(s['effect_on'])}): {s['summary']}")
    lines += ["", "### Q2 Mechanisms (ranked) and Q5 verdicts", "",
              "| # | Hypothesis | Tier / level | Verdict | Top risk |", "|---|---|---|---|---|"]
    for c in state["cards"]:
        hid = c["id"]
        g = state["grades"].get(hid, {})
        v = state["verdicts"].get(hid, {})
        risk = (v.get("risks") or [{}])[0].get("text", "")
        lines.append(f"| {g.get('rank', '')} | {c['mechanism']} | {c['evidence_tier']} / "
                     f"{c['validation_level']} | {v.get('verdict', '-')}"
                     f"{' (revised)' if hid in state['revised'] else ''} | {risk[:90]} |")
    lines += ["", "### Q3 Must-haves (TPP)"]
    for hid, p in state["profiles"].items():
        lines.append(f"- **{hid}**: " + "; ".join(p["must_haves"]))
    if state["killed"]:
        lines += ["", "### Killed", *[f"- {k}" for k in state["killed"]]]
    lines += ["", f"Workbook: `{state['workbook_path']}`"]
    return "\n".join(lines)
