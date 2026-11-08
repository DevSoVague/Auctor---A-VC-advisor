"""A7 Citation checker, run on EVERY agent's output (grounding gate), ported from the team repo
(vidhi-ds-24/Ctrl-Alt-Cure, branch team-role-2-agent7, src/frontier/agents/citations/a7_citations.py).

For each (claim, source) pair:
  1. Code, no model: the source must exist (evidence pack, else the store as of the run's date) and be
     dated before as_of. Missing -> not_found; on/after the cutoff -> after_cutoff.
  2. Model: the remaining pairs go in batches with the source text; it judges yes / partly / no.
     Skipped or garbled answers count as 'no'. Stub LLM: code checks only ('yes' for found sources).
Dates and actions are set by code, never by the model.

Hallucination = any pair judged no / not_found / after_cutoff. The caller restarts the node with
`feedback(result)` (max 2 restarts), then `apply()` strips what is still unsupported.
'partly' is not a hallucination: the claim is kept with confidence capped at PARTLY_CAP.
"""

from __future__ import annotations

import json
from datetime import date

from agents.analysis._common import iter_claims, system_prompt
from agents.analysis.context import StubLLM
from agents.analysis.retrieval import _d
from agents.analysis.schemas import ClaimLedger, LedgerEntry

AGENT = "a7_citations"
BATCH_SIZE = 10
MAX_SOURCE_CHARS = 4000
MAX_RESTARTS = 2
PARTLY_CAP = 0.5
ACTION_FOR = {"yes": "keep", "partly": "downgrade", "no": "remove", "not_found": "remove", "after_cutoff": "remove"}
BAD = ("no", "not_found", "after_cutoff")


def _pairs(claims) -> list[tuple]:
    seen, out = set(), []
    for c in claims:
        for s in c.source_ids:
            if (c.id, s) not in seen:
                seen.add((c.id, s))
                out.append((c, s))
    return out


async def _source(sid: str, pack, ctx) -> dict | None:
    r = pack.items.get(sid) if pack is not None else None
    if r is None and getattr(ctx, "retrieval", None) is not None:
        try:
            r = await ctx.retrieval.doc(sid)
        except Exception:  # noqa: BLE001 - a lookup error counts as not found
            r = None
        if r is not None and not isinstance(r, dict):
            r = r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r)
    return r


async def check(agent: str, out, pack, ctx, hid: str | None = None) -> dict:
    """Ledger for one agent output. Returns {entries, hallucinated, partly, checked}."""
    as_of: date = ctx.config.as_of
    claims = list(iter_claims(out))
    entries: dict[tuple, dict] = {}
    to_judge = []
    for c, sid in _pairs(claims):
        r = await _source(sid, pack, ctx)
        d = _d(str(r.get("source_date"))) if r and r.get("source_date") else None
        if r is None:
            sup = "not_found"
        elif d is None or d >= as_of:
            sup = "after_cutoff" if d else "not_found"
        else:
            to_judge.append((c, sid, r))
            continue
        entries[(c.id, sid)] = {"claim_id": c.id, "source_id": sid, "source_date": d, "supports": sup}
    verdicts = await _judge(to_judge, ctx)
    for c, sid, r in to_judge:
        entries[(c.id, sid)] = {"claim_id": c.id, "source_id": sid, "source_date": _d(str(r["source_date"])),
                                "supports": verdicts[(c.id, sid)]}
    ledger = [{**e, "action": ACTION_FOR[e["supports"]], "agent": agent, "hypothesis_id": hid}
              for e in (entries[(c.id, s)] for c, s in _pairs(claims))]
    text = {c.id: c.text for c in claims}
    bad = [{**e, "claim_text": text[e["claim_id"]]} for e in ledger if e["supports"] in BAD]
    ctx.emit("citation_check", {"agent": agent, "hypothesis_id": hid, "entries": [
        {**e, "source_date": e["source_date"].isoformat() if e["source_date"] else None} for e in ledger]})
    return {"entries": ledger, "hallucinated": bad, "checked": len(ledger),
            "partly": [e for e in ledger if e["supports"] == "partly"]}


async def _judge(items: list, ctx) -> dict:
    verdicts: dict = {}
    if not items:
        return verdicts
    if isinstance(ctx.llm, StubLLM):  # offline: code checks only
        return {(c.id, sid): "yes" for c, sid, _ in items}
    system = system_prompt(AGENT)
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        payload = [{"claim_id": c.id, "claim_text": c.text, "source_id": sid, "source_title": r.get("title") or "",
                    "source_text": (r.get("body") or "")[:MAX_SOURCE_CHARS]} for c, sid, r in batch]
        led: ClaimLedger = await ctx.llm(agent=AGENT, schema=ClaimLedger, messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"pairs": payload}, ensure_ascii=False)}])
        asked = {(c.id, sid) for c, sid, _ in batch}
        got = {(e.claim_id, e.source_id): e.supports for e in led.entries
               if (e.claim_id, e.source_id) in asked and e.supports in ("yes", "partly", "no")}
        for k in asked:
            verdicts[k] = got.get(k, "no")
    return verdicts


def feedback(result: dict) -> str:
    """The restart message: which claims are not grounded, and why."""
    why = {"no": "the source does not support it", "not_found": "the source id does not exist",
           "after_cutoff": "the source is dated on/after the as-of date"}
    lines = [f"- {e['claim_id']}: \"{e['claim_text'][:200]}\" cites {e['source_id']}: {why[e['supports']]}"
             for e in result["hallucinated"]]
    hid = next((e.get("hypothesis_id") for e in result["hallucinated"] if e.get("hypothesis_id")), None)
    return ("CITATION CHECK FAILED (Agent 7)" + (f' for hypothesis_id "{hid}"' if hid else "")
            + ". These claims are not grounded in their cited sources:\n"
            + "\n".join(lines) + "\nRewrite the full JSON. Every evidence claim must say only what its cited "
            "source text says, citing ids from the evidence pack exactly. Drop or rephrase anything you cannot "
            "ground, or mark it kind \"reasoning\" with no sources.")


def apply(out, result: dict) -> list[dict]:
    """After the last restart: strip unsupported sources, downgrade 'partly'. Mutates out; returns a log."""
    bad = {(e["claim_id"], e["source_id"]) for e in result["entries"] if e["action"] == "remove"}
    partly = {e["claim_id"] for e in result["entries"] if e["action"] == "downgrade"}
    log = []
    for c in iter_claims(out):
        drop = [s for s in c.source_ids if (c.id, s) in bad]
        if drop:
            c.source_ids = [s for s in c.source_ids if s not in drop]
            log.append({"claim_id": c.id, "action": "removed_unsupported_sources", "ids": drop})
            if not c.source_ids:
                c.kind, c.confidence = "reasoning", min(c.confidence, 0.3)
                c.text = c.text if c.text.startswith("[unverified]") else f"[unverified] {c.text}"
        if c.id in partly and c.confidence > PARTLY_CAP:
            c.confidence = PARTLY_CAP
            log.append({"claim_id": c.id, "action": "partly_supported_capped"})
    return log
