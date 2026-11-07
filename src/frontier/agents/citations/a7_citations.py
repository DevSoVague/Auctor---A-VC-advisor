"""Agent 7: Citation Checker (owner: person 2).

PRD: for every claim, the source is real, supports the claim, and predates the
cutoff; writes the claim ledger (claim, source ID, source date, supports, action).
Done when every memo claim is in the ledger and none is dated after the cutoff.

How it decides, for each (claim, source) pair:
1. Code, no model: look the source up as of the run's date. Missing entirely ->
   'not_found'; exists but only on or after the cutoff -> 'after_cutoff'. Both
   are removed.
2. Model: the remaining pairs go to the model in batches with the source text,
   which judges 'yes', 'partly' or 'no'.
3. Final profile only: pairs judged 'partly' or 'no' get a second opinion from
   the stronger escalation model (profiles.yaml: a7_escalate), whose verdict
   stands. The dev profile has no escalation model, so dev skips this step.

Source dates and actions are always set by code from the store and the verdict,
never taken from the model.
"""

import json
from typing import Literal

from frontier.agents.schemas import A7Input, Claim, ClaimLedger, LedgerEntry
from frontier.core.context import RunContext, load_prompt
from frontier.core.retrieval import Record

AGENT = "a7_citations"
ESCALATE = "a7_escalate"
BATCH_SIZE = 10  # pairs per model call
MAX_SOURCE_CHARS = 4000  # source text sent per pair, to bound cost

Supports = Literal["yes", "partly", "no", "not_found", "after_cutoff"]
ACTION_FOR: dict[str, Literal["keep", "downgrade", "remove"]] = {
    "yes": "keep",
    "partly": "downgrade",
    "no": "remove",
    "not_found": "remove",
    "after_cutoff": "remove",
}
Key = tuple[str, str]  # (claim_id, source_id)


def _pairs(claims: list[Claim]) -> list[tuple[Claim, str]]:
    """Every (claim, source_id) once, in input order."""
    seen: set[Key] = set()
    out = []
    for claim in claims:
        for source_id in claim.source_ids:
            if (claim.id, source_id) not in seen:
                seen.add((claim.id, source_id))
                out.append((claim, source_id))
    return out


async def run(inp: A7Input, ctx: RunContext) -> ClaimLedger:
    entries: dict[Key, LedgerEntry] = {}
    to_judge: list[tuple[Claim, Record]] = []

    for claim, source_id in _pairs(inp.claims):
        record = await ctx.retrieval.doc(source_id)
        if record is not None:
            to_judge.append((claim, record))
            continue
        first_public = await ctx.retrieval.source_date(source_id)
        supports: Supports = "after_cutoff" if first_public is not None else "not_found"
        entries[(claim.id, source_id)] = LedgerEntry(
            claim_id=claim.id,
            source_id=source_id,
            source_date=first_public,
            supports=supports,
            action=ACTION_FOR[supports],
        )

    verdicts = await _judge(to_judge, ctx, AGENT)
    if ctx.config.profile == "final":
        doubtful = [p for p in to_judge if verdicts[(p[0].id, p[1].id)] in ("partly", "no")]
        if doubtful:
            verdicts.update(await _judge(doubtful, ctx, ESCALATE))

    for claim, record in to_judge:
        supports = verdicts[(claim.id, record.id)]
        entries[(claim.id, record.id)] = LedgerEntry(
            claim_id=claim.id,
            source_id=record.id,
            source_date=record.source_date,
            supports=supports,
            action=ACTION_FOR[supports],
        )

    return ClaimLedger(entries=[entries[(c.id, s)] for c, s in _pairs(inp.claims)])


async def _judge(
    pairs: list[tuple[Claim, Record]], ctx: RunContext, agent: str
) -> dict[Key, Supports]:
    """Ask the model about each pair; anything it skips or garbles counts as 'no'."""
    verdicts: dict[Key, Supports] = {}
    system = load_prompt(AGENT)
    for start in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[start : start + BATCH_SIZE]
        payload = [
            {
                "claim_id": claim.id,
                "claim_text": claim.text,
                "source_id": record.id,
                "source_title": record.title or "",
                "source_text": (record.body or "")[:MAX_SOURCE_CHARS],
            }
            for claim, record in batch
        ]
        ledger = await ctx.llm(
            agent=agent,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"pairs": payload}, ensure_ascii=False)},
            ],
            schema=ClaimLedger,
        )
        asked = {(claim.id, record.id) for claim, record in batch}
        answered = {
            (e.claim_id, e.source_id): e.supports
            for e in ledger.entries
            if (e.claim_id, e.source_id) in asked and e.supports in ("yes", "partly", "no")
        }
        for key in asked:
            verdicts[key] = answered.get(key, "no")
    return verdicts


# --- helpers for the flow (person 5) and the claims table ----------------------------

LedgerStatus = Literal["unchecked", "supported", "partly", "unsupported", "removed"]
_RANK = {"yes": 4, "partly": 3, "no": 2, "not_found": 1, "after_cutoff": 1}
_STATUS: dict[str, LedgerStatus] = {
    "yes": "supported",
    "partly": "partly",
    "no": "unsupported",
    "not_found": "removed",
    "after_cutoff": "removed",
}


def claim_status(claim: Claim, ledger: ClaimLedger) -> LedgerStatus:
    """The claims.ledger_status value: the best verdict across the claim's sources."""
    found = [e.supports for e in ledger.entries if e.claim_id == claim.id]
    if not found:
        return "unchecked"
    return _STATUS[max(found, key=_RANK.__getitem__)]


def gate_problems(claims: list[Claim], ledger: ClaimLedger, as_of) -> list[str]:
    """Why the memo must stay blocked; empty means the citation gate passes.

    PRD: every memo claim is in the ledger, and none is dated after the cutoff.
    """
    covered = {(e.claim_id, e.source_id) for e in ledger.entries}
    problems = [
        f"{claim.id}: source {source_id} not checked"
        for claim, source_id in _pairs(claims)
        if (claim.id, source_id) not in covered
    ]
    problems += [
        f"{e.claim_id}: kept source {e.source_id} is dated {e.source_date}, on or after {as_of}"
        for e in ledger.entries
        if e.action != "remove" and e.source_date is not None and e.source_date >= as_of
    ]
    return problems
