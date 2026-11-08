"""Agent 8 as twelve visible steps: the same memo, reported to the monitor step by step.

Offline: a fake model stands in for Nebius, so nothing here reaches an API. Every test also proves
the steps never use retrieval (NoRetrieval raises if anything touches it).
"""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from frontier.memo import steps
from frontier.memo.a8_memo import MemoText, PursuitText
from frontier.memo.draft import Statement, draft_from_numbers
from frontier.memo.inputs import from_graph_state

STATE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/memo/p3g_stub_state.json").read_text()
)
NO_PDF = frozenset({"pdf"})  # LibreOffice is slow and optional; the other steps are the point


class FakeModel:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def complete(self, *, run_id, agent, messages, schema):
        self.calls.append(messages)
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        return reply if isinstance(reply, str) else reply.model_dump_json()


def good_reply(state) -> MemoText:
    base = draft_from_numbers(from_graph_state(copy.deepcopy(state)))
    return MemoText(
        bottom_line=[Statement(kind="reasoning", text="We recommend three moves; risk is high.")],
        pursuits=[
            PursuitText(
                candidate_id=p.candidate_id,
                hook="Imagine a " + p.hook.removeprefix("Imagine "),
                the_move=p.the_move,
                next_step=p.next_step,
                why_good=p.why_good,
                what_could_go_wrong=p.what_could_go_wrong,
                return_vs_risk=p.return_vs_risk,
                gap_to_profile=p.gap_to_profile,
                what_would_change_the_call=p.what_would_change_the_call,
            )
            for p in base.pursuits
        ],
    )


class Recorder:
    """Stands in for the monitor: collects (node, event, payload) in order."""

    def __init__(self):
        self.events = []

    def __call__(self, node):
        def send(event, data=None):
            self.events.append((node, event, data or {}))

        return send

    def nodes(self):
        return [n for n, e, _ in self.events if e == "start"]

    def payloads(self, node, event):
        return [d for n, e, d in self.events if n == node and e == event]


def run(state, tmp_path, rec=None, skip=NO_PDF):
    return asyncio.run(steps.run_all(copy.deepcopy(state), tmp_path, rec, skip))


@pytest.fixture
def live(monkeypatch):
    """A live-mode run whose 'Nebius' is a fake model we control."""
    state = {**copy.deepcopy(STATE), "llm": {"mode": "live"}}

    def use(*replies):
        model = FakeModel(*replies)
        monkeypatch.setattr(steps, "backend_for", lambda live: model)
        return model

    return state, use


# ---- the steps, in order, in a stub run ------------------------------------------------------
def test_a_stub_run_goes_through_every_step_and_writes_the_files(tmp_path):
    rec = Recorder()
    work = run(STATE, tmp_path, rec)
    expected = [f"a8_{s}" for s in steps.STEPS if s != "pdf"]
    assert rec.nodes() == expected
    assert set(work["files"]) == {"docx", "xlsx"}
    assert (tmp_path / "memo.docx").exists() and (tmp_path / "programs.xlsx").exists()
    assert work.get("error") is None
    assert not [e for e in rec.events if e[1] == "error"]
    assert rec.payloads("a8_llm", "end")[0]["detail"].startswith("no model call (stub run)")


def test_each_step_reports_what_it_read_and_decided(tmp_path):
    rec = Recorder()
    run(STATE, tmp_path, rec)
    read = rec.payloads("a8_read", "inputs")[0]["inputs"]
    assert read["agent 8 reads"].endswith("no search, no web)") and "what it has" in read
    score = rec.payloads("a8_score", "inputs")[0]["inputs"]
    assert score["survivors, best first"][0]["upside"] in {"low", "medium", "high"}
    pick = rec.payloads("a8_pick", "inputs")[0]["inputs"]
    assert pick["chosen by code (one per top hypothesis, no repeated asset)"]
    assert "proceed with caution" in pick and "the menu of ways in" in pick
    prompt = rec.payloads("a8_prompt", "messages")[0]["messages"]
    assert prompt[0]["role"] == "system" and prompt[1]["content"].startswith("BRIEF:")
    merged = rec.payloads("a8_merge", "inputs")[0]["inputs"]
    assert merged["wording written by"] == "template"
    assert "stub" in merged["fallback reason"]
    commit = rec.payloads("a8_commit", "inputs")[0]["inputs"]
    assert commit["files written"]["docx"] == "memo.docx"


# ---- a live run with a fake model ------------------------------------------------------------
def test_good_model_wording_passes_the_rules_and_is_used(live, tmp_path):
    state, use = live
    model = use(good_reply(state))
    rec = Recorder()
    work = run(state, tmp_path, rec)
    assert len(model.calls) == 1
    check = rec.payloads("a8_check", "inputs")[0]["inputs"]
    assert check["result"].startswith("all passed")
    assert rec.payloads("a8_merge", "inputs")[0]["inputs"]["wording written by"] == "model"
    assert "a8_llm" in rec.nodes() and rec.nodes().count("a8_llm") == 1
    assert work["files"]["docx"] == "memo.docx"
    # the model call is reported like any other agent's: request, answer, response
    kinds = [e for n, e, _ in rec.events if n == "a8_llm"]
    assert {"request", "delta", "response"} <= set(kinds)


def test_a_rule_break_sends_the_run_back_to_the_model_once(live, tmp_path):
    state, use = live
    bad = good_reply(state)
    bad.pursuits[0] = bad.pursuits[0].model_copy(
        update={"the_move": "Invest in Pfizer, who will license it."}
    )
    model = use(bad, good_reply(state))
    rec = Recorder()
    run(state, tmp_path, rec)
    assert len(model.calls) == 2
    assert rec.nodes().count("a8_llm") == 2 and rec.nodes().count("a8_check") == 2
    first = rec.payloads("a8_check", "inputs")[0]["inputs"]
    assert any("Pfizer" in p for p in first["rule breaks"]) and "one retry" in first["so"]
    assert "Pfizer" in model.calls[1][-1]["content"]  # the retry tells the model what it broke
    assert rec.payloads("a8_merge", "inputs")[0]["inputs"]["wording written by"] == "model"


def test_two_bad_answers_fall_back_to_the_template_wording(live, tmp_path):
    state, use = live
    bad = good_reply(state)
    bad.pursuits[0] = bad.pursuits[0].model_copy(update={"the_move": "Acquire Pfizer's asset."})
    use(bad, bad)
    rec = Recorder()
    work = run(state, tmp_path, rec)
    merged = rec.payloads("a8_merge", "inputs")[0]["inputs"]
    assert merged["wording written by"] == "template"
    assert merged["fallback reason"].startswith("model wording rejected twice")
    assert work["files"]["docx"] == "memo.docx"  # a memo is always produced


def test_a_model_failure_is_reported_and_the_memo_is_still_written(live, monkeypatch, tmp_path):
    state, _ = live

    class Broken:
        async def complete(self, **_):
            raise ConnectionError("model API unreachable")

    monkeypatch.setattr(steps, "backend_for", lambda live: Broken())
    rec = Recorder()
    work = run(state, tmp_path, rec)
    assert "model call failed" in rec.payloads("a8_merge", "inputs")[0]["inputs"]["fallback reason"]
    assert work["files"]["docx"] == "memo.docx"


# ---- failure handling and the no-retrieval rule ----------------------------------------------
def test_a_step_that_fails_is_reported_and_the_later_steps_skip(monkeypatch, tmp_path):
    async def boom(*_a, **_k):
        raise RuntimeError("scoring exploded")

    monkeypatch.setitem(steps._STEP_FN, "score", boom)
    rec = Recorder()
    work = run(STATE, tmp_path, rec)
    errors = [d for n, e, d in rec.events if e == "error"]
    assert errors == [{"error": "RuntimeError: scoring exploded"}]
    assert not work.get("files")
    skipped = rec.payloads("a8_pick", "end")[0]["detail"]
    assert skipped.startswith("skipped: an earlier step failed")


def test_route_after_check_goes_back_to_the_model_only_when_asked():
    assert steps.route_after_check({"retry": True}) == "llm"
    assert steps.route_after_check({"retry": False}) == "merge"
    assert steps.route_after_check(None) == "merge"


def test_agent_8_never_touches_retrieval(live, tmp_path):
    state, use = live
    use(good_reply(state))
    with pytest.raises(RuntimeError, match="must not use retrieval"):
        steps.NoRetrieval().search("anything")
    run(state, tmp_path)  # a whole live run completes without the retrieval object being used
