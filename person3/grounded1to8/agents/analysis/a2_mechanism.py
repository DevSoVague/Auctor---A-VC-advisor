"""A2 Mechanistic analysis -> 3-5 ranked HypothesisCards. Answers Q2 "Which mechanism?".

HOOK OUT: async def run(why_now: WhyNow, ctx) -> list[HypothesisCard]
Side output (HOOK OUT): ctx.signals["pub_counts"] {target_symbol: n_docs}  -> person 4
(crowding score) and person 1 (naive most-published baseline). Also emitted as a
"signal" event.

The model proposes and writes cards; evidence_tier, validation_level, the tractability
gate and the converging-evidence rule are computed here in code, not by the model.
"""

from __future__ import annotations

import asyncio
from agents.analysis.tool_loop import with_tools
from agents.analysis._common import (
    TIER_CAP, EvidencePack, build_messages, emit_claims, post_check,
)
from agents.analysis.schemas import A2Output, HypothesisCard, WhyNow

AGENT = "a2_mechanism"
MIN_CARDS, MAX_CARDS = 3, 5
GENETIC_STRONG, GENETIC_WEAK = 0.5, 0.2


SCHEMA = A2Output


# ---------------------------------------------------------------- steps
async def retrieve(why_now: WhyNow, ctx) -> dict:
    """Deterministic retrieval: Open Targets associations, in-disease drugs, segment and
    per-target literature, plus publication counts (naive baseline / crowding signal)."""
    cfg = ctx.config
    pack, log = EvidencePack(cfg.as_of), []
    assoc = await ctx.retrieval.targets(cfg.disease_id)
    pack.add([{"id": assoc[0]["id"], "source_date": assoc[0]["source_date"],
               "title": "Open Targets association release", "body": _assoc_table(assoc)}]
             if assoc else [])
    log.append({"call": f"targets({cfg.disease_id})", "kept": [assoc[0]["id"]] if assoc else [],
                "n_targets": len(assoc)})
    disease_drugs = await ctx.retrieval.drugs(indication=cfg.disease)
    added = pack.add(disease_drugs)
    log.append({"call": f"drugs(indication={cfg.disease})", "kept": [r["id"] for r in added]})
    for seg in why_now.segments:
        added = pack.add(await ctx.retrieval.search(f"{cfg.disease} {seg.name} mechanism", k=8))
        log.append({"call": f"search('{cfg.disease} {seg.name} mechanism', k=8)", "kept": [r["id"] for r in added]})
    for a in assoc[:15]:
        added = pack.add(await ctx.retrieval.search(f"{a['symbol']} {cfg.disease}", k=4))
        log.append({"call": f"search('{a['symbol']} {cfg.disease}', k=4)", "kept": [r["id"] for r in added]})
    # Publication counts are a crowding signal over the paper corpus. sources=["pubmed"] keeps them off
    # the web (one Tavily call per target would be hundreds of calls) and off non-paper records.
    # The store has ~300 targets and each count is one vector search, so run them 8 at a time.
    gate = asyncio.Semaphore(8)

    async def count(symbol: str) -> int:
        async with gate:
            return len(await ctx.retrieval.search(symbol, sources=["pubmed"], k=1000))

    counts = await asyncio.gather(*(count(a["symbol"]) for a in assoc))
    pub_counts = {a["symbol"]: n for a, n in zip(assoc, counts)}
    return {"pack": pack, "assoc": assoc, "disease_drugs": disease_drugs,
            "pub_counts": pub_counts, "log": log}


def make_messages(why_now: WhyNow, assoc: list[dict], pack: EvidencePack, feedback: str | None,
                  cfg, system: str | None = None, pitch: dict | None = None) -> list[dict]:
    payload = {"segments": [s.model_dump(mode="json") for s in why_now.segments],
               "shifts": [{"on": s.on, "type": s.type, "summary": s.summary} for s in why_now.shifts],
               "candidate_targets": [{k: a.get(k) for k in (
                   "target_id", "symbol", "genetic_score", "known_drug_max_phase_in_disease",
                   "tractability", "safety")} for a in assoc],
               "ask": f"Propose {MAX_CARDS + 1} candidate cards; weaker ones will be filtered."}
    if pitch:  # A0 intake: build the run around the startup's pitch
        payload["startup_pitch"] = pitch
        payload["ask"] += (" The FIRST card must be the pitch's own mechanism hypothesis (its targets and "
                           "direction), graded on the evidence pack like any other; the rest are the strongest "
                           "alternative mechanisms the pitch must beat.")
    return build_messages(AGENT, payload, pack, feedback, cfg, system)


def grade_and_rank(out: A2Output, assoc: list[dict], disease_drugs: list[dict], pack: EvidencePack,
                   pub_counts: dict) -> dict:
    """Deterministic: tier/level from data (not the model), gates, ranking, 3-5 cards."""
    by_id = {a["target_id"]: a for a in assoc}
    kept, rejected = [], []
    for card in out.cards:
        g = grade(card, by_id, disease_drugs, pack)
        card.evidence_tier, card.validation_level = g["tier"], g["level"]
        (rejected if g["reject"] else kept).append((card, g))
    kept.sort(key=lambda cg: (cg[1]["tier"], -cg[1]["lines"]))
    warning = None
    if len(kept) < MIN_CARDS:  # backfill, labelled, so the run still has 3 cards
        for card, g in rejected[: MIN_CARDS - len(kept)]:
            card.validation_level = "bronze"
            card.biggest_uncertainty = f"[failed gate: {g['reject']}] {card.biggest_uncertainty}"
            kept.append((card, g))
        warning = f"only {len(kept)} cards passed gates"
    cards = [c for c, _ in kept[:MAX_CARDS]]
    grades = {}
    for i, card in enumerate(cards, 1):
        card.id = card.id or f"h{i}"
        g = next(g for c, g in kept if c is card)
        grades[card.id] = {**g, "rank": i,
                           "pub_count": sum(pub_counts.get(by_id[t]["symbol"], 0)
                                            for t in card.target_ids if t in by_id)}
    return {"cards": cards, "grades": grades, "warning": warning,
            "rejected": [{"hypothesis_id": c.id, "mechanism": c.mechanism, "reason": g["reject"]}
                         for c, g in rejected if c not in cards]}


def finalize(cards: list[HypothesisCard], pack: EvidencePack) -> list[dict]:
    """Citation post-check per card, confidence capped by that card's tier."""
    log = []
    for card in cards:
        log += post_check(card, AGENT, pack, cap=TIER_CAP[card.evidence_tier], prefix=f"{card.id}-")
    return log


def commit(ctx, cards: list[HypothesisCard], graded: dict, pub_counts: dict, log: list[dict]) -> None:
    ctx.signals["pub_counts"] = pub_counts
    ctx.emit("signal", {"agent": AGENT, "name": "pub_counts", "value": pub_counts})
    for r in graded["rejected"]:
        ctx.emit("card_rejected", {"agent": AGENT, "hypothesis_id": r["hypothesis_id"], "reason": r["reason"]})
    if graded["warning"]:
        ctx.emit("warning", {"agent": AGENT, "msg": graded["warning"]})
    ctx.signals.setdefault("postcheck", {})[AGENT] = log
    ctx.signals["a2_grades"] = graded["grades"]
    for card in cards:
        emit_claims(ctx, AGENT, card, card.id)
    ctx.emit("agent_done", {"agent": AGENT, "hypothesis_id": None,
                            "output": [c.model_dump(mode="json") for c in cards],
                            "grades": graded["grades"]})


# ---------------------------------------------------------------- whole agent
async def run(why_now: WhyNow, ctx) -> list[HypothesisCard]:
    ctx.emit("agent_started", {"agent": AGENT, "hypothesis_id": None})
    r = await retrieve(why_now, ctx)
    msgs = make_messages(why_now, r["assoc"], r["pack"], ctx.feedback, ctx.config)
    msgs = await with_tools(ctx, AGENT, msgs, r["pack"])  # DB tool calls (AGENT_TOOLS=1), else no-op
    out: A2Output = await ctx.llm(agent=AGENT, messages=msgs, schema=A2Output)
    graded = grade_and_rank(out, r["assoc"], r["disease_drugs"], r["pack"], r["pub_counts"])
    cards = graded["cards"]
    log = finalize(cards, r["pack"])
    commit(ctx, cards, graded, r["pub_counts"], log)
    return cards


def grade(card: HypothesisCard, by_id: dict, disease_drugs: list[dict],
          pack: EvidencePack) -> dict:
    """Evidence tier (1 best) + validation level from human evidence lines."""
    targets = [by_id[t] for t in card.target_ids if t in by_id]
    symbols = {t["symbol"].lower() for t in targets}
    genetic = max((t.get("genetic_score") or 0 for t in targets), default=0)
    phase_in_disease = max([t.get("known_drug_max_phase_in_disease") or 0 for t in targets]
                           + [d["max_phase_in_indication"] for d in disease_drugs
                              if symbols & {s.lower() for s in d.get("target_symbols", [])}],
                           default=0)
    human_docs = [r for r in pack.items.values() if "human" in r.get("tags", [])
                  and any(s in f"{r.get('title','')} {r.get('body','')}".lower() for s in symbols)]
    lines = sum([genetic >= GENETIC_WEAK, phase_in_disease >= 2, len(human_docs) >= 1])
    # Failed precedent in this disease is evidence AGAINST, not for: reaching Phase 3 and
    # failing must not earn tier 1. It still counts as a human line (data exist).
    failed = [r["id"] for r in pack.items.values() if "failure" in r.get("tags", [])
              and any(s in f"{r.get('title','')} {r.get('body','')} {' '.join(r.get('tags', []))}".lower()
                      for s in symbols)]

    if genetic >= GENETIC_STRONG or (phase_in_disease >= 3 and not failed):
        tier = 1
    elif genetic >= GENETIC_WEAK or (phase_in_disease >= 2 and not failed):
        tier = 2
    elif human_docs:
        tier = 3
    else:
        tier = 4
    level = "gold" if tier == 1 and lines >= 2 else "silver" if tier <= 2 else "bronze"

    reject = None
    if not targets:
        reject = "target_ids not in the Open Targets association table"
    elif not any(any((t.get("tractability") or {}).values()) for t in targets):
        reject = "no tractable modality for any target"
    elif genetic < GENETIC_WEAK and lines < 2:
        reject = "weak genetics and fewer than 2 converging human evidence lines"
    return {"tier": tier, "level": level, "lines": lines, "genetic": genetic,
            "phase_in_disease": phase_in_disease, "human_docs": len(human_docs),
            "failed_precedent": failed, "reject": reject}


def _assoc_table(assoc: list[dict]) -> str:
    rows = ["target_id | symbol | genetic | max_phase_in_disease | tractability | safety"]
    for a in assoc:
        tr = ",".join(k for k, v in (a.get("tractability") or {}).items() if v) or "none"
        rows.append(f"{a['target_id']} | {a['symbol']} | {a.get('genetic_score')} | "
                    f"{a.get('known_drug_max_phase_in_disease')} | {tr} | "
                    f"{'; '.join(a.get('safety') or [])}")
    return "\n".join(rows)
