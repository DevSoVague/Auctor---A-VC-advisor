"""A6 Red Teamer -> Verdict per card. Answers Q5 "What could kill the hypothesis?".

HOOK OUT: async def run(inp: RedTeamInput, ctx) -> Verdict
HOOK OUT: Verdict.revise_target in {a1_disease..a5_active}; the flow (person 5) honours it once.
HOOK OUT: event "kill" {hypothesis_id, reason, kill_criterion} -> person 5 writes `decisions`.
HOOK OUT: event "revise" {hypothesis_id, revise_target, reason}.

A deterministic checklist runs before the model, so the model must answer specific
findings (retractions, terminated trials, single-lab claims, contradictions) instead of
inventing generic risks.
"""

from __future__ import annotations

from agents.analysis.tool_loop import with_checkers
from agents.analysis._common import (
    TIER_CAP, EvidencePack, build_messages, emit_claims, post_check,
)
from agents.analysis.schemas import HypothesisCard, RedTeamInput, Verdict

AGENT = "a6_redteam"
REVISE_TARGETS = {"a1_disease", "a2_mechanism", "a3_profile", "a4_inactive", "a5_active"}


class A6Error(RuntimeError):
    pass


SCHEMA = Verdict
CHECKLIST_QUERIES = ("failed did not meet primary endpoint", "not replicated single cohort",
                     "safety adverse events", "contradicts no association")


# ---------------------------------------------------------------- steps
async def retrieve(inp: RedTeamInput, ctx) -> tuple[EvidencePack, dict, list[dict]]:
    """Retrieval + deterministic risk checklist (retractions, prior failures, single-lab,
    safety, contradictions) that the model must then answer."""
    cfg, terms = ctx.config, _terms(inp)
    pack, log = EvidencePack(cfg.as_of), []
    checklist: dict[str, list[str]] = {k: [] for k in (
        "retractions", "prior_failures", "single_lab_or_unreplicated", "safety", "contradictions")}

    for q in CHECKLIST_QUERIES:
        added = pack.add(await ctx.retrieval.search(f"{terms} {q}", k=6))
        log.append({"call": f"search('{terms} {q}', k=6)", "kept": [d["id"] for d in added]})
        for d in added:
            tags = set(d.get("tags", []))
            if "failure" in tags:
                checklist["prior_failures"].append(d["id"])
            if "unreplicated" in tags or "single_lab" in tags:
                checklist["single_lab_or_unreplicated"].append(d["id"])
            if "safety" in tags:
                checklist["safety"].append(d["id"])
            if "contradiction" in tags:
                checklist["contradictions"].append(d["id"])

    hits = []
    for t in await ctx.retrieval.trials(condition=cfg.disease):
        hay = f"{t.get('title','')} {t.get('intervention','')} {t.get('mechanism','')}".lower()
        if t.get("status") in ("terminated", "withdrawn", "completed_negative") and \
                any(w in hay for w in terms.lower().split()):
            pack.add([t])
            hits.append(t["id"])
            checklist["prior_failures"].append(
                f"{t['id']} ({t['status']}: {t.get('why_stopped') or 'no reason given'})")
    log.append({"call": f"trials(condition={cfg.disease}) terminated/withdrawn/negative matching '{terms}'",
                "kept": hits})

    dois = {d["doi"] for d in pack.items.values() if d.get("doi")}
    hits = []
    for r in await ctx.retrieval.retractions():
        if r["doi"] in dois:
            pack.add([r])
            hits.append(r["id"])
            checklist["retractions"].append(f"{r['id']} retracts {r['doi']}")
    log.append({"call": f"retractions() for {len(dois)} DOI(s) in pack", "kept": hits})

    hits = []
    for c_ids in _upstream_sources(inp):  # cited upstream docs are fair game to re-read
        if (d := await ctx.retrieval.doc(c_ids)) is not None:
            hits += [r["id"] for r in pack.add([d])]
    log.append({"call": "doc(id) for every source cited upstream (A2/A3/A4/A5)", "kept": hits})
    return pack, checklist, log


def make_messages(inp: RedTeamInput, pack: EvidencePack, checklist: dict, feedback: str | None,
                  cfg, system: str | None = None) -> list[dict]:
    card = inp.card
    payload = {"hypothesis_id": card.id,
               "card": card.model_dump(mode="json"),
               "product_profile": inp.profile.model_dump(mode="json"),
               "why_now_shifts": [s.summary for s in inp.why_now.shifts],
               "inactive_assets": inp.inactive.model_dump(mode="json") if inp.inactive else None,
               "landscape": inp.landscape.model_dump(mode="json") if inp.landscape else None,
               "checklist_findings": checklist,
               "allowed_revise_targets": sorted(REVISE_TARGETS)}
    return build_messages(AGENT, payload, pack, feedback, cfg, system)


def fix_prompt(problems: list[str]) -> dict:
    return {"role": "user", "content": "Fix these problems and return the full JSON:\n- " + "\n- ".join(problems)}


def finalize(out: Verdict, card: HypothesisCard, pack: EvidencePack) -> list[dict]:
    return post_check(out, AGENT, pack, cap=TIER_CAP[card.evidence_tier], prefix=f"{card.id}-")


def commit(ctx, card: HypothesisCard, out: Verdict, checklist: dict, log: list[dict]) -> None:
    """Emit claims + the kill / revise hooks + agent_done."""
    ctx.signals.setdefault("postcheck", {}).setdefault(AGENT, {})[card.id] = log
    ctx.signals.setdefault("a6_checklist", {})[card.id] = checklist
    emit_claims(ctx, AGENT, out, card.id)
    if out.verdict == "kill":
        top = out.risks[0] if out.risks else None
        ctx.emit("kill", {"agent": AGENT, "hypothesis_id": card.id,
                          "reason": top.text if top else out.premortem,
                          "kill_criterion": top.kill_criterion if top else ""})
    elif out.verdict == "revise":
        ctx.emit("revise", {"agent": AGENT, "hypothesis_id": card.id, "revise_target": out.revise_target,
                            "reason": out.risks[0].text if out.risks else out.premortem})
    ctx.emit("agent_done", {"agent": AGENT, "hypothesis_id": card.id,
                            "output": out.model_dump(mode="json"), "checklist": checklist})


# ---------------------------------------------------------------- whole agent
async def run(inp: RedTeamInput, ctx) -> Verdict:
    card = inp.card
    ctx.emit("agent_started", {"agent": AGENT, "hypothesis_id": card.id})
    pack, checklist, _ = await retrieve(inp, ctx)
    msgs = make_messages(inp, pack, checklist, ctx.feedback, ctx.config)
    msgs = await with_checkers(ctx, AGENT, msgs, pack)  # checker subagents with DB tools (AGENT_TOOLS=1), else no-op
    out: Verdict = await ctx.llm(agent=AGENT, messages=msgs, schema=Verdict)
    if problems := check(out, card.id):
        msgs.append(fix_prompt(problems))
        out = await ctx.llm(agent=AGENT, messages=msgs, schema=Verdict)
        if problems := check(out, card.id):
            raise A6Error(f"{card.id}: " + "; ".join(problems))
    log = finalize(out, card, pack)
    commit(ctx, card, out, checklist, log)
    return out


def check(out: Verdict, hid: str) -> list[str]:
    p = []
    out.hypothesis_id = hid
    if out.verdict == "revise" and out.revise_target not in REVISE_TARGETS:
        p.append(f"revise_target must be one of {sorted(REVISE_TARGETS)}")
    if out.verdict != "revise":
        out.revise_target = None
    if not out.risks:
        p.append("list at least one risk")
    for r in out.risks:
        if not r.kill_criterion.strip() or not r.cheapest_test.strip():
            p.append(f"risk '{r.text[:40]}' needs a kill_criterion and a cheapest_test")
        if not r.claims:
            p.append(f"risk '{r.text[:40]}' needs a claim (cited evidence or labelled reasoning)")
    if not out.premortem.strip():
        p.append("premortem is empty")
    return p


def _terms(inp: RedTeamInput) -> str:
    syms = [t.split(":")[-1] for t in inp.card.target_ids]
    return " ".join(dict.fromkeys(syms + inp.card.mechanism.split()[:3]))


def _upstream_sources(inp: RedTeamInput) -> list[str]:
    from agents.analysis._common import iter_claims
    ids: list[str] = []
    for obj in (inp.card, inp.profile, inp.landscape, inp.inactive):
        if obj is not None:
            for c in iter_claims(obj):
                ids += c.source_ids
    return list(dict.fromkeys(ids))
