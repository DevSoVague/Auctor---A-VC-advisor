"""A1 Disease analysis -> WhyNow. Answers Q1 "Why is now the time?".

HOOK OUT: async def run(cfg: RunConfig, ctx) -> WhyNow

Steps (each is callable on its own, so Langflow/LangGraph can show them as nodes):
    retrieve -> make_messages -> [LLM] -> finalize (citation post-check) -> check (PS bars) -> commit
"""

from __future__ import annotations

from datetime import date

from agents.analysis._common import EvidencePack, build_messages, emit_claims, post_check
from agents.analysis.schemas import RunConfig, WhyNow

AGENT = "a1_disease"
SCHEMA = WhyNow
LOOKBACK_YEARS = 5

QUERIES = [
    "FDA guidance endpoint exacerbation",
    "eosinophil biomarker patient selection",
    "trial design endpoint FEV1 SGRQ",
    "phenotype segment type 2 inflammation",
    "failed phase 3 did not meet primary endpoint",
    "triple therapy mortality standard of care",
]


class A1Error(RuntimeError):
    pass


def lookback(cfg: RunConfig) -> date:
    return date(cfg.as_of.year - LOOKBACK_YEARS, cfg.as_of.month, 1)


# ---------------------------------------------------------------- steps
async def retrieve(cfg: RunConfig, ctx, queries: list[str] | None = None) -> tuple[EvidencePack, list[dict]]:
    """Deterministic retrieval. Returns the evidence pack and a log of every call."""
    lb = lookback(cfg).isoformat()
    pack, log = EvidencePack(cfg.as_of), []
    added = pack.add(await ctx.retrieval.approvals(indication=cfg.disease))
    log.append({"call": f"approvals(indication={cfg.disease})", "kept": [r["id"] for r in added]})
    trials = await ctx.retrieval.trials(condition=cfg.disease)
    added = pack.add([t for t in trials if t["source_date"] >= lb
                      and ("3" in t.get("phase", "") or t.get("status") == "terminated")])
    log.append({"call": f"trials(condition={cfg.disease}) phase 3 or terminated, since {lb}",
                "kept": [r["id"] for r in added]})
    for q in queries or QUERIES:
        added = pack.add([d for d in await ctx.retrieval.search(f"{cfg.disease} {q}", k=8)
                          if d["source_date"] >= lb])
        log.append({"call": f"search('{cfg.disease} {q}', k=8) since {lb}", "kept": [r["id"] for r in added]})
    return pack, log


def make_messages(cfg: RunConfig, pack: EvidencePack, feedback: str | None,
                  system: str | None = None) -> list[dict]:
    return build_messages(AGENT, {"disease": cfg.disease, "lookback_from": lookback(cfg)},
                          pack, feedback, cfg, system)


def finalize(out: WhyNow, cfg: RunConfig, pack: EvidencePack) -> list[dict]:
    """Citation post-check + drop shifts dated on/after the cutoff."""
    log = post_check(out, AGENT, pack)
    late = [s.summary[:50] for s in out.shifts if s.on >= cfg.as_of]
    out.shifts = [s for s in out.shifts if s.on < cfg.as_of]
    if late:
        log.append({"action": "dropped_post_cutoff_shifts", "ids": late})
    return log


def check(out: WhyNow) -> list[str]:
    """PS bars for A1."""
    p = []
    if len(out.shifts) < 3:
        p.append(f"need at least 3 shifts, got {len(out.shifts)}")
    if not any(s.type == "negative" for s in out.shifts):
        p.append("include at least one 'negative' shift that raises the bar")
    for s in out.shifts:
        if not s.effect_on:
            p.append(f"shift '{s.summary[:40]}' must state effect_on (time/cost/selection)")
        if not any(c.source_ids for c in s.claims):
            p.append(f"shift '{s.summary[:40]}' needs a cited claim from the evidence pack")
    if not out.segments:
        p.append("define at least one patient segment")
    for g in out.segments:
        if len(g.inclusion_criteria) < 2:
            p.append(f"segment '{g.name}' needs >=2 concrete inclusion criteria")
    return p


def fix_prompt(problems: list[str]) -> dict:
    return {"role": "user", "content": "Fix these problems and return the full JSON:\n- " + "\n- ".join(problems)}


def commit(ctx, out: WhyNow, log: list[dict]) -> None:
    ctx.signals.setdefault("postcheck", {})[AGENT] = log
    emit_claims(ctx, AGENT, out)
    ctx.emit("agent_done", {"agent": AGENT, "hypothesis_id": None, "output": out.model_dump(mode="json")})


# ---------------------------------------------------------------- whole agent
async def run(cfg: RunConfig, ctx) -> WhyNow:
    ctx.emit("agent_started", {"agent": AGENT, "hypothesis_id": None})
    pack, _ = await retrieve(cfg, ctx)
    msgs = make_messages(cfg, pack, ctx.feedback)
    out: WhyNow = await ctx.llm(agent=AGENT, messages=msgs, schema=WhyNow)
    log = finalize(out, cfg, pack)
    if problems := check(out):  # one semantic retry, then fail loudly
        msgs.append(fix_prompt(problems))
        out = await ctx.llm(agent=AGENT, messages=msgs, schema=WhyNow)
        log += finalize(out, cfg, pack)
        if problems := check(out):
            raise A1Error("; ".join(problems))
    commit(ctx, out, log)
    return out
