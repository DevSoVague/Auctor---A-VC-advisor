"""Agent 4: inactive-in-disease assets on the hypothesis mechanism, in two ranked lists.

- repurposing: approved, or in active development, for another indication, with
  no active program in the disease.
- shelved: never approved, and development stopped or stalled: in the disease if
  it was ever there, else wherever it ran.

Assets active in the disease belong to agent 5 and are left out. Code finds the
assets and fixes their stage, owner, last activity and why each is inactive. One
model call judges relevance (an asset it leaves out is dropped), main limitation
and profile fit, and whether assets code cannot place act on the mechanism (it
reads synonyms; those rank at reduced weight). The facts are re-imposed after
the call, as in agent 5.
Patents (PRD: top mechanisms only) plug into `gather` once retrieval serves them.
"""

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal, TypedDict

from agents.competitor.common import (
    PLACEMENT_WEIGHTS,
    STAGE_LABELS,
    UNKNOWN,
    Candidate,
    customize_messages as _customize,
    Mechanism,
    Placement,
    collect_assets,
    drug_index,
    evidence,
    evidence_claim,
    judge_messages,
    last_update,
    lead_trial,
    match_replies,
    model_placement_claim,
    number_claims,
    placement_weights,
    shown,
    shown_claims,
    stage_rank,
    trial_stage,
)
from agents.competitor.landscape import (
    STOPPED,
    deprioritization,
    stop_category,
    trial_status,
)
from agents.competitor.steps import build_graph, run_steps
from agents.analysis.schemas import A4Input, Asset, Claim, InactiveAssets
from agents.competitor.context import CompetitorCtx as RunContext
from agents.competitor.records import Record

AGENT = "a4_inactive"
MAX_BRIEFS = 20  # assets per list sent to the model, most advanced first
MAX_LISTED = 10  # assets per list returned
SEARCH_K = 100  # registry and approval hits per asset when looking outside the disease
log = logging.getLogger(__name__)

Group = Literal["repurposing", "shelved"]


def inactive_reason(trials: list[Record], as_of: date) -> str | None:
    """Why development looks stopped, or None if it looks active.

    Stopped for business reasons or stale (agent 5's deprioritization rule), or
    every trial stopped, for whatever reason.
    """
    flagged, reason = deprioritization(trials, as_of)
    if flagged:
        return reason
    stopped = [t for t in trials if trial_status(t) in STOPPED]
    if trials and len(stopped) == len(trials):
        last = max(stopped, key=lambda t: last_update(t, as_of))
        why = last.fields.get("why_stopped") or "no reason given"
        return f"{last.id} {trial_status(last).lower()} ({stop_category(why)}): {why}"
    return None


def _approved(candidate: Candidate) -> bool:
    """Approved anywhere: an approval record, or ChEMBL's max phase 4 (sent as 4, 4.0 or "4.0")."""
    try:
        max_phase = float(candidate.drug.fields.get("max_phase") or 0)
    except (TypeError, ValueError):
        max_phase = 0
    return bool(candidate.approvals) or max_phase >= 4


def in_disease(record: Record, disease: str, disease_ids: set[str]) -> bool:
    """Returned by the disease query, or naming the disease in its conditions or indication.

    The text check catches disease records a capped or paged query did not return,
    so an active program in the disease is never listed as inactive.
    """
    if record.id in disease_ids:
        return True
    where = record.fields.get("conditions") or record.fields.get("indication") or []
    text = " ".join(map(str, where)) if isinstance(where, list) else str(where)
    return re.search(rf"\b{re.escape(disease)}\b", text, re.IGNORECASE) is not None


@dataclass
class Status:
    """Which list an asset belongs on, why, and the trials that show it."""

    candidate: Candidate
    group: Group
    reason: str | None
    trials: list[Record]


def classify(
    candidate: Candidate, disease: str, disease_ids: set[str], as_of: date
) -> Status | None:
    """The asset's list, or None when it is active in the disease (agent 5) or has no record."""
    if any(in_disease(a, disease, disease_ids) for a in candidate.approvals):
        return None
    here = [t for t in candidate.trials if in_disease(t, disease, disease_ids)]
    disease_reason = inactive_reason(here, as_of) if here else None
    if here and not disease_reason:
        return None
    trials = here or candidate.trials
    if _approved(candidate):
        return Status(candidate, "repurposing", disease_reason, trials)
    if reason := disease_reason or inactive_reason(trials, as_of):
        return Status(candidate, "shelved", reason, trials)
    return Status(candidate, "repurposing", None, trials) if trials else None


@dataclass
class Fact:
    """An asset as code knows it, with the records the model may cite for it."""

    group: Group
    asset: Asset
    reason: str | None
    records: list[Record]
    rank: int
    placed_by: str  # "code" (gold sources) or "model" (code could not place it)
    candidate_keys: list[str]  # names and IDs the model may use for it


def _claim(text: str, source_ids: list[str]) -> Claim:
    return evidence_claim(AGENT, text, source_ids)


def establish_fact(status: Status, as_of: date, placed_by: str) -> Fact:
    candidate, name = status.candidate, status.candidate.name
    lead = lead_trial(status.trials)
    if _approved(candidate):
        stage = "approved"
        approval = max(candidate.approvals, key=lambda a: a.source_date, default=None)
        source = approval or candidate.drug
        where = approval.fields.get("indication") if approval else None
        where = where or ", ".join(candidate.drug.fields.get("indications", []))
        label = STAGE_LABELS[stage]
    else:
        stage, source = trial_stage(lead), lead
        where = ", ".join(lead.fields.get("conditions", []))
        label = STAGE_LABELS.get(stage or "", "Phase not reported")
        if status.group == "shelved":
            state = trial_status(lead).lower() if trial_status(lead) in STOPPED else "stalled"
            label = f"{label}, {state}"
    stage_text = f"{label} ({where})" if where else label
    owner = source.fields.get("sponsor") or UNKNOWN

    dates = [last_update(t, as_of) for t in candidate.trials]
    dates += [a.source_date for a in candidate.approvals]
    claims = [_claim(f"Latest stage of {name} ({owner}): {stage_text}.", [source.id])]
    if status.reason:
        trial_ids = [t.id for t in status.trials]
        claims.append(_claim(f"{name} looks inactive: {status.reason}.", trial_ids))
    asset = Asset(
        name=name,
        entity_id=candidate.entity_id,
        owner=owner,
        stage=stage_text,
        last_activity=max(dates, default=None),
        limitations="",
        profile_fit=0,
        claims=claims,
    )
    recent = sorted([*candidate.trials, *candidate.approvals], key=lambda r: r.source_date)
    records = shown([source, lead, candidate.drug, *reversed(recent)])
    rank = stage_rank(stage)
    return Fact(status.group, asset, status.reason, records, rank, placed_by, candidate.keys())


def _recency(asset: Asset) -> int:
    return (asset.last_activity or date.min).toordinal()


def _brief(fact: Fact) -> dict:
    facts = fact.asset.model_dump(mode="json", exclude={"limitations", "profile_fit", "claims"})
    return {
        "group": fact.group,
        **facts,
        "why_inactive": fact.reason,
        "placed_by": fact.placed_by,
        "evidence": evidence(fact.records),
    }


class A4State(TypedDict, total=False):
    """What the steps share. Inputs first, then each step's output in order."""

    inp: A4Input
    ctx: RunContext
    weights: Mapping[str, float]
    candidates: list[Candidate]
    placements: dict[str, Placement]
    disease_ids: set[str]
    statuses: list[Status]
    facts: list[Fact]
    cut: list[str]
    system: str | None  # a system prompt edited on a node (our convention); None = prompts/a4_inactive.md
    feedback: str | None  # reviewer / red-team comment, appended to the case
    messages: list[dict] | None
    draft: InactiveAssets
    matching: dict[str, list[str]]
    inactive: InactiveAssets


async def gather(s: A4State) -> A4State:
    """Drugs that may act on the mechanism, with their trials and approvals anywhere."""
    retrieval, disease, card = s["ctx"].retrieval, s["ctx"].config.disease, s["inp"].card
    trials, approvals, drugs = await asyncio.gather(
        retrieval.trials(condition=disease),
        retrieval.approvals(indication=disease),
        drug_index(s["ctx"], card.mechanism),
    )
    target = Mechanism.of(card.mechanism, card.target_ids)
    placements = {d.id: target.place(Candidate.of(d)) for d in drugs}
    kept = [d for d in drugs if placements[d.id] != "off"]

    def names(drug: Record) -> str:
        return " ".join([drug.fields.get("name", ""), *drug.fields.get("synonyms", [])])

    searches = await asyncio.gather(
        *(retrieval.search(names(d), ["ctgov", "fda"], SEARCH_K) for d in kept)
    )
    records = [r for hits in searches for r in hits]
    candidates = collect_assets(
        [*trials, *(r for r in records if r.source == "ctgov")],
        [*approvals, *(r for r in records if r.source == "fda")],
        kept,
    )
    candidates = [c for c in candidates if c.drug]  # interventions that are not these drugs
    known = {c.entity_id for c in candidates}
    candidates += [Candidate.of(d) for d in kept if d.id not in known]
    return {
        "candidates": candidates,
        "placements": {c.entity_id: placements[c.entity_id] for c in candidates},
        "disease_ids": {r.id for r in [*trials, *approvals]},
    }


async def triage(s: A4State) -> A4State:
    """Each asset onto its list, or out when it is active in the disease."""
    config = s["ctx"].config
    found = (classify(c, config.disease, s["disease_ids"], config.as_of) for c in s["candidates"])
    return {"statuses": [status for status in found if status]}


async def establish(s: A4State) -> A4State:
    """Each asset's facts; per list, MAX_BRIEFS go on: code-placed first, then by stage, recency.

    Assets past the cap are named under `cut`, so the audit trail shows them.
    """
    as_of = s["ctx"].config.as_of
    facts = [
        establish_fact(
            status,
            as_of,
            "code" if s["placements"][status.candidate.entity_id] == "on" else "model",
        )
        for status in s["statuses"]
    ]
    facts.sort(key=lambda f: (f.placed_by == "code", f.rank, _recency(f.asset)), reverse=True)
    groups = {g: [f for f in facts if f.group == g] for g in ("repurposing", "shelved")}
    cut = [f.asset.name for group in groups.values() for f in group[MAX_BRIEFS:]]
    if cut:
        log.warning("%s: %d assets over the cap, not judged: %s", AGENT, len(cut), cut)
    top = [f for group in groups.values() for f in group[:MAX_BRIEFS]]
    return {"facts": top, "cut": cut}


async def prompt(s: A4State) -> A4State:
    """The messages for the one model call: fixed prompt, then the case and every asset's brief.

    None when there is nothing to judge (no model call is made).
    """
    if not s["facts"]:
        return {"messages": None}
    briefs = [_brief(f) for f in s["facts"]]
    return {"messages": _customize(s, judge_messages(AGENT, s["ctx"], s["inp"], assets=briefs))}


async def llm(s: A4State) -> A4State:
    """The one model call (validated against InactiveAssets; one retry on a schema error)."""
    if s["messages"] is None:
        return {"draft": InactiveAssets(hypothesis_id=s["inp"].card.id, repurposing=[], shelved=[])}
    return {"draft": await s["ctx"].llm(agent=AGENT, messages=s["messages"], schema=InactiveAssets)}


async def judge(s: A4State) -> A4State:
    """prompt + llm in one call (kept for callers that used the old single step)."""
    s = {**s, **(await prompt(s))}
    return await llm(s)


async def merge(s: A4State) -> A4State:
    """Pair replies with assets, re-impose code's facts, and rank.

    Ranked by profile fit times the placement weight (a model-placed asset counts
    less), then stage, then recency. Assets the model left out, and replies that
    name no asset, are recorded under `matching`, never dropped silently.
    """
    draft = s["draft"]
    replies = [(a, [a.entity_id, a.name]) for a in [*draft.repurposing, *draft.shelved]]
    keys = {f.asset.entity_id: f.candidate_keys for f in s["facts"]}
    matched, unmatched = match_replies(keys, replies)

    lists: dict[Group, list[tuple[Fact, Asset]]] = {"repurposing": [], "shelved": []}
    dropped = []
    for fact in s["facts"]:
        if (verdict := matched.get(fact.asset.entity_id)) is None:
            dropped.append(fact.asset.name)
            continue
        claims = [
            *fact.asset.claims,
            *shown_claims(AGENT, verdict.claims, {r.id for r in fact.records}),
        ]
        if fact.placed_by == "model":
            claims.append(model_placement_claim(AGENT, fact.asset.name))
        asset = fact.asset.model_copy(
            update={
                "limitations": verdict.limitations or fact.reason or "",
                "profile_fit": verdict.profile_fit,
                "claims": claims,
            }
        )
        lists[fact.group].append((fact, asset))

    def closeness(pair: tuple[Fact, Asset]) -> tuple:
        fact, asset = pair
        return asset.profile_fit * s["weights"][fact.placed_by], fact.rank, _recency(asset)

    ranked = {
        group: [asset for _, asset in sorted(pairs, key=closeness, reverse=True)][:MAX_LISTED]
        for group, pairs in lists.items()
    }
    claims = [c for assets in ranked.values() for a in assets for c in a.claims]
    number_claims(claims, f"a4-{s['inp'].card.id}")

    matching = {"dropped": dropped, "unmatched": [a.name for a in unmatched]}
    if matching["unmatched"]:
        log.warning("%s: model replies matching no asset: %s", AGENT, matching["unmatched"])
    inactive = InactiveAssets(hypothesis_id=s["inp"].card.id, **ranked)
    return {"inactive": inactive, "matching": matching}


STEPS = (
    ("gather", gather),
    ("triage", triage),
    ("establish", establish),
    ("prompt", prompt),
    ("llm", llm),
    ("merge", merge),
)


def inputs(
    inp: A4Input, ctx: RunContext, weights: Mapping[str, float] = PLACEMENT_WEIGHTS
) -> A4State:
    return {"inp": inp, "ctx": ctx, "weights": placement_weights(weights)}


def graph():
    """Agent 4 as a LangGraph graph, for auditing and display."""
    return build_graph(STEPS, A4State)


async def run(
    inp: A4Input, ctx: RunContext, weights: Mapping[str, float] = PLACEMENT_WEIGHTS
) -> InactiveAssets:
    """weights: how much a code-placed and a model-placed asset count in the ranking."""
    return (await run_steps(STEPS, inputs(inp, ctx, weights)))["inactive"]
