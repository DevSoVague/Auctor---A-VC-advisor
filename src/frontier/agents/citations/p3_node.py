"""Agent 7 (Citation Checker) as a step in Person 3's LangGraph pipeline (owner: person 2).

Spliced in by person3/1to6merged/langgraph-person3/workflow.yaml after every agent's
commit step, so each agent's claims are checked the moment that agent finishes:

    - name: a7c_after_a3
      after: a3_commit
      node: frontier.agents.citations.p3_node:check_citations
      params: {agent: a3_profile}

What it does with each claim in that agent's output: the same checks as a7_citations
(source exists before the cutoff, then the model judges support). Failures are
flagged, never deleted: the agent's output is left as it is, and the ledger goes into
the run state under signals["citations:<agent>[:<hypothesis>]"], where the review
pauses, the step trace and the memo (person 5) can read it.

It reuses Person 3's per-step context (their model client, budget, usage tracking and
search tool bound to the run's cutoff), so the check counts toward their run's tokens
under the name 'a7_citations'. Not to be confused with Person 3's A7 Judge, which rules
on the red team's attacks.
"""

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from frontier.agents.citations import a7_citations
from frontier.agents.schemas import A7Input, Claim, ClaimLedger, LedgerEntry

AGENT = "a7_citations"
PER_CARD = {"a3_profile", "a4_inactive", "a5_active", "a6_redteam"}


def committed_output(state: dict, agent: str, hid: str | None) -> Any:
    """Where each agent's committed output lives in Person 3's state.

    A4/A5 write flat keys, assets["<hid>:inactive"] and assets["<hid>:landscape"]; the
    nested assets[hid]["inactive"] layout in p3graph/state.py's comment is kept as a fallback.
    """
    assets = state.get("assets") or {}

    def asset(kind: str) -> Any:
        return assets.get(f"{hid}:{kind}") or (assets.get(hid) or {}).get(kind)

    return {
        "a1_disease": lambda: state.get("why_now"),
        "a2_mechanism": lambda: state.get("cards"),
        "a3_profile": lambda: (state.get("profiles") or {}).get(hid),
        "a4_inactive": lambda: asset("inactive"),
        "a5_active": lambda: asset("landscape"),
        "a6_redteam": lambda: (state.get("verdicts") or {}).get(hid),
    }[agent]()


def claims_in(obj: Any):
    """Every claim-shaped dict anywhere inside an output."""
    if isinstance(obj, dict):
        if {"id", "kind", "source_ids", "text"} <= obj.keys():
            yield obj
        for value in obj.values():
            yield from claims_in(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from claims_in(item)


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


class RecordView:
    """Person 3's search returns dicts; agent 7 reads id, source_date, title and body."""

    def __init__(self, retrieval: Any) -> None:
        self.inner = retrieval

    async def doc(self, doc_id: str):
        d = await self.inner.doc(doc_id)
        if not d:
            return None
        return SimpleNamespace(
            id=d.get("id", doc_id),
            source_date=_as_date(d.get("source_date")),
            title=d.get("title") or "",
            body=d.get("body") or d.get("text") or "",
        )

    async def source_date(self, doc_id: str) -> date | None:
        # Person 3's search has no date-only lookup, so a source dated after the cutoff
        # reads as 'not_found' rather than 'after_cutoff'. Both are flagged.
        lookup = getattr(self.inner, "source_date", None)
        return await lookup(doc_id) if lookup else None


class StubJudge:
    """Stub-mode stand-in for the model: marks every pair it is asked about as supported.

    Stub runs check plumbing, not judgment; existence and cutoff checks still run for real.
    """

    async def __call__(self, *, agent: str, messages: list[dict], schema: type) -> ClaimLedger:
        pairs = json.loads(messages[-1]["content"])["pairs"]
        return ClaimLedger(
            entries=[
                LedgerEntry(
                    claim_id=p["claim_id"],
                    source_id=p["source_id"],
                    source_date=None,
                    supports="yes",
                    action="keep",
                )
                for p in pairs
            ]
        )


def _split_claims(output: Any) -> tuple[list[Claim], list[dict]]:
    """Valid claims to check, plus problems for claims that break the contract."""
    claims, problems, seen = [], [], set()
    for raw in claims_in(output):
        if raw.get("id") in seen:
            continue
        seen.add(raw.get("id"))
        try:
            claims.append(Claim.model_validate(raw))
        except ValidationError as err:
            problems.append({"claim_id": raw.get("id"), "problem": err.errors()[0]["msg"]})
    return claims, problems


def summarize(ledger: ClaimLedger, problems: list[dict]) -> dict:
    counts = {"keep": 0, "downgrade": 0, "remove": 0}
    for e in ledger.entries:
        counts[e.action] += 1
    return {
        "checked_pairs": len(ledger.entries),
        "kept": counts["keep"],
        "downgraded": counts["downgrade"],
        "flagged": counts["remove"] + len(problems),
        "entries": [e.model_dump(mode="json") for e in ledger.entries],
        "invalid_claims": problems,
    }


async def check_citations(state: dict, params: dict) -> dict:
    """The workflow.yaml node: check the claims of params['agent']'s committed output."""
    from p3graph import nodes as N  # only importable inside Person 3's pipeline

    agent = params["agent"]
    hid = state.get("current") if agent in PER_CARD else None
    run = N.AgentRun(state, AGENT, hid)
    # Any stub-mode run (including tests that swap in their own stub client): Person 3's
    # stub clients have no a7_citations fixture, so the stub judge stands in.
    stub = (state.get("llm") or {}).get("mode") == "stub"
    ctx = SimpleNamespace(
        config=run.ctx.config,
        llm=StubJudge() if stub else run.ctx.llm,
        retrieval=RecordView(run.ctx.retrieval),
    )

    claims, problems = _split_claims(committed_output(state, agent, hid))
    ledger = (
        await a7_citations.run(A7Input(claims=claims), ctx) if claims else ClaimLedger(entries=[])
    )
    report = summarize(ledger, problems)
    key = f"citations:{agent}" + (f":{hid}" if hid else "")
    run.ctx.signals[key] = report

    detail = (
        f"{agent}{f' {hid}' if hid else ''}: {len(claims)} claims, {report['checked_pairs']} "
        f"source checks, {report['kept']} kept, {report['downgraded']} downgraded, "
        f"{report['flagged']} flagged" + (" (stub: support not judged)" if stub else "")
    )
    return {**run.update(), "detail": detail}
