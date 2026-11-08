"""One LangGraph node per step of each agent, so a run can be inspected and debugged step by step.

Every agent is the same pipeline, split at the points where something can go wrong:

    retrieve → prompt → llm → parse → [postcheck ⇄ validate] → commit
                         ▲      │            │
                         └─ fix ◄─ schema    └─ fix ◄─ semantic problems     (one retry each)

    retrieve   deterministic evidence retrieval (what the model is allowed to cite)
    prompt     builds the messages (system prompt + payload + evidence pack + reviewer feedback)
    llm        ONE model call, raw text back (model, finish_reason, tokens, latency recorded)
    parse      JSON-schema validation of the raw text
    fix        appends the error to the messages for the single retry (schema or semantic)
    postcheck  citation post-check: strips bad sources, downgrades claims, caps confidence
    validate   the agent's own checks (PS bars, measurable must-haves, ...)
    commit     emits claims/events and writes the agent's output into graph state

A2 has `grade` (tier/level/gates/ranking in code) instead of `validate`; A6 has a `route` node
after `commit` (revise / kill / survive). All the logic lives in ../frontier-engine; these
nodes only call its step functions, so results are identical to `agent.run()`.

Each node keeps its working data in state["work"]["agent[:hid]"] and writes a debug file
runs/<run_id>/steps/NNN_<node>.json (what it changed, plus the full prompt / reasoning for `llm`).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.types import Command
from pydantic import ValidationError

from p3graph import engine as E
from p3graph import narrate as NR
from p3graph import nodes as N
from p3graph import workflow as WF
from p3graph import live
from p3graph.state import P3State


@dataclass(frozen=True)
class Spec:
    agent: str
    short: str  # node prefix: a1, a2, a3, a6
    mod: Any
    schema: Any
    per_card: bool

    def node(self, step: str) -> str:
        return f"{self.short}_{step}"


A1 = Spec("a1_disease", "a1", E.a1_disease, E.WhyNow, False)
A2 = Spec("a2_mechanism", "a2", E.a2_mechanism, E.A2Output, False)
A3 = Spec("a3_profile", "a3", E.a3_profile, E.ProductProfile, True)
A6 = Spec("a6_redteam", "a6", E.a6_redteam, E.Verdict, True)


@dataclass
class R:
    """What a step returns."""
    delta: dict  # changes to this agent's work area
    detail: str = ""  # one line for the step trace
    extra: dict = field(default_factory=dict)  # other state updates (why_now, cards, profiles, ...)
    goto: Any = None
    artifact: dict = field(default_factory=dict)  # written to the debug file only, not kept in state
    consume: bool = False  # mark single-use reviewer feedback as used


def step(spec: Spec, name: str, fresh: bool = False):
    """Wrap a step function as a graph node: run it, keep its work data, log it, dump it."""
    node_name = spec.node(name)

    def deco(fn):
        async def node(state: P3State):
            hid = state.get("current") if spec.per_card else None
            key = N.fb_key(spec.agent, hid)
            w = {} if fresh else dict((state.get("work") or {}).get(key, {}))
            run = N.AgentRun(state, spec.agent, hid)
            model = ("stub" if isinstance(run.ctx.llm, E.StubLLM) else
                     getattr(run.ctx.llm, "model_override", None) or
                     E.model_for(spec.agent, getattr(run.ctx.llm, "profile", "dev")))
            send = live.observer(state, node_name, spec.agent, hid,
                                 model if name == "llm" else None)
            send("start", {"messages": w.get("msgs", []) if name == "llm" else []})
            if hasattr(run.ctx.llm, "on_stream"):
                run.ctx.llm.on_stream = send
            t0 = time.time()
            try:
                res: R = await fn(state, run, w, hid)
            except BaseException as exc:
                send("error", {"error": f"{type(exc).__name__}: {exc}"})
                raise
            data = {"detail": res.detail, "result": res.delta}
            if name == "prompt":
                send("start", {"messages": res.delta.get("msgs", [])})
            if name == "llm" and isinstance(run.ctx.llm, E.StubLLM):
                data["fixture"] = res.delta.get("raw", {}).get("text", "")
            send("end", data)
            ms = int((time.time() - t0) * 1000)
            row = {"node": node_name, "agent": spec.agent, "hid": hid, "ms": ms, "detail": res.detail}
            _dump(state, node_name, hid, row, res)
            upd = run.update(consume=res.consume, **res.extra)
            upd["work"] = {key: {**w, **res.delta}}
            try:  # chat narration + full log are best effort and never change the run
                upd["chat"] = [NR.say(N._writer(), state["run_id"], NR.cap(spec.agent, hid, name),
                                      NR.STEP_ICON.get(name, "▸"), node_name,
                                      NR.narrate_step(spec.agent, name, hid, row, res.delta, res.artifact,
                                                      upd["work"][key]), ms, hid)]
                NR.log_full(state["run_id"], node_name, hid, row, res.delta, res.artifact)
            except Exception:  # noqa: BLE001
                pass
            upd["steps"] = [row]
            return Command(goto=res.goto, update=upd) if res.goto else upd

        node.__name__ = node_name
        return node

    return deco


def _dump(state: P3State, node_name: str, hid: str | None, row: dict, res: R) -> None:
    try:
        d = N.RUNS / state["run_id"] / "steps"
        d.mkdir(parents=True, exist_ok=True)
        n = len(state.get("steps") or []) + 1
        f = d / f"{n:03d}_{node_name}{'_' + hid if hid else ''}.json"
        f.write_text(json.dumps({**row, "changed": res.delta, **res.artifact}, indent=1, default=str))
    except OSError:
        pass  # debug files are best effort


def _system(state: P3State, agent: str) -> str | None:
    """Edited prompt: a prompt typed into a node (state['prompts']) wins over prompts/ files, which win over the default."""
    return (state.get("prompts") or {}).get(agent) or WF.system_for(agent)


def work(state: P3State, spec: Spec) -> dict:
    hid = state.get("current") if spec.per_card else None
    return (state.get("work") or {}).get(N.fb_key(spec.agent, hid), {})


def _pack(w: dict) -> E.EvidencePack:
    return E.EvidencePack.from_dict(w["pack"])


def _web(pack: E.EvidencePack) -> str:
    n = sum(1 for d in pack.items.values() if d.get("source") == "web")
    return f" ({n} from the web)" if n else ""


def _short(text: str, n: int = 90) -> str:
    return " ".join(str(text).split())[:n]


# ---------------------------------------------------------------------------- generic steps
def llm_step(spec: Spec):
    @step(spec, "llm")
    async def llm(state, run, w, hid):
        raw = await E.complete_raw(run.ctx.llm, agent=spec.agent, messages=w["msgs"], schema=spec.schema)
        reasoning = raw.pop("reasoning", "") or ""
        raw["reasoning_chars"] = len(reasoning)
        n = w.get("llm_calls", 0) + 1
        detail = (f"call {n} · {raw.get('model')} · {raw.get('finish_reason')} · "
                  f"{raw.get('input_tokens')} in / {raw.get('output_tokens')} out · {raw.get('latency_ms')} ms")
        if raw.get("finish_reason") == "length":
            detail += " · TRUNCATED (hit max_tokens)"
        return R({"raw": raw, "llm_calls": n}, detail,
                 artifact={"messages": w["msgs"], "reasoning": reasoning})

    return llm


def parse_step(spec: Spec):
    @step(spec, "parse")
    async def parse(state, run, w, hid):
        try:
            out = E.parse_output(w["raw"]["text"], spec.schema)
        except ValidationError as e:
            if w.get("schema_tries", 0) >= 1:  # PRD: retry once with the error, then fail loudly
                raise E.LLMError(f"{spec.agent}: output failed schema validation twice: {e}") from e
            return R({"out": None, "parse_error": str(e)[:2000]}, "schema error, will retry: " + _short(e, 100))
        return R({"out": out.model_dump(mode="json"), "parse_error": None}, f"valid {spec.schema.__name__}")

    return parse


def fix_step(spec: Spec):
    @step(spec, "fix")
    async def fix(state, run, w, hid):
        if w.get("parse_error"):
            msgs = E.fix_messages(w["msgs"], w["raw"]["text"], w["parse_error"])
            return R({"msgs": msgs, "schema_tries": w.get("schema_tries", 0) + 1, "parse_error": None},
                     "schema error appended to the prompt")
        problems = w.get("problems") or []
        msgs = w["msgs"] + [spec.mod.fix_prompt(problems)]
        return R({"msgs": msgs, "sem_tries": w.get("sem_tries", 0) + 1, "problems": []},
                 f"{len(problems)} problem(s) appended to the prompt")

    return fix


def route_parse(spec: Spec, ok: str):
    fix = spec.node("fix")

    def route(state: P3State) -> str:
        return fix if work(state, spec).get("parse_error") else ok

    return route


def route_validate(spec: Spec, ok: str):
    fix = spec.node("fix")

    def route(state: P3State) -> str:
        return fix if work(state, spec).get("problems") else ok

    return route


def _validate_result(spec: Spec, err_cls, out, problems: list[str], w: dict) -> R:
    if problems and w.get("sem_tries", 0) >= 1:  # one semantic retry, then fail loudly
        raise err_cls("; ".join(problems))
    detail = "ok" if not problems else f"{len(problems)} problem(s), will retry: " + _short("; ".join(problems), 110)
    return R({"problems": problems, "out": out.model_dump(mode="json")}, detail)


def _postcheck_detail(log: list[dict]) -> str:
    if not log:
        return "no changes needed"
    kinds: dict[str, int] = {}
    for x in log:
        kinds[x["action"]] = kinds.get(x["action"], 0) + 1
    return ", ".join(f"{n} {k}" for k, n in kinds.items())


# ---------------------------------------------------------------------------- A1 disease
@step(A1, "retrieve", fresh=True)
async def a1_retrieve(state, run, w, hid):
    run.ctx.emit("agent_started", {"agent": A1.agent, "hypothesis_id": None})
    pack, log = await E.a1_disease.retrieve(run.ctx.config, run.ctx)
    return R({"pack": pack.to_dict(), "retrieval_log": log},
             f"{len(pack.items)} docs kept{_web(pack)} from {len(log)} retrieval calls")


@step(A1, "prompt")
async def a1_prompt(state, run, w, hid):
    msgs = E.a1_disease.make_messages(run.ctx.config, _pack(w), run.ctx.feedback,
                                              system=_system(state, A1.agent))
    fb = " · with reviewer feedback" if run.ctx.feedback else ""
    return R({"msgs": msgs}, f"{len(msgs)} messages, {sum(len(m['content']) for m in msgs):,} chars{fb}",
             consume=True)


@step(A1, "postcheck")
async def a1_postcheck(state, run, w, hid):
    out = E.WhyNow.model_validate(w["out"])
    log = list(w.get("postcheck_log", [])) + E.a1_disease.finalize(out, run.ctx.config, _pack(w))
    return R({"out": out.model_dump(mode="json"), "postcheck_log": log},
             f"{len(out.shifts)} shifts kept · " + _postcheck_detail(log))


@step(A1, "validate")
async def a1_validate(state, run, w, hid):
    out = E.WhyNow.model_validate(w["out"])
    return _validate_result(A1, E.a1_disease.A1Error, out, E.a1_disease.check(out), w)


@step(A1, "commit")
async def a1_commit(state, run, w, hid):
    out = E.WhyNow.model_validate(w["out"])
    E.a1_disease.commit(run.ctx, out, w.get("postcheck_log", []))
    return R({}, f"{len(out.shifts)} shifts ({sum(s.type == 'negative' for s in out.shifts)} negative), "
                 f"{len(out.segments)} segments",
             extra={"why_now": out.model_dump(mode="json")})


# ---------------------------------------------------------------------------- A2 mechanism
@step(A2, "retrieve", fresh=True)
async def a2_retrieve(state, run, w, hid):
    run.ctx.emit("agent_started", {"agent": A2.agent, "hypothesis_id": None})
    r = await E.a2_mechanism.retrieve(E.WhyNow.model_validate(state["why_now"]), run.ctx)
    return R({"pack": r["pack"].to_dict(), "assoc": r["assoc"], "disease_drugs": r["disease_drugs"],
              "pub_counts": r["pub_counts"], "retrieval_log": r["log"]},
             f"{len(r['pack'].items)} docs{_web(r['pack'])}, {len(r['assoc'])} Open Targets targets, "
             f"{len(r['disease_drugs'])} drugs, {len(r['log'])} retrieval calls")


@step(A2, "prompt")
async def a2_prompt(state, run, w, hid):
    msgs = E.a2_mechanism.make_messages(E.WhyNow.model_validate(state["why_now"]), w["assoc"], _pack(w),
                                        run.ctx.feedback, run.ctx.config,
                                        system=_system(state, A2.agent))
    fb = " · with reviewer feedback" if run.ctx.feedback else ""
    return R({"msgs": msgs}, f"{len(msgs)} messages, {sum(len(m['content']) for m in msgs):,} chars{fb}",
             consume=True)


@step(A2, "grade")
async def a2_grade(state, run, w, hid):
    out = E.A2Output.model_validate(w["out"])
    g = E.a2_mechanism.grade_and_rank(out, w["assoc"], w["disease_drugs"], _pack(w), w["pub_counts"])
    cards = [c.model_dump(mode="json") for c in g["cards"]]
    tiers = ", ".join(f"{c['id']}:t{c['evidence_tier']}/{c['validation_level']}" for c in cards)
    return R({"cards": cards, "grades": g["grades"], "warning": g["warning"], "rejected": g["rejected"]},
             f"{len(out.cards)} proposed → {len(cards)} kept, {len(g['rejected'])} rejected · {tiers}")


@step(A2, "postcheck")
async def a2_postcheck(state, run, w, hid):
    cards = [E.HypothesisCard.model_validate(c) for c in w["cards"]]
    log = E.a2_mechanism.finalize(cards, _pack(w))
    return R({"cards": [c.model_dump(mode="json") for c in cards], "postcheck_log": log},
             f"{len(cards)} cards · " + _postcheck_detail(log))


@step(A2, "commit")
async def a2_commit(state, run, w, hid):
    cards = [E.HypothesisCard.model_validate(c) for c in w["cards"]]
    graded = {"cards": cards, "grades": w["grades"], "warning": w["warning"], "rejected": w["rejected"]}
    E.a2_mechanism.commit(run.ctx, cards, graded, w["pub_counts"], w.get("postcheck_log", []))
    return R({}, f"{len(cards)} ranked cards → queue {[c.id for c in cards]}",
             extra={"cards": [c.model_dump(mode="json") for c in cards], "grades": w["grades"],
                    "queue": [c.id for c in cards], "current": None})


# ---------------------------------------------------------------------------- A3 profile
@step(A3, "retrieve", fresh=True)
async def a3_retrieve(state, run, w, hid):
    card = N._card(state, hid)
    run.ctx.emit("agent_started", {"agent": A3.agent, "hypothesis_id": hid})
    pack, log = await E.a3_profile.retrieve(card, run.ctx)
    return R({"pack": pack.to_dict(), "retrieval_log": log},
             f"{hid} · {len(pack.items)} docs{_web(pack)} from {len(log)} retrieval calls")


@step(A3, "prompt")
async def a3_prompt(state, run, w, hid):
    msgs = E.a3_profile.make_messages(N._card(state, hid), _pack(w), run.ctx.feedback, run.ctx.config,
                                         system=_system(state, A3.agent))
    fb = " · with reviewer/red-team feedback" if run.ctx.feedback else ""
    return R({"msgs": msgs}, f"{hid} · {len(msgs)} messages, {sum(len(m['content']) for m in msgs):,} chars{fb}",
             consume=True)


@step(A3, "validate")
async def a3_validate(state, run, w, hid):
    out = E.ProductProfile.model_validate(w["out"])
    return _validate_result(A3, E.a3_profile.A3Error, out, E.a3_profile.check(out, N._card(state, hid)), w)


@step(A3, "postcheck")
async def a3_postcheck(state, run, w, hid):
    out = E.ProductProfile.model_validate(w["out"])
    log = E.a3_profile.finalize(out, N._card(state, hid), _pack(w))
    return R({"out": out.model_dump(mode="json"), "postcheck_log": log}, _postcheck_detail(log))


@step(A3, "commit")
async def a3_commit(state, run, w, hid):
    out = E.ProductProfile.model_validate(w["out"])
    E.a3_profile.commit(run.ctx, N._card(state, hid), out, w.get("postcheck_log", []))
    return R({}, f"{hid} · {len(out.must_haves)} must-haves",
             extra={"profiles": {hid: out.model_dump(mode="json")}})


# ---------------------------------------------------------------------------- A6 red team
def _a6_input(state: P3State, hid: str) -> E.RedTeamInput:
    assets = state.get("assets", {})
    return E.RedTeamInput(card=N._card(state, hid), profile=state["profiles"][hid], why_now=state["why_now"],
                          inactive=assets.get(f"{hid}:inactive"), landscape=assets.get(f"{hid}:landscape"))


@step(A6, "retrieve", fresh=True)
async def a6_retrieve(state, run, w, hid):
    run.ctx.emit("agent_started", {"agent": A6.agent, "hypothesis_id": hid})
    pack, checklist, log = await E.a6_redteam.retrieve(_a6_input(state, hid), run.ctx)
    found = {k: len(v) for k, v in checklist.items() if v}
    return R({"pack": pack.to_dict(), "checklist": checklist, "retrieval_log": log},
             f"{hid} · {len(pack.items)} docs{_web(pack)} · checklist hits: {found or 'none'}")


@step(A6, "prompt")
async def a6_prompt(state, run, w, hid):
    msgs = E.a6_redteam.make_messages(_a6_input(state, hid), _pack(w), w["checklist"],
                                      run.ctx.feedback, run.ctx.config,
                                      system=_system(state, A6.agent))
    fb = " · with reviewer feedback" if run.ctx.feedback else ""
    return R({"msgs": msgs}, f"{hid} · {len(msgs)} messages, {sum(len(m['content']) for m in msgs):,} chars{fb}",
             consume=True)


@step(A6, "validate")
async def a6_validate(state, run, w, hid):
    out = E.Verdict.model_validate(w["out"])
    return _validate_result(A6, E.a6_redteam.A6Error, out, E.a6_redteam.check(out, hid), w)


@step(A6, "postcheck")
async def a6_postcheck(state, run, w, hid):
    out = E.Verdict.model_validate(w["out"])
    log = E.a6_redteam.finalize(out, N._card(state, hid), _pack(w))
    return R({"out": out.model_dump(mode="json"), "postcheck_log": log}, _postcheck_detail(log))


@step(A6, "commit")
async def a6_commit(state, run, w, hid):
    out = E.Verdict.model_validate(w["out"])
    E.a6_redteam.commit(run.ctx, N._card(state, hid), out, w["checklist"], w.get("postcheck_log", []))
    return R({}, f"{hid} · model verdict: {out.verdict} ({len(out.risks)} risks)",
             extra={"verdicts": {hid: out.model_dump(mode="json")}})


@step(A6, "route")
async def a6_route(state, run, w, hid):
    """Red-team routing: revise once, otherwise survive/kill. Writes the decision log."""
    card = N._card(state, hid)
    v = E.Verdict.model_validate(state["verdicts"][hid])
    extra: dict[str, Any] = {"decisions": [], "killed": [], "feedback": {}}
    goto: Any = "review_a6"
    detail = f"{hid} · {v.verdict} → review"

    if v.verdict == "revise":
        reason = v.risks[0].text if v.risks else v.premortem
        target = v.revise_target
        used = state.get("revise_used", {}).get(hid)
        if not used and target in ("a3_profile", "a4_inactive", "a5_active"):
            extra["revise_used"] = {hid: True}
            extra["feedback"][N.fb_key(target, hid)] = f"Red Team requested a revision: {reason}"
            extra["decisions"].append({"actor": "red_teamer", "action": "revise",
                                       "target": f"{target}:{hid}", "reason": reason})
            goto = "a3_retrieve" if target == "a3_profile" else ["a4_inactive", "a5_active"]
            detail = f"{hid} · revise → back to {target} (once)"
        else:
            why = ("revise cap reached (one round)" if used
                   else f"revise target {target} is run-wide; not rerun per hypothesis")
            v.verdict, v.revise_target = "survives", None
            v.premortem = f"[{why}; unresolved] {v.premortem}"
            extra["decisions"].append({"actor": "orchestrator", "action": "override",
                                       "target": f"a6_redteam:{hid}", "reason": f"{why}: {reason}"})
            run.events.append(["agent_done", {"run_id": state["run_id"], "agent": "a6_redteam",
                                              "hypothesis_id": hid, "output": v.model_dump(mode="json")}])
            detail = f"{hid} · revise overridden → survives ({why})"
    if v.verdict == "kill":
        top = v.risks[0] if v.risks else None
        extra["killed"].append({"hypothesis_id": hid, "mechanism": card.mechanism,
                                "reason": top.text if top else v.premortem,
                                "kill_criterion": top.kill_criterion if top else ""})
        extra["decisions"].append({"actor": "red_teamer", "action": "kill", "target": hid,
                                   "reason": top.kill_criterion if top else v.premortem})
        detail = f"{hid} · kill: {_short(top.kill_criterion if top else v.premortem, 80)}"
    extra["verdicts"] = {hid: v.model_dump(mode="json")}
    return R({"route": goto if isinstance(goto, str) else list(goto)}, detail, extra=extra, goto=goto)
