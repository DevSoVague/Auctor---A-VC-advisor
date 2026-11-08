"""A1 Disease analysis -> WhyNow. Answers Q1 "Why is now the time?".

HOOK OUT: async def run(cfg: RunConfig, ctx) -> WhyNow

Steps (each is callable on its own, so Langflow/LangGraph can show them as nodes):
    retrieve -> make_messages -> [LLM] -> finalize (citation post-check) -> check (PS bars) -> commit
"""

from __future__ import annotations

import os
from datetime import date

from agents.analysis._common import EvidencePack, build_messages, emit_claims, post_check
from agents.analysis.tool_loop import with_tools
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
                  system: str | None = None, pitch: dict | None = None) -> list[dict]:
    payload = {"disease": cfg.disease, "lookback_from": lookback(cfg)}
    if pitch:  # A0 intake: the startup pitch this run tests (gated, fenced; claims are not evidence)
        payload["startup_pitch"] = pitch
        payload["ask"] = ("Answer why now is (or is not) the time for THIS pitch's mechanism and patients. "
                          "Shifts must rest on evidence-pack sources, not on the pitch's own claims.")
    return build_messages(AGENT, payload, pack, feedback, cfg, system)


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


NOVELTY_BRIEF = (
    "STEP 1 OF 2: BRING THE EVIDENCE STORE UP TO DATE (do this now; analysis comes later).\n"
    "Window: {floor} <= published < {as_of}. The store's newest records are listed by store_coverage.\n"
    "1. Call store_coverage.\n"
    "2. Find COPD drug / trial evidence newer than the store holds: pubmed_search (e.g. 'COPD[ti] AND "
    "(randomized controlled trial[pt] OR clinical trial, phase III[pt] OR clinical trial, phase II[pt])', "
    "published_after = the store's latest pubmed date), trials_search for COPD trials updated recently, and "
    "web_search if available.\n"
    "3. For each relevant result with in_store=false (a COPD drug, mechanism or trial result; skip "
    "rehabilitation-only, devices, surveys): fetch_source, then ingest_document with its fetch_id. Aim for "
    "the {limit} most important new items. Duplicates and undated or out-of-window items are refused for you.\n"
    "4. When done, reply with one line per item: id | published date | why it matters. No JSON.")

ANALYSIS_BRIEF = ("STEP 2 OF 2: the why-now analysis. Use search_evidence / list_trials / paper_findings / "
                  "list_drugs; newer evidence first, including what step 1 just added.")


async def novelty_and_tools(ctx, cfg: RunConfig, msgs: list[dict], pack: EvidencePack) -> list[dict]:
    """A1's tool phase. Step 1 (novelty): find new evidence and write it to the store. Step 2: gather for
    the analysis. Emits new_evidence. No-op unless AGENT_TOOLS=1."""
    from agents.analysis.tool_loop import gather, llm_of, tools_on

    on = ctx.signals.get("tools")
    if not (tools_on() if on is None else on):
        return msgs
    from agents.analysis.db_ingest import Ingestor
    from agents.analysis.db_tools import TOOLS, ToolExecutor

    ing = Ingestor(cfg.as_of)
    ing.pack = pack
    fns, schemas = ing.tools()
    ex = ToolExecutor(AGENT, cfg.as_of, pack, extra=fns, on_call=lambda e: ctx.emit("tool_call", e))
    brief = NOVELTY_BRIEF.format(floor=ex.floor, as_of=cfg.as_of, limit=ing.max_ingests)
    g = await gather(llm_of(ctx), agent=AGENT, messages=msgs[:1], executor=ex,
                     tools=[TOOLS["store_coverage"], TOOLS["web_search"]] + schemas, brief=brief, raw_brief=True,
                     first_choice="required", max_rounds=int(os.getenv("A1_TOOL_ROUNDS", 10)),
                     max_calls=int(os.getenv("A1_TOOL_CALLS", 30)),
                     max_seconds=float(os.getenv("A1_TOOL_SECONDS", 420)))
    ctx.signals.setdefault("tool_calls", []).extend(g["calls"])
    ctx.signals["ingest"] = ing.outcomes
    ctx.emit("new_evidence", {"agent": AGENT, "inserted": ing.inserted, "note": g.get("note", "")[:1500],
                              "outcomes": [{k: o.get(k) for k in ("status", "id", "reason")} for o in ing.outcomes]})
    if ing.inserted:
        msgs = list(msgs) + [{"role": "user", "content": "NEW EVIDENCE ADDED TO THE STORE THIS RUN (step 1):\n"
                              + (g.get("note") or "\n".join(ing.inserted))}]
    return await with_tools(ctx, AGENT, msgs, pack, brief=ANALYSIS_BRIEF)


# ---------------------------------------------------------------- whole agent
async def run(cfg: RunConfig, ctx) -> WhyNow:
    ctx.emit("agent_started", {"agent": AGENT, "hypothesis_id": None})
    pack, _ = await retrieve(cfg, ctx)
    msgs = make_messages(cfg, pack, ctx.feedback)
    msgs = await novelty_and_tools(ctx, cfg, msgs, pack)  # DB read/write + web (AGENT_TOOLS=1), else no-op
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
