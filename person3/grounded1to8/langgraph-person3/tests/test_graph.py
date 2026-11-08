"""LangGraph tests (stub LLM, stub retrieval: 0 tokens).  Run: pytest -q"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from p3graph import engine as E  # noqa: E402
from p3graph import nodes as N  # noqa: E402
from p3graph.graph import build  # noqa: E402


@pytest.fixture
def stub():
    """One shared StubLLM so tests can inspect the prompts every node sent."""
    llm = E.StubLLM()
    N.LLM_FACTORY = lambda state: llm
    yield llm
    N.LLM_FACTORY = None


def run(coro):
    return asyncio.run(coro)


def inp(mode="single_shot"):
    return {"config": {"as_of": "2021-01-01", "mode": mode}, "llm": {"mode": "stub"}}


async def drive(g, x, cfg):
    async for _ in g.astream(x, cfg, stream_mode="updates"):
        pass
    return await g.aget_state(cfg)


def cfg():
    return {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 400}


def pending(snap):
    return [i.value for t in snap.tasks for i in t.interrupts]


# ---------------------------------------------------------------------------- structure
def test_graph_shape():
    m = build().get_graph().draw_mermaid()
    for agent in ("a1", "a2", "a3", "a6"):
        for step in ("retrieve", "prompt", "llm", "parse", "fix", "postcheck", "commit"):
            assert f"{agent}_{step}" in m
    for n in ("a2_grade", "a1_validate", "a3_validate", "a6_validate", "a6_route", "review_a1", "review_a6",
              "next_card", "report"):
        assert n in m
    for n in ("a4_gather", "a4_triage", "a4_establish", "a4_prompt", "a4_llm", "a4_merge", "a4_commit",
              "a5_gather", "a5_shortlist", "a5_establish", "a5_prompt", "a5_llm", "a5_merge", "a5_score", "a5_commit"):
        assert n in m


# ---------------------------------------------------------------------------- single shot
def test_single_shot_backtest_end_to_end(stub):
    g, c = build(InMemorySaver()), cfg()
    snap = run(drive(g, inp(), c))
    s = snap.values
    assert not snap.next  # finished, no pauses in single_shot
    assert [k["hypothesis_id"] for k in s["killed"]] == ["h5"]
    assert s["revise_used"] == {"h3": True}
    acts = [(d["actor"], d["action"]) for d in s["decisions"]]
    assert ("red_teamer", "revise") in acts and ("red_teamer", "kill") in acts
    assert set(s["verdicts"]) == {c["id"] for c in s["cards"]}
    # A3 ran twice for h3 (revise round) and got the red-team feedback the second time
    a3_calls = [x for x in stub.calls if x["agent"] == "a3_profile" and '"h3"' in x["messages"][-1]["content"]]
    assert len(a3_calls) == 2 and "Red Team requested a revision" in a3_calls[1]["messages"][-1]["content"]
    # report + workbook
    assert "## At a glance" in s["report"] and "## Decision log" in s["report"]
    wb = load_workbook(s["workbook_path"])
    hyp = list(wb["Hypotheses"].iter_rows(min_row=2, values_only=True))
    assert len(hyp) == len(s["cards"])
    assert (Path(s["workbook_path"]).parent / "result.json").exists()


def test_backtest_never_sees_post_cutoff(stub):
    g, c = build(InMemorySaver()), cfg()
    run(drive(g, inp(), c))
    prompts = " ".join(m["content"] for x in stub.calls for m in x["messages"])
    for late in ("fx:pm-boreas-2023", "fx:fda-ensifentrine-2024", "fx:nct-aerify1", "fx:pm-il33-ph3-2025"):
        assert late not in prompts


# ---------------------------------------------------------------------------- guided (human in the loop)
def test_guided_pauses_then_rerun_with_comment_reaches_prompt(stub):
    g, c = build(InMemorySaver()), cfg()
    snap = run(drive(g, inp("guided"), c))
    assert snap.next == ("review_a1",)
    p = pending(snap)[0]
    assert p["checkpoint"] == "a1_disease" and p["summary"] and "citation_check" in p

    snap = run(drive(g, Command(resume={"action": "rerun", "comment": "add a safety shift"}), c))
    assert snap.next == ("review_a1",)  # A1 reran, paused again
    a1_calls = [x for x in stub.calls if x["agent"] == "a1_disease"]
    assert len(a1_calls) == 2 and "add a safety shift" in a1_calls[-1]["messages"][-1]["content"]
    assert snap.values["feedback"]["a1_disease"] is None  # feedback consumed


def test_guided_edit_cards_then_approve_all(stub):
    g, c = build(InMemorySaver()), cfg()
    run(drive(g, inp("guided"), c))
    snap = run(drive(g, Command(resume={"action": "approve"}), c))
    cards = pending(snap)[0]["output"]
    assert pending(snap)[0]["checkpoint"] == "a2_mechanism"

    kept = [x for x in cards if x["id"] in ("h1", "h5")]  # reviewer keeps two cards
    snap = run(drive(g, Command(resume={"action": "edit", "edited_output": kept, "comment": "focus"}), c))
    assert pending(snap)[0]["checkpoint"] == "a3_profile" and pending(snap)[0]["hypothesis_id"] == "h1"
    for _ in range(3):  # approve_all for a3, then a6 → runs to the end
        snap = run(drive(g, Command(resume={"action": "approve_all"}), c))
        if not snap.next:
            break
    s = snap.values
    assert not snap.next
    assert [x["id"] for x in s["cards"]] == ["h1", "h5"]
    assert set(s["verdicts"]) == {"h1", "h5"}
    assert ("reviewer", "edit", "a2_mechanism") in [(d["actor"], d["action"], d["target"]) for d in s["decisions"]]


def test_rerun_cap_is_two(stub):
    g, c = build(InMemorySaver()), cfg()
    run(drive(g, inp("guided"), c))
    for _ in range(3):
        snap = run(drive(g, Command(resume={"action": "rerun", "comment": "again"}), c))
    assert len([x for x in stub.calls if x["agent"] == "a1_disease"]) == 3  # 1 + 2 reruns
    assert pending(snap)[0]["checkpoint"] == "a2_mechanism"  # 3rd rerun approved as is
    assert "rerun cap" in snap.values["decisions"][-1]["reason"]


# ---------------------------------------------------------------------------- A4 / A5 feed A6
def test_a5_landscape_feeds_red_team(stub):
    s = run(drive(build(InMemorySaver()), inp(), cfg())).values
    crowding = s["assets"]["h1:landscape"]["crowding_score"]
    assert s["signals"]["crowding"]["h1"] == crowding
    a6 = [x for x in stub.calls if x["agent"] == "a6_redteam"][0]
    assert f'"crowding_score": {crowding}' in a6["messages"][-1]["content"]


# ---------------------------------------------------------------------------- step-level behaviour
def test_every_step_runs_in_order_and_is_logged(stub):
    g, c = build(InMemorySaver()), cfg()
    s = run(drive(g, inp(), c)).values
    nodes = [x["node"] for x in s["steps"]]
    assert nodes[:12] == ["setup", "intake", "a1_retrieve", "a1_prompt", "a1_toolloop", "a1_llm", "a1_parse", "a1_postcheck",
                          "a1_validate", "a1_cite", "a1_commit", "a2_retrieve"]
    # A2 has a grade step, A3/A6 validate before postcheck
    a2 = [n for n in nodes if n.startswith("a2_")]
    assert a2 == ["a2_retrieve", "a2_prompt", "a2_toolloop", "a2_llm", "a2_parse", "a2_grade", "a2_postcheck", "a2_cite", "a2_commit"]
    h1 = [x["node"] for x in s["steps"] if x.get("hid") == "h1" and x["node"].startswith(("a3_", "a6_", "a6b_"))]
    assert h1 == ["a3_retrieve", "a3_prompt", "a3_toolloop", "a3_llm", "a3_parse", "a3_validate", "a3_postcheck", "a3_cite", "a3_commit",
                  "a6_retrieve", "a6_prompt", "a6_toolloop", "a6_llm", "a6_parse", "a6_validate", "a6_postcheck", "a6_cite", "a6_commit",
                  "a6b_judge", "a6_route"]
    # every step has a timing and a one-line explanation
    assert all("ms" in x and x["detail"] is not None for x in s["steps"])
    # per-step debug files + step trace
    d = Path(s["workbook_path"]).parent
    assert not (d / "steps").exists()  # one log.jsonl line per step instead of a file per step
    recs = [json.loads(x) for x in (d / "log.jsonl").read_text().splitlines()]
    dump = next(r for r in recs if r["node"] == "a1_llm")
    assert dump["messages"][0]["role"] == "system" and dump["changed"]["raw"]["text"]
    assert not any(r["node"] in ("setup", "intake") for r in recs)  # the folder starts with the first agent
    assert "| a6_route |" in (d / "steps.md").read_text()


def test_work_area_holds_intermediate_data(stub):
    g, c = build(InMemorySaver()), cfg()
    s = run(drive(g, inp(), c)).values
    w = s["work"]["a1_disease"]
    assert w["pack"]["items"] and w["msgs"] and w["raw"]["text"] and w["out"]["shifts"]
    assert s["work"]["a6_redteam:h5"]["checklist"]["retractions"]  # the red-team checklist is inspectable


def test_usage_counts_each_llm_call_once(stub):
    g, c = build(InMemorySaver()), cfg()
    s = run(drive(g, inp(), c)).values
    for agent in ("a1_disease", "a2_mechanism", "a3_profile", "a6_redteam"):
        assert s["usage"][agent]["calls"] == len([x for x in stub.calls if x["agent"] == agent])


class ShortA1(E.StubLLM):
    """First A1 answer has too few shifts (fails the PS bars), the second is the fixture."""

    async def __call__(self, *, agent, messages, schema, **kw):
        out = await super().__call__(agent=agent, messages=messages, schema=schema, **kw)
        if agent == "a1_disease" and len([x for x in self.calls if x["agent"] == agent]) == 1:
            out.shifts = out.shifts[:1]
        return out


def test_semantic_problem_loops_through_fix_then_passes():
    llm = ShortA1()
    N.LLM_FACTORY = lambda state: llm
    try:
        g, c = build(InMemorySaver()), cfg()
        s = run(drive(g, inp(), c)).values
    finally:
        N.LLM_FACTORY = None
    a1 = [x["node"] for x in s["steps"] if x["node"].startswith("a1_")]
    assert a1 == ["a1_retrieve", "a1_prompt", "a1_toolloop", "a1_llm", "a1_parse", "a1_postcheck", "a1_validate",
                  "a1_fix", "a1_llm", "a1_parse", "a1_postcheck", "a1_validate", "a1_cite", "a1_commit"]
    second = [x for x in llm.calls if x["agent"] == "a1_disease"][1]
    assert "Fix these problems" in second["messages"][-1]["content"]
    assert s["work"]["a1_disease"]["sem_tries"] == 1


def test_semantic_problem_twice_fails_loudly():
    class AlwaysShort(E.StubLLM):
        async def __call__(self, *, agent, messages, schema, **kw):
            out = await super().__call__(agent=agent, messages=messages, schema=schema, **kw)
            if agent == "a1_disease":
                out.shifts = out.shifts[:1]
            return out

    N.LLM_FACTORY = lambda state: AlwaysShort()
    try:
        with pytest.raises(E.a1_disease.A1Error):
            run(drive(build(InMemorySaver()), inp(), cfg()))
    finally:
        N.LLM_FACTORY = None


def test_schema_error_loops_through_fix_then_passes(stub, monkeypatch):
    real, n = E.complete_raw, {"a3": 0}

    async def flaky(llm, *, agent, messages, schema, **kw):
        if agent == "a3_profile" and n["a3"] == 0:
            n["a3"] += 1
            await real(llm, agent=agent, messages=messages, schema=schema, **kw)  # keep stub call count honest
            return {"text": '{"hypothesis_id": "h1"', "reasoning": "", "model": "stub", "mode": "fixture",
                    "finish_reason": "length", "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
                    "latency_ms": 1}
        return await real(llm, agent=agent, messages=messages, schema=schema, **kw)

    monkeypatch.setattr(E, "complete_raw", flaky)
    s = run(drive(build(InMemorySaver()), inp(), cfg())).values
    h1 = [x["node"] for x in s["steps"] if x.get("hid") == "h1" and x["node"].startswith("a3_")]
    assert h1[:7] == ["a3_retrieve", "a3_prompt", "a3_toolloop", "a3_llm", "a3_parse", "a3_fix", "a3_llm"]
    assert any("schema error" in x["detail"] for x in s["steps"] if x["node"] == "a3_parse")
    assert any("TRUNCATED" in x["detail"] for x in s["steps"] if x["node"] == "a3_llm")
    assert "h1" in s["profiles"]


# ---------------------------------------------------------------------------- editing the workflow
from p3graph import tools as T  # noqa: E402


def ins(name, after, node, **params):
    return {"name": name, "after": after, "node": f"custom_nodes:{node}", "params": params}


def test_prompt_override_and_append(stub, tmp_path):
    (tmp_path / "a3_profile.md").write_text("YOU ARE A CUSTOM A3 PROMPT")
    (tmp_path / "a1_disease.append.md").write_text("EXTRA RULE FOR A1")
    g = build(InMemorySaver(), workflow={"prompts_dir": str(tmp_path)})
    run(drive(g, inp(), cfg()))
    a3 = [x for x in stub.calls if x["agent"] == "a3_profile"][0]["messages"][0]["content"]
    a1 = [x for x in stub.calls if x["agent"] == "a1_disease"][0]["messages"][0]["content"]
    assert a3 == "YOU ARE A CUSTOM A3 PROMPT"
    assert a1.endswith("EXTRA RULE FOR A1") and "Agent 1" in a1  # engine prompt + appended text
    # untouched agents keep the engine prompt
    a6 = [x for x in stub.calls if x["agent"] == "a6_redteam"][0]["messages"][0]["content"]
    assert "CUSTOM" not in a6


def test_inserted_tool_node_feeds_the_prompt(stub):
    @T.tool("fake_tool")
    async def fake(ctx, state, hid, **_):
        return [{"id": "tool:fake:1", "source": "tool", "source_date": "2019-01-01",
                 "title": "Tool result", "body": "TOOLDOC for " + str(hid)}]
    try:
        wf = {"insert": [ins("a3_tools", "a3_retrieve", "run_tools", tools=[{"name": "fake_tool"}])]}
        s = run(drive(build(InMemorySaver(), workflow=wf), inp(), cfg())).values
    finally:
        T.REGISTRY.pop("fake_tool")
    order = [x["node"] for x in s["steps"] if x.get("hid") == "h1" and x["node"].startswith("a3_")][:3]
    assert order == ["a3_retrieve", "a3_tools", "a3_prompt"]
    a3 = [x for x in stub.calls if x["agent"] == "a3_profile"][0]
    assert "tool:fake:1" in a3["messages"][-1]["content"]
    assert s["work"]["a3_profile:h1"]["tool_log"][0]["tool"] == "fake_tool"


def test_tool_cannot_leak_the_future(stub):
    @T.tool("late_tool")
    async def late(ctx, state, hid, **_):
        return [{"id": "tool:late:1", "source": "tool", "source_date": "2023-05-01", "title": "x", "body": "FUTURE"}]
    try:
        wf = {"insert": [ins("a3_tools", "a3_retrieve", "run_tools", tools=[{"name": "late_tool"}])]}
        run(drive(build(InMemorySaver(), workflow=wf), inp(), cfg()))
    finally:
        T.REGISTRY.pop("late_tool")
    assert "tool:late:1" not in " ".join(m["content"] for x in stub.calls for m in x["messages"])


def test_refine_loop_goes_back_then_gives_up_at_the_cap(stub):
    wf = {"insert": [ins("a3_refine", "a3_postcheck", "refine_loop", field="must_haves", min=99, max_loops=2,
                         feedback="MORE MUST-HAVES PLEASE")]}
    s = run(drive(build(InMemorySaver(), workflow=wf), inp(), cfg())).values
    h1 = [x["node"] for x in s["steps"] if x.get("hid") == "h1"]
    assert h1.count("a3_prompt") == 3 and h1.count("a3_refine") == 3  # 1 + 2 loops
    assert s["loops"]["a3_refine:h1"] == 2
    a3 = [x for x in stub.calls if x["agent"] == "a3_profile" and '"h1"' in x["messages"][-1]["content"]]
    assert "MORE MUST-HAVES PLEASE" in a3[1]["messages"][-1]["content"]
    assert any(d["action"] == "loop" for d in s["decisions"])


def test_safety_gate_stop_and_loop_and_pass(stub):
    wf = {"insert": [ins("gate", "a1_postcheck", "safety_gate", block_terms=["GOLD"], on_fail="stop")]}
    from custom_nodes import GateError
    with pytest.raises(GateError):
        run(drive(build(InMemorySaver(), workflow=wf), inp(), cfg()))

    wf = {"insert": [ins("gate", "a1_postcheck", "safety_gate", block_terms=["GOLD"], on_fail="loop", max_loops=1)]}
    s = run(drive(build(InMemorySaver(), workflow=wf), inp(), cfg())).values
    a1 = [x["node"] for x in s["steps"] if x["node"].startswith("a1_") or x["node"] == "gate"]
    assert a1.count("a1_llm") == 2  # looped back once, then the cap let it through
    assert any("loop cap reached" in x["detail"] for x in s["steps"] if x["node"] == "gate")

    wf = {"insert": [ins("gate", "a1_postcheck", "safety_gate", block_terms=["zzzz-never"], max_confidence=1.0,
                         require_sources=False, on_fail="stop")]}
    s = run(drive(build(InMemorySaver(), workflow=wf), inp(), cfg())).values
    assert [x["detail"] for x in s["steps"] if x["node"] == "gate"] == ["passed"]


def test_safety_gate_review_pauses_a_human_and_reject_stops(stub):
    from custom_nodes import GateError
    wf = {"insert": [ins("gate", "a1_postcheck", "safety_gate", block_terms=["GOLD"], on_fail="review")]}
    # single_shot never pauses: it only flags
    s = run(drive(build(InMemorySaver(), workflow=wf), inp(), cfg())).values
    assert any(d["action"] == "gate_failed" for d in s["decisions"])
    # guided: pauses at the gate itself
    g, c = build(InMemorySaver(), workflow=wf), cfg()
    snap = run(drive(g, inp("guided"), c))
    assert pending(snap)[0]["checkpoint"] == "gate" and snap.next == ("gate",)
    snap = run(drive(g, Command(resume={"action": "approve"}), c))
    assert pending(snap)[0]["checkpoint"] == "a1_disease"  # continued to the normal A1 review
    g, c = build(InMemorySaver(), workflow=wf), cfg()
    run(drive(g, inp("guided"), c))
    with pytest.raises(GateError):
        run(drive(g, Command(resume={"action": "reject"}), c))


def test_bad_insert_point_lists_the_valid_ones():
    with pytest.raises(ValueError, match="Valid places"):
        build(workflow={"insert": [ins("x", "nope_step", "safety_gate")]})


def test_inserted_nodes_show_in_the_diagram_and_are_marked_custom(stub):
    wf = {"insert": [ins("a2_gate", "a2_grade", "safety_gate", block_terms=["zzzz"], on_fail="flag")]}
    g = build(InMemorySaver(), workflow=wf)
    assert "a2_gate" in g.get_graph().draw_mermaid()
    s = run(drive(g, inp(), cfg())).values
    a2 = [x["node"] for x in s["steps"] if x["node"].startswith("a2_")]
    assert a2[a2.index("a2_grade") + 1] == "a2_gate" and a2[a2.index("a2_gate") + 1] == "a2_postcheck"
    assert next(x for x in s["steps"] if x["node"] == "a2_gate")["custom"] is True


def test_a4_then_a5_then_a6_run_in_sequence_for_every_card(stub):
    s = run(drive(build(InMemorySaver()), inp(), cfg())).values
    want = ["a3_commit", "a4_gather", "a4_triage", "a4_establish", "a4_prompt", "a4_llm", "a4_merge", "a4_commit",
            "a5_gather", "a5_shortlist", "a5_establish", "a5_prompt", "a5_llm", "a5_merge", "a5_score", "a5_commit",
            "a6_retrieve"]
    for card in s["cards"]:
        h = card["id"]
        seq = [x["node"] for x in s["steps"] if x.get("hid") == h and x["node"] in want]
        assert seq[:len(want)] == want, (h, seq)
        assert f"{h}:inactive" in s["assets"] and f"{h}:landscape" in s["assets"]


def test_a4_a5_llm_steps_use_the_model_and_steps_match_the_plain_run(stub):
    from agents.competitor import a4_inactive, a5_active
    from agents.competitor.context import for_agents

    s = run(drive(build(InMemorySaver()), inp(), cfg())).values
    nodes = [x["node"] for x in s["steps"]]
    # one model call per llm step (a revise sends a card through A4/A5 again)
    assert [x["agent"] for x in stub.calls].count("a4_inactive") == nodes.count("a4_llm") >= len(s["cards"])
    assert [x["agent"] for x in stub.calls].count("a5_active") == nodes.count("a5_llm") >= len(s["cards"])
    # step-by-step in the graph == one-shot run of the agent
    ctx = E.RunContext(config=E.RunConfig.model_validate(s["config"]), retrieval=E.StubRetrieval(date(2021, 1, 1)),
                       llm=E.StubLLM())
    card = E.HypothesisCard.model_validate(s["cards"][0])
    prof = E.ProductProfile.model_validate(s["profiles"][card.id])
    plain = run(a5_active.run(a5_active.A5Input(card=card, profile=prof), for_agents(ctx)))
    assert s["assets"][f"{card.id}:landscape"] == plain.model_dump(mode="json")
    plain4 = run(a4_inactive.run(a4_inactive.A4Input(card=card, profile=prof), for_agents(ctx)))
    assert s["assets"][f"{card.id}:inactive"] == plain4.model_dump(mode="json")


def test_competitor_steps_emit_per_card_monitor_events(stub, tmp_path, monkeypatch):
    from p3graph import live, competitor as C
    monkeypatch.setattr(live, 'RUNS', tmp_path)
    state = run(drive(build(InMemorySaver()), inp(), cfg())).values
    events = [json.loads(line) for line in (tmp_path / state['run_id'] / 'live.jsonl').read_text().splitlines()]
    for card in state['cards']:
        hid = card['id']
        for short in ('a4', 'a5'):
            for step in C.AGENTS[short]['steps']:
                rows = [e for e in events if e.get('hid') == hid and e.get('node') == f'{short}_{step}']
                assert rows[0]['event'] == 'start'
                assert rows[-1]['event'] == 'end'
                for attempt in {e['id'] for e in rows}:
                    attempt_rows = [e for e in rows if e['id'] == attempt]
                    assert attempt_rows[0]['event'] == 'start'
                    assert attempt_rows[-1]['event'] == 'end'
                    assert attempt_rows[-1]['time'] >= attempt_rows[0]['time']
                if step == 'llm':
                    assert rows[0]['model'] == 'stub'
                    messages = next(e['messages'] for e in rows if e['event'] == 'messages')
                    assert {m['role'] for m in messages} == {'system', 'user'}
                    assert rows[-1]['fixture']
    starts = [e['id'] for e in events if e['event'] == 'start' and e['node'].startswith(('a4_', 'a5_'))]
    assert len(starts) == len(set(starts))


# ---------------------------------------------------------------------------- tools step + A6b judge
def test_judge_decides_routing_and_fills_workbook(stub):
    from openpyxl import load_workbook as _lw

    x = {**inp(), "tools": True, "judge": True}  # stub LLM: no scripted tool calls, judge from fixtures
    s = run(drive(build(InMemorySaver()), x, cfg())).values
    assert any(r["node"] == "a1_toolloop" for r in s["steps"])
    assert set(s["judgments"]) >= {"h1"}
    assert any(d["actor"] == "judge" for d in s["decisions"])
    j = [x for x in stub.calls if x["agent"] == "a6b_judge"]
    assert j and "product_profile" in j[0]["messages"][-1]["content"]
    wb = _lw(s["workbook_path"])
    assert {"Judge", "ToolCalls", "NewEvidence"} <= set(wb.sheetnames)
    assert wb["Judge"].max_row > 1


def test_judge_off_keeps_a6_routing(stub):
    s = run(drive(build(InMemorySaver()), inp(), cfg())).values
    assert not s.get("judgments")
    assert not any(d["actor"] == "judge" for d in s["decisions"])
    assert any(r["node"] == "a6b_judge" and "judge off" in r["detail"] for r in s["steps"])


# ---------------------------------------------------------------------------- A7 citation gate
def test_hallucination_restarts_node_then_passes(stub, monkeypatch):
    C = E.a7_citations
    real, calls = C.check, {"n": 0}

    async def flaky(agent, out, pack, ctx, hid=None):
        res = await real(agent, out, pack, ctx, hid)
        if agent == "a3_profile" and hid == "h1" and calls["n"] == 0 and res["entries"]:
            calls["n"] += 1
            e = {**res["entries"][0], "supports": "no", "action": "remove", "claim_text": "x"}
            return {**res, "hallucinated": [e]}
        return res

    monkeypatch.setattr(C, "check", flaky)
    s = run(drive(build(InMemorySaver()), inp(), cfg())).values
    h1 = [x["node"] for x in s["steps"] if x.get("hid") == "h1" and x["node"].startswith("a3_")]
    i = h1.index("a3_cite")
    assert h1[i + 1] == "a3_llm" and h1.count("a3_cite") == 2 and h1[-1] == "a3_commit"
    assert any(e[0] == "node_restart" for e in s["events"])
    assert "CITATION CHECK FAILED" in s["work"]["a3_profile:h1"]["msgs"][-1]["content"]


def test_cite_strips_unsupported_after_max_restarts():
    from datetime import date as _date
    from agents.analysis._common import EvidencePack
    from agents.analysis.schemas import Claim, ProductProfile

    C = E.a7_citations
    p = ProductProfile(hypothesis_id="h1", soc_gap_at_launch="", minimum_specs=[], holy_grail_specs=[],
                       must_haves=["x"], expansion_opportunity="", claims=[
                           Claim(id="c1", agent="a3", text="t", kind="evidence", confidence=0.8, source_ids=["fake:1"])])
    pack = EvidencePack(_date(2026, 1, 1))

    class Ctx:
        config = type("c", (), {"as_of": _date(2026, 1, 1)})
        llm, retrieval = E.StubLLM(), None
        def emit(self, *a): pass

    res = run(C.check("a3_profile", p, pack, Ctx()))
    assert res["hallucinated"][0]["supports"] == "not_found"
    C.apply(p, res)
    assert p.claims[0].kind == "reasoning" and p.claims[0].text.startswith("[unverified]")


# ---------------------------------------------------------------------------- A0 intake gate
def test_rejected_pitch_stops_before_any_agent(stub, tmp_path):
    p = tmp_path / "off.txt"
    p.write_text("A fintech platform for payments. " * 40)
    s = run(drive(build(InMemorySaver()), {**inp(), "pitch": str(p)}, cfg())).values
    assert [x["node"] for x in s["steps"]] == ["setup", "intake"]
    assert not s["intake"]["accepted"] and "rejected" in s["report"]
    assert not stub.calls  # no tokens spent
    assert not (N.RUNS / s["run_id"]).exists()  # and no run folder is left behind


def test_accepted_pitch_reaches_a1_and_a2(stub, tmp_path, monkeypatch):
    from agents.analysis import a0_intake as A0

    async def fake(refs, cfg, ingest):
        return [{**r, "status": "missing"} for r in refs]
    monkeypatch.setattr(A0, "_compare", fake)
    p = tmp_path / "pitch.md"
    p.write_text("Inhaled PDE4B inhibitor for COPD exacerbations; chronic bronchitis patients; FEV1. " * 12)
    s = run(drive(build(InMemorySaver()), {**inp(), "pitch": str(p)}, cfg())).values
    assert s["intake"]["accepted"]
    for agent in ("a1_disease", "a2_mechanism"):
        msg = next(x for x in stub.calls if x["agent"] == agent)["messages"][-1]["content"]
        assert "startup_pitch" in msg


def test_cite_end_mode_checks_once_before_report(stub):
    s = run(drive(build(InMemorySaver()), {**inp(), "cite_mode": "end"}, cfg())).values
    nodes = [x["node"] for x in s["steps"]]
    assert "a7_final" in nodes and nodes.index("a7_final") < len(nodes)
    assert all("not per node" in x["detail"] for x in s["steps"] if x["node"].endswith("_cite"))
    assert any(e[0] == "citation_check" for e in s["events"])
