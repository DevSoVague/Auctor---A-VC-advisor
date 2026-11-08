"""Agent 5: active-in-disease assets, the competitor landscape for one hypothesis.

Code establishes the facts: which programs exist in the disease, their stage,
owner, deprioritization flags, funding signal, catalysts and the crowding score.
One model call judges them: relevance (a program it leaves out is dropped),
novel edge and fit to the product profile, and the mechanism of programs code
cannot place (it reads synonyms; such programs count at reduced weight). The
facts are re-imposed after the call, so the model can drop a program but not
invent one, restage it, overrule code's placement, or cite a record it was not shown.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TypedDict

from frontier.agents.competitor.common import (
    STAGE_LABELS,
    UNKNOWN,
    Candidate,
    Mechanism,
    collect_assets,
    drug_index,
    evidence,
    evidence_claim,
    judge_messages,
    match_replies,
    model_placement_claim,
    number_claims,
    owner_type,
    shown,
    shown_claims,
    stage_rank,
)
from frontier.agents.competitor.landscape import (
    DEFAULT_CROWDING,
    CrowdingParams,
    catalysts,
    crowding_score,
    deprioritization,
    stage_weight,
)
from frontier.agents.competitor.steps import build_graph, run_steps
from frontier.agents.schemas import A5Input, Claim, Landscape, Program
from frontier.core.context import RunContext
from frontier.core.retrieval import Record

AGENT = "a5_active"
# Programs sent to the model per placement group, most advanced first.
CAPS = {"programs": 30, "orthogonal": 20, "open": 20}
_GROUP = {"on": "programs", "off": "orthogonal", "open": "open"}
PUBLICATION_WINDOW = timedelta(days=3 * 365)
PUBLICATION_SEARCH_K = 1000
_FIT_ORDER = {"yes": 0, "partly": 1, "no": 2}
log = logging.getLogger(__name__)


def _claim(text: str, source_ids: list[str]) -> Claim:
    return evidence_claim(AGENT, text, source_ids)


async def _facts(candidate: Candidate, ctx: RunContext) -> tuple[Program, list[Record]]:
    """The program as code knows it, and the records shown for it (the only ones it may cite)."""
    source = candidate.stage_source
    owner = source.fields.get("sponsor") or UNKNOWN
    sponsor_class = (candidate.lead_trial or source).fields.get("sponsor_class")
    filings = await ctx.retrieval.filings(company=owner) if owner != UNKNOWN else []
    kind = owner_type(owner, sponsor_class, filings)
    form_d = [f for f in filings if f.fields.get("form") == "D"]
    funding = max(form_d, key=lambda f: f.source_date) if kind == "startup" and form_d else None

    name, stage = candidate.name, STAGE_LABELS.get(candidate.stage or "", "Phase not reported")
    flagged, reason = (
        (False, None)
        if candidate.stage == "approved"
        else deprioritization(candidate.trials, ctx.config.as_of)
    )
    claims = [_claim(f"{name} ({owner}) is at {stage} in {ctx.config.disease}.", [source.id])]
    if flagged:
        trial_ids = [t.id for t in candidate.trials]
        claims.append(_claim(f"{name} looks deprioritized: {reason}.", trial_ids))
    if funding:
        claims.append(_claim(f"{owner} filed a Form D on {funding.source_date}.", [funding.id]))

    lead = candidate.lead_trial
    program = Program(
        asset=candidate.name,
        owner=owner,
        owner_type=kind,
        mechanism=candidate.mechanism,
        modality=candidate.modality,
        stage=stage,
        lead_trial=lead.id if lead else None,
        novel_edge="",
        profile_fit="no",
        deprioritized=flagged,
        flag_reason=reason,
        funding_signal=f"Form D filed {funding.source_date}" if funding else None,
        claims=claims,
    )
    recent = sorted([*candidate.trials, *candidate.approvals], key=lambda r: r.source_date)
    records = shown([source, lead, funding, candidate.drug, *reversed(recent)])
    return program, records


async def _publications(ctx: RunContext, mechanism: Mechanism) -> int:
    """Papers on the mechanism in the PUBLICATION_WINDOW before as_of."""
    if not mechanism.query:
        return 0
    hits = await ctx.retrieval.search(mechanism.query, ["pubmed"], PUBLICATION_SEARCH_K)
    since = ctx.config.as_of - PUBLICATION_WINDOW
    return sum(1 for hit in hits if hit.source_date >= since)


async def _crowding(
    ctx: RunContext,
    target: Mechanism,
    candidates: list[Candidate],
    params: CrowdingParams,
    model_placed: list[str],
) -> dict:
    """Crowding of the hypothesis mechanism, against the busiest mechanism in the disease.

    Every mechanism is measured the same way (programs code places on it, papers
    matching its words), so the comparison is like for like; the hypothesis also
    counts the stages of programs the model placed on it, at the model's weight.
    Returns the score with the signals and references behind it, for the audit trail.
    """
    others = {Mechanism.of(c.mechanism_text, c.targets) for c in candidates if c.drug}
    mechanisms = [target, *(others - {target})]
    papers = await asyncio.gather(*(_publications(ctx, m) for m in mechanisms))
    weights = params.placement_weights
    signals = [
        {
            "programs": weights["code"]
            * stage_weight([c.stage for c in candidates if c.stage and m.covers(c)], params),
            "publications": count,
        }
        for m, count in zip(mechanisms, papers, strict=True)
    ]
    signals[0]["programs"] += weights["model"] * stage_weight(model_placed, params)
    references = {name: max(s[name] for s in signals) for name in signals[0]}
    score = crowding_score(signals[0], references, params)
    return {"score": score, "signals": signals[0], "references": references}


def _most_advanced(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(
        candidates, key=lambda c: (stage_rank(c.stage), c.latest.source_date), reverse=True
    )


def _brief(program: Program, records: list[Record], group: str) -> dict:
    facts = program.model_dump(mode="json", exclude={"novel_edge", "profile_fit", "claims"})
    return {"group": group, **facts, "evidence": evidence(records)}


def _merge(facts: Program, judged: Program, shown: set[str]) -> Program:
    """Code's facts plus the model's judgment; model claims citing unseen records are dropped."""
    return facts.model_copy(
        update={
            "novel_edge": judged.novel_edge,
            "profile_fit": judged.profile_fit,
            "claims": [*facts.claims, *shown_claims(AGENT, judged.claims, shown)],
        }
    )


@dataclass
class Kept:
    """A program the model kept, merged with code's facts; by_model if the model placed it."""

    candidate: Candidate
    program: Program
    by_model: bool


class A5State(TypedDict, total=False):
    """What the steps share. Inputs first, then each step's output in order."""

    inp: A5Input
    ctx: RunContext
    params: CrowdingParams
    candidates: list[Candidate]
    target: Mechanism
    shortlisted: list[tuple[Candidate, str]]
    cut: list[str]
    facts: list[tuple[Program, list[Record]]]
    draft: Landscape
    kept: dict[str, list[Kept]]
    matching: dict[str, list[str]]
    crowding: dict
    landscape: Landscape


async def gather(s: A5State) -> A5State:
    """Every asset in the disease, from trials and approvals, named through ChEMBL."""
    ctx = s["ctx"]
    trials, approvals, drugs = await asyncio.gather(
        ctx.retrieval.trials(condition=ctx.config.disease),
        ctx.retrieval.approvals(indication=ctx.config.disease),
        drug_index(ctx, s["inp"].card.mechanism),
    )
    return {"candidates": collect_assets(trials, approvals, drugs)}


async def shortlist(s: A5State) -> A5State:
    """Place each asset against the hypothesis mechanism; keep the most advanced per group.

    Assets past a group's cap are named under `cut`, so the audit trail shows them.
    """
    card = s["inp"].card
    target = Mechanism.of(card.mechanism, card.target_ids)
    groups: dict[str, list[Candidate]] = {group: [] for group in CAPS}
    for candidate in s["candidates"]:
        groups[_GROUP[target.place(candidate)]].append(candidate)
    ranked = {group: _most_advanced(groups[group]) for group in CAPS}
    cut = [c.name for group, cap in CAPS.items() for c in ranked[group][cap:]]
    if cut:
        log.warning("%s: %d assets over the caps, not judged: %s", AGENT, len(cut), cut)
    return {
        "target": target,
        "shortlisted": [(c, group) for group, cap in CAPS.items() for c in ranked[group][:cap]],
        "cut": cut,
    }


async def establish(s: A5State) -> A5State:
    """Each shortlisted program as code knows it, with the records it may cite."""
    facts = await asyncio.gather(*(_facts(c, s["ctx"]) for c, _ in s["shortlisted"]))
    return {"facts": list(facts)}


async def judge(s: A5State) -> A5State:
    """The one model call; skipped when there is nothing to judge."""
    if not s["shortlisted"]:
        empty = {"programs": [], "orthogonal": [], "crowding_score": 0, "catalysts": []}
        return {"draft": Landscape(hypothesis_id=s["inp"].card.id, **empty)}
    pairs = zip(s["facts"], s["shortlisted"], strict=True)
    briefs = [_brief(program, records, group) for (program, records), (_, group) in pairs]
    messages = judge_messages(AGENT, s["ctx"], s["inp"], programs=briefs)
    return {"draft": await s["ctx"].llm(agent=AGENT, messages=messages, schema=Landscape)}


async def merge(s: A5State) -> A5State:
    """Pair replies with programs, re-impose code's facts, rank by fit, then stage.

    Code's placement stands for "programs" and "orthogonal"; for "open" programs
    the model's list decides. Programs the model left out, and replies that name
    no program, are recorded under `matching`, never dropped silently.
    """
    draft = s["draft"]
    replies = [
        ((group, p), [p.asset, p.lead_trial])
        for group, programs in (("programs", draft.programs), ("orthogonal", draft.orthogonal))
        for p in programs
    ]
    keys = {
        str(i): [*c.keys(), c.lead_trial.id if c.lead_trial else None]
        for i, (c, _) in enumerate(s["shortlisted"])
    }
    matched, unmatched = match_replies(keys, replies)

    kept: dict[str, list[Kept]] = {"programs": [], "orthogonal": []}
    dropped = []
    for i, ((candidate, placed), (program, records)) in enumerate(
        zip(s["shortlisted"], s["facts"], strict=True)
    ):
        if (reply := matched.get(str(i))) is None:
            dropped.append(candidate.name)
            continue
        chosen, verdict = reply
        group = chosen if placed == "open" else placed
        merged = _merge(program, verdict, {r.id for r in records})
        by_model = placed == "open" and group == "programs"
        if by_model:
            merged.claims.append(model_placement_claim(AGENT, candidate.name))
        kept[group].append(Kept(candidate, merged, by_model))
    for group in kept.values():
        group.sort(
            key=lambda k: (
                _FIT_ORDER[k.program.profile_fit],
                -stage_rank(k.candidate.stage),
                k.by_model,
            )
        )
    claims = [c for group in kept.values() for k in group for c in k.program.claims]
    number_claims(claims, f"a5-{s['inp'].card.id}")

    matching = {"dropped": dropped, "unmatched": [p.asset for (_, p) in unmatched]}
    if matching["unmatched"]:
        log.warning("%s: model replies matching no program: %s", AGENT, matching["unmatched"])
    return {"kept": kept, "matching": matching}


async def score(s: A5State) -> A5State:
    """Crowding and catalysts, then the landscape."""
    ctx, kept = s["ctx"], s["kept"]
    model_placed = [k.candidate.stage for k in kept["programs"] if k.by_model and k.candidate.stage]
    crowding = await _crowding(ctx, s["target"], s["candidates"], s["params"], model_placed)
    every = [*kept["programs"], *kept["orthogonal"]]
    landscape = Landscape(
        hypothesis_id=s["inp"].card.id,
        programs=[k.program for k in kept["programs"]],
        orthogonal=[k.program for k in kept["orthogonal"]],
        crowding_score=crowding["score"],
        catalysts=catalysts(
            {k.candidate.name: k.candidate.trials for k in every}, ctx.config.as_of
        ),
    )
    return {"crowding": crowding, "landscape": landscape}


STEPS = (
    ("gather", gather),
    ("shortlist", shortlist),
    ("establish", establish),
    ("judge", judge),
    ("merge", merge),
    ("score", score),
)


def inputs(inp: A5Input, ctx: RunContext, params: CrowdingParams = DEFAULT_CROWDING) -> A5State:
    return {"inp": inp, "ctx": ctx, "params": params}


def graph():
    """Agent 5 as a LangGraph graph, for auditing and display (needs the `graph` extra)."""
    return build_graph(STEPS, A5State)


async def run(
    inp: A5Input, ctx: RunContext, params: CrowdingParams = DEFAULT_CROWDING
) -> Landscape:
    return (await run_steps(STEPS, inputs(inp, ctx, params)))["landscape"]
