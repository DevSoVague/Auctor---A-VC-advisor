"""Agent 8 step 4: the model rewrites wording; code rejects anything outside the rules.

A fake model backend stands in for Nebius, so no tokens are spent. Every test also proves the
agent never touches retrieval: ctx.retrieval raises if anything calls it.
"""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from docx import Document

from frontier.core.context import RunContext, StubLLM
from frontier.memo import a8_memo
from frontier.memo.a8_memo import MemoText, PursuitText, build_brief, check, merge
from frontier.memo.build import write_memo
from frontier.memo.draft import Statement, draft_from_numbers
from frontier.memo.inputs import from_graph_state

STATE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/memo/p3_stub_state.json").read_text()
)


class NoRetrieval:
    """A8 gets no search and no web: any use of the retrieval client is a test failure."""

    def __getattr__(self, name):
        raise AssertionError(f"A8 used retrieval ({name}); it must only read the MemoInput")


class FakeModel:
    """Returns the queued replies in order (a MemoText, or a raw string for a broken reply)."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def complete(self, *, run_id, agent, messages, schema):
        self.calls.append(messages)
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        return reply if isinstance(reply, str) else reply.model_dump_json()


@pytest.fixture
def inp():
    return from_graph_state(copy.deepcopy(STATE))


def ctx_for(inp, backend):
    return RunContext(config=inp.config, retrieval=NoRetrieval(), backend=backend)


def write(inp, backend):
    return asyncio.run(a8_memo.run(inp, ctx_for(inp, backend)))


def good_reply(inp) -> MemoText:
    """Wording built only from the code's own draft: it must pass every check."""
    base = draft_from_numbers(inp)
    return MemoText(
        bottom_line=[Statement(kind="reasoning", text="We recommend three moves; risk is high.")]
        + base.bottom_line[:1],
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


def with_text(reply: MemoText, **change) -> MemoText:
    """The reply with its first pursuit's fields replaced."""
    first = reply.pursuits[0].model_copy(update=change)
    return reply.model_copy(update={"pursuits": [first, *reply.pursuits[1:]]})


# ---- the happy path --------------------------------------------------------------------------
def test_valid_wording_is_used_and_the_codes_decisions_stay(inp):
    base = draft_from_numbers(inp)
    model = FakeModel(good_reply(inp))

    out = write(inp, model)

    assert out.written_by == "model" and out.fallback_reason is None
    assert len(model.calls) == 1
    assert out.pursuits[0].hook.startswith("Imagine a ")
    for mine, theirs in zip(out.pursuits, base.pursuits, strict=True):
        for kept in ("candidate_id", "form", "name", "upside", "risk", "validation_level"):
            assert getattr(mine, kept) == getattr(theirs, kept)
    assert out.proceed_with_caution == base.proceed_with_caution
    assert out.caution_reasons == base.caution_reasons and out.catalysts == base.catalysts


def test_the_model_is_told_the_rules_first_and_given_only_the_brief(inp):
    model = FakeModel(good_reply(inp))
    write(inp, model)
    system, user = model.calls[0]
    assert system["role"] == "system" and "You cannot search, browse" in system["content"]
    brief = json.loads(user["content"].removeprefix("BRIEF:\n"))
    status = inp.claim_status()
    for p in brief["pursuits"]:
        assert p["claims"], "every pursuit gets claims it may cite"
        assert all(status.get(c["id"]) != "removed" for c in p["claims"])
        assert {c["check"] for c in p["claims"]} <= {"verified", "partly", "unchecked"}


def test_the_template_memo_never_cites_removed_claims(inp):
    status = inp.claim_status()
    assert "removed" in status.values()  # the stub's A4/A5 sources are fake
    base = draft_from_numbers(inp)
    for p in base.pursuits:
        for s in [*p.why_good, *p.what_could_go_wrong]:
            assert all(status.get(c) != "removed" for c in s.claim_ids)
            assert s.kind == "reasoning" or s.claim_ids


# ---- rejected wording ------------------------------------------------------------------------
def test_invented_company_is_rejected_then_fixed_on_retry(inp):
    good = good_reply(inp)
    bad = with_text(good, the_move="Invest in Pfizer, who will license it.")
    model = FakeModel(bad, good)

    out = write(inp, model)

    assert out.written_by == "model" and len(model.calls) == 2
    retry = model.calls[1][-1]["content"]
    assert "Pfizer" in retry and "does not appear in the brief" in retry


def test_invented_number_is_rejected(inp):
    bad = with_text(good_reply(inp), gap_to_profile="The market is worth 4.2 billion dollars.")
    problems = check(bad, inp, draft_from_numbers(inp), build_brief(inp, draft_from_numbers(inp)))
    assert any("'4.2'" in p for p in problems)


def test_small_counts_are_allowed_but_big_unknown_numbers_are_not(inp):
    base = draft_from_numbers(inp)
    brief = build_brief(inp, base)
    ok = with_text(good_reply(inp), the_move="We see 3 ways in.")
    assert not [p for p in check(ok, inp, base, brief) if "'3'" in p]
    bad = with_text(good_reply(inp), the_move="We see 73 ways in.")
    assert any("'73'" in p for p in check(bad, inp, base, brief))


def test_evidence_line_must_cite_and_only_its_own_claims(inp):
    base = draft_from_numbers(inp)
    brief = build_brief(inp, base)
    good = good_reply(inp)
    first = brief["pursuits"][0]["claims"]
    mine = next(c["id"] for c in first if c["kind"] == "evidence")
    other = next(  # a sourced claim that belongs to a different pursuit only
        c["id"]
        for p in brief["pursuits"][1:]
        for c in p["claims"]
        if c["kind"] == "evidence" and c["id"] not in {x["id"] for x in first}
    )

    uncited = with_text(good, why_good=[Statement(text="It works.", kind="evidence")])
    assert any("cites no claim" in p for p in check(uncited, inp, base, brief))

    stolen = with_text(good, why_good=[Statement(text="It works.", claim_ids=[other])])
    assert any("not one of this pursuit's claims" in p for p in check(stolen, inp, base, brief))

    fine = with_text(good, why_good=[Statement(text="It works.", claim_ids=[mine])])
    assert not check(fine, inp, base, brief)


def test_removed_claims_cannot_be_cited(inp):
    base = draft_from_numbers(inp)
    brief = build_brief(inp, base)
    removed = next(c for c, s in inp.claim_status().items() if s == "removed")
    bad = with_text(good_reply(inp), why_good=[Statement(text="It works.", claim_ids=[removed])])
    assert any("removed by the citation check" in p for p in check(bad, inp, base, brief))


def test_reasoning_lines_cannot_carry_citations(inp):
    base = draft_from_numbers(inp)
    brief = build_brief(inp, base)
    cid = brief["pursuits"][0]["claims"][0]["id"]
    bad = with_text(
        good_reply(inp),
        return_vs_risk=Statement(kind="reasoning", text="Balanced.", claim_ids=[cid]),
    )
    assert any("reasoning line must not cite" in p for p in check(bad, inp, base, brief))


def test_pursuits_cannot_be_renamed_reordered_or_dropped(inp):
    base = draft_from_numbers(inp)
    brief = build_brief(inp, base)
    good = good_reply(inp)
    assert check(good.model_copy(update={"pursuits": good.pursuits[:-1]}), inp, base, brief)
    assert check(
        good.model_copy(update={"pursuits": list(reversed(good.pursuits))}), inp, base, brief
    )
    renamed = with_text(good, candidate_id="h9:invest:Someone Else")
    assert check(renamed, inp, base, brief)


# ---- fallback --------------------------------------------------------------------------------
def test_two_bad_attempts_fall_back_to_the_template_memo(inp):
    bad = with_text(good_reply(inp), the_move="Acquire Pfizer's asset.")
    model = FakeModel(bad, bad)

    out = write(inp, model)

    assert len(model.calls) == 2
    assert out.written_by == "template"
    assert out.fallback_reason.startswith("model wording rejected twice")
    assert out.pursuits == draft_from_numbers(inp).pursuits  # exactly the code's draft


def test_invalid_json_falls_back_without_a_third_call(inp):
    model = FakeModel("this is not json")
    out = write(inp, model)
    assert out.written_by == "template" and "not valid memo JSON" in out.fallback_reason
    assert len(model.calls) == 2  # ctx.llm's own single schema retry, then we stop


def test_stub_mode_makes_no_model_call(inp):
    out = write(inp, StubLLM())
    assert out.written_by == "template" and "stub mode" in out.fallback_reason


# ---- the memo shows who wrote it -------------------------------------------------------------
def test_memo_says_how_its_wording_was_written(inp, tmp_path):
    model_draft = merge(good_reply(inp), draft_from_numbers(inp))
    doc = Document(write_memo(inp, tmp_path / "m", draft=model_draft, pdf=False)["docx"])
    assert "written by the memo agent's model" in "\n".join(p.text for p in doc.paragraphs)

    fell_back = draft_from_numbers(inp).model_copy(update={"fallback_reason": "model failed"})
    doc = Document(write_memo(inp, tmp_path / "t", draft=fell_back, pdf=False)["docx"])
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "written from templates" in text and "Note: model failed" in text


def test_natural_wording_is_not_rejected(inp):
    """Plain investor prose that reuses the brief's own names and numbers passes every check."""
    base = draft_from_numbers(inp)
    brief = build_brief(inp, base)
    first = brief["pursuits"][0]
    mech, name = first["mechanism"], first["name"]
    cid = first["claims"][0]["id"]
    natural = with_text(
        good_reply(inp),
        hook=f"Imagine a treatment aimed at {mech} that keeps patients out of hospital.",
        the_move=f"Open talks to bring in {name} this year.",
        why_good=[
            Statement(text="Patients on triple therapy still have flare-ups.", claim_ids=[cid]),
            Statement(kind="reasoning", text="The field is not crowded, so there is room to win."),
        ],
        what_could_go_wrong=[
            Statement(kind="reasoning", text="The red team's main worry has not been tested yet.")
        ],
    )
    assert check(natural, inp, base, brief) == []


def test_numbers_must_match_a_whole_number_in_the_brief(inp):
    base = draft_from_numbers(inp)
    brief = build_brief(inp, base)
    pool = a8_memo._corpus(inp, base, brief)
    whole = set(a8_memo._plain_numbers(pool))
    hidden = next(  # a 2-digit number that only ever appears inside longer numbers
        n for n in map(str, range(10, 100)) if n in pool and n not in whole
    )
    bad = with_text(good_reply(inp), the_move=f"We see {hidden} routes.")
    assert any(repr(hidden) in p for p in check(bad, inp, base, brief))


def test_a_reasoning_claim_cannot_back_an_evidence_line(inp):
    base = draft_from_numbers(inp)
    brief = build_brief(inp, base)
    opinion = next(
        c["id"] for p in brief["pursuits"] for c in p["claims"] if c["kind"] == "reasoning"
    )
    owner = next(p for p in brief["pursuits"] if opinion in {c["id"] for c in p["claims"]})[
        "candidate_id"
    ]
    text = good_reply(inp)
    i = [p.candidate_id for p in text.pursuits].index(owner)
    pursuits = list(text.pursuits)
    pursuits[i] = pursuits[i].model_copy(
        update={"why_good": [Statement(text="It works.", claim_ids=[opinion])]}
    )
    bad = text.model_copy(update={"pursuits": pursuits})
    assert any("reasoning with no source" in p for p in check(bad, inp, base, brief))
