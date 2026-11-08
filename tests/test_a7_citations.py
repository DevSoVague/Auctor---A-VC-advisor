"""Agent 7, the Citation Checker, against the stub search tool and fixtures."""

import asyncio
import json
from datetime import date

from frontier.agents.citations import a7_citations
from frontier.agents.citations.a7_citations import claim_status, gate_problems
from frontier.agents.schemas import A7Input, Claim, ClaimLedger, RunConfig
from frontier.core.context import RunContext, StubLLM, load_fixture
from frontier.core.retrieval import StubRetrievalClient

LIVE = date(2026, 11, 7)
BACKTEST = date(2021, 1, 1)


class Recorder:
    """Wraps a backend and records which agent was asked about which pairs."""

    def __init__(self, reply=None):
        self.reply = reply  # function(pairs) -> ClaimLedger JSON, or None for the stub
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    async def complete(self, *, run_id, agent, messages, schema):
        pairs = json.loads(messages[-1]["content"])["pairs"]
        self.calls.append((agent, [(p["claim_id"], p["source_id"]) for p in pairs]))
        if self.reply:
            return self.reply(pairs)
        return await StubLLM().complete(
            run_id=run_id, agent=agent, messages=messages, schema=schema
        )


def ctx_for(backend, as_of=LIVE, profile="dev") -> RunContext:
    config = RunConfig.model_validate(
        {**load_fixture("a1_disease.input"), "as_of": as_of, "profile": profile}
    )
    return RunContext(config=config, retrieval=StubRetrievalClient(as_of), backend=backend)


def check(claims, backend, **kw) -> ClaimLedger:
    return asyncio.run(a7_citations.run(A7Input(claims=claims), ctx_for(backend, **kw)))


def fixture_claims() -> list[Claim]:
    return A7Input.model_validate(load_fixture("a7_citations.input")).claims


def evidence(id, *sources) -> Claim:
    return Claim(
        id=id,
        agent="a3_profile",
        text=f"claim {id}",
        kind="evidence",
        confidence=0.7,
        source_ids=list(sources),
    )


def verdicts(ledger):
    return {(e.claim_id, e.source_id): (e.supports, e.action) for e in ledger.entries}


def test_live_run_matches_the_fixture_ledger():
    ledger = check(fixture_claims(), Recorder())
    expected = ClaimLedger.model_validate(load_fixture("a7_citations.output"))
    assert ledger == expected


def test_source_after_the_cutoff_is_removed_without_asking_the_model():
    backend = Recorder()
    ledger = check(fixture_claims(), backend, as_of=BACKTEST)
    entry = next(e for e in ledger.entries if e.source_id == "sec:FIXTURE-0002")
    assert (entry.supports, entry.action) == ("after_cutoff", "remove")
    assert entry.source_date == date(2022, 2, 1)
    asked = [pair for _, pairs in backend.calls for pair in pairs]
    assert ("a5-c1", "sec:FIXTURE-0002") not in asked
    assert ("a5-c1", "nct:FIXTURE-0001") in asked  # the claim's other, earlier source


def test_unknown_source_is_not_found():
    ledger = check([evidence("x1", "pmid:FIXTURE-9999")], Recorder())
    entry = ledger.entries[0]
    assert (entry.supports, entry.action, entry.source_date) == ("not_found", "remove", None)


def test_dates_and_actions_come_from_code_not_the_model():
    def wrong(pairs):
        return json.dumps(
            {
                "entries": [
                    {
                        "claim_id": p["claim_id"],
                        "source_id": p["source_id"],
                        "source_date": "1999-01-01",
                        "supports": "partly",
                        "action": "keep",
                    }
                    for p in pairs
                ]
            }
        )

    entry = check([evidence("x1", "pmid:FIXTURE-0001")], Recorder(wrong)).entries[0]
    assert entry.source_date == date(2019, 5, 14)  # from the store
    assert (entry.supports, entry.action) == ("partly", "downgrade")


def test_pair_the_model_skips_counts_as_no():
    def skip_all(pairs):
        return json.dumps({"entries": []})

    entry = check([evidence("x1", "pmid:FIXTURE-0001")], Recorder(skip_all)).entries[0]
    assert (entry.supports, entry.action) == ("no", "remove")


def test_pairs_the_model_invents_are_ignored():
    def invent(pairs):
        real = [
            {
                "claim_id": p["claim_id"],
                "source_id": p["source_id"],
                "source_date": None,
                "supports": "yes",
                "action": "keep",
            }
            for p in pairs
        ]
        fake = {
            "claim_id": "ghost",
            "source_id": "pmid:FIXTURE-0003",
            "source_date": None,
            "supports": "yes",
            "action": "keep",
        }
        return json.dumps({"entries": [*real, fake]})

    ledger = check([evidence("x1", "pmid:FIXTURE-0001")], Recorder(invent))
    assert [(e.claim_id, e.source_id) for e in ledger.entries] == [("x1", "pmid:FIXTURE-0001")]


def test_final_profile_escalates_only_doubtful_pairs():
    backend = Recorder()
    check(fixture_claims(), backend, profile="final")
    agents = [agent for agent, _ in backend.calls]
    assert agents == ["a7_citations", "a7_escalate"]
    assert backend.calls[1][1] == [("a5-c1", "sec:FIXTURE-0002")]  # the one 'partly'


def test_escalation_verdict_stands():
    def first_doubts_then_confirms(pairs):
        verdict = "no" if len(recorder.calls) == 1 else "yes"
        return json.dumps(
            {
                "entries": [
                    {
                        "claim_id": p["claim_id"],
                        "source_id": p["source_id"],
                        "source_date": None,
                        "supports": verdict,
                        "action": "keep",
                    }
                    for p in pairs
                ]
            }
        )

    recorder = Recorder(first_doubts_then_confirms)
    ledger = check([evidence("x1", "pmid:FIXTURE-0001")], recorder, profile="final")
    assert verdicts(ledger)[("x1", "pmid:FIXTURE-0001")] == ("yes", "keep")


def test_dev_profile_never_escalates():
    backend = Recorder()
    check(fixture_claims(), backend, profile="dev")
    assert [agent for agent, _ in backend.calls] == ["a7_citations"]


def test_pairs_are_sent_in_batches():
    claims = [evidence(f"x{i}", "pmid:FIXTURE-0001") for i in range(25)]
    backend = Recorder(lambda pairs: json.dumps({"entries": []}))
    check(claims, backend)
    assert [len(pairs) for _, pairs in backend.calls] == [10, 10, 5]


def test_prompt_goes_first_as_the_system_message():
    seen = {}

    class Peek:
        async def complete(self, *, run_id, agent, messages, schema):
            seen["messages"] = messages
            return json.dumps({"entries": []})

    check([evidence("x1", "pmid:FIXTURE-0001")], Peek())
    assert seen["messages"][0]["role"] == "system"
    assert "Citation Checker" in seen["messages"][0]["content"]


def test_reasoning_claim_without_sources_is_not_in_the_ledger():
    reasoning = Claim(
        id="r1", agent="a6_redteam", text="t", kind="reasoning", confidence=0.5, source_ids=[]
    )
    ledger = check([reasoning], Recorder())
    assert ledger.entries == []
    assert claim_status(reasoning, ledger) == "unchecked"


def test_claim_status_takes_the_best_source():
    claims = fixture_claims()
    ledger = check(claims, Recorder(), as_of=BACKTEST)
    a5 = next(c for c in claims if c.id == "a5-c1")  # nct 'yes', sec 'after_cutoff'
    assert claim_status(a5, ledger) == "supported"
    gone = evidence("x1", "pmid:FIXTURE-9999")
    assert claim_status(gone, check([gone], Recorder())) == "removed"


def test_gate_passes_on_a_checked_ledger():
    claims = fixture_claims()
    assert gate_problems(claims, check(claims, Recorder(), as_of=BACKTEST), BACKTEST) == []


def test_gate_blocks_unchecked_claims_and_late_kept_sources():
    claims = fixture_claims()
    ledger = check(claims, Recorder())
    partial = ClaimLedger(entries=ledger.entries[:-1])
    assert any("not checked" in p for p in gate_problems(claims, partial, LIVE))
    # The live ledger kept a 2022 source; judged against the 2021 cutoff it must fail.
    assert any("on or after" in p for p in gate_problems(claims, ledger, BACKTEST))


def test_source_date_reveals_the_date_but_doc_still_hides_the_content():
    client = StubRetrievalClient(BACKTEST)
    assert asyncio.run(client.doc("sec:FIXTURE-0002")) is None
    assert asyncio.run(client.source_date("sec:FIXTURE-0002")) == date(2022, 2, 1)
    assert asyncio.run(client.source_date("pmid:FIXTURE-9999")) is None
