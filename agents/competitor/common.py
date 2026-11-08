"""Helpers shared by agents 4 and 5: dates, stages, drug names, assets, owner type, claims."""

import asyncio
import calendar
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeVar

from agents.analysis.schemas import A4Input, A5Input, Claim
from agents.competitor.context import CompetitorCtx as RunContext
from agents.competitor.records import Record

T = TypeVar("T")
OwnerType = Literal["startup", "mid_size", "big_pharma", "academic"]
UNKNOWN = "Unknown"
# How much an asset counts, by who placed it on the hypothesis mechanism: code from gold
# sources (ChEMBL targets and mechanisms), or the model's judgment. PROPOSAL, tunable.
PLACEMENT_WEIGHTS = {"code": 1.0, "model": 0.5}
DRUG_SEARCH_K = 200  # ChEMBL hits from the hybrid search on the mechanism text
MAX_EVIDENCE = 4  # records shown to the model per asset, most telling first
EVIDENCE_CHARS = 800  # of each record's text; ClinicalTrials.gov puts the key facts first
_FIELD_CHARS = 200  # record fields longer than this (label sections, summaries) are not shown
PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
_COMPARATORS = ("placebo", "standard of care", "usual care", "sham", "vehicle")
# ClinicalTrials.gov intervention types that are not drug programs.
_NOT_DRUGS = {"DEVICE", "PROCEDURE", "BEHAVIORAL", "DIETARY_SUPPLEMENT", "RADIATION",
              "DIAGNOSTIC_TEST"}  # fmt: skip
# Generic words: sharing one ('inhibitor', 'alpha', 'kinase') doesn't make two mechanisms alike.
_MECHANISM_WORDS = {
    "inhibitor", "inhibition", "antagonist", "antagonism", "agonist", "agonism", "blocker",
    "blockade", "modulator", "modulation", "antibody", "monoclonal", "receptor", "selective",
    "dual", "and", "of", "subunit", "type", "family", "member", "protein", "factor", "kinase",
    "channel", "ligand", "activator", "positive", "negative", "allosteric", "partial", "inverse",
    "alpha", "beta", "gamma", "delta",
}  # fmt: skip

# Development stages, earliest first. Program.stage and Asset.stage show the label.
STAGES = ("preclinical", "phase1", "phase2", "phase3", "filed", "approved")
STAGE_LABELS = {
    "preclinical": "Preclinical",
    "phase1": "Phase 1",
    "phase2": "Phase 2",
    "phase3": "Phase 3",
    "filed": "Filed",
    "approved": "Approved",
}

# ClinicalTrials.gov v2 phase values. Phase 4 trials run after approval.
_CTGOV_PHASES = {
    "EARLY_PHASE1": "phase1",
    "PHASE1": "phase1",
    "PHASE2": "phase2",
    "PHASE3": "phase3",
    "PHASE4": "approved",
}

# Big pharma, matched as substrings of the normalized sponsor name.
BIG_PHARMA = (
    "pfizer", "johnsonjohnson", "janssen", "roche", "genentech", "merck", "abbvie",
    "astrazeneca", "novartis", "bristolmyers", "sanofi", "glaxosmithkline", "gsk", "lilly",
    "novonordisk", "amgen", "takeda", "boehringer", "bayer", "gilead",
)  # fmt: skip

# ClinicalTrials.gov lead-sponsor classes that are not companies.
_NON_INDUSTRY = {"NIH", "FED", "OTHER_GOV", "NETWORK", "OTHER"}


def parse_date(value: Any) -> date | None:
    """An ISO date; ClinicalTrials.gov's '2021-06' or '2021' as the last day it could mean.

    Same rule as person 1's ingest (frontier.ingest.ctgov.to_date); else None.
    """
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}", value):
            return date(int(value), 12, 31)
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            return date(year, month, calendar.monthrange(year, month)[1])
        return date.fromisoformat(value)
    except ValueError:
        return None


def months_before(day: date, months: int) -> date:
    """The date `months` months before `day` (day of month capped at 28)."""
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(year, month + 1, min(day.day, 28))


def last_update(record: Record, as_of: date) -> date:
    """When the record last changed, never on or after as_of.

    Falls back to source_date when the record has no last_update_posted or reports
    one from the future, which would leak post-cutoff information into a backtest.
    """
    updated = parse_date(record.fields.get("last_update_posted"))
    return updated if updated and updated < as_of else record.source_date


def trial_stage(record: Record) -> str | None:
    """A trial's stage from its phase; a combined phase (1/2) counts as the earlier one."""
    phases = re.findall(r"EARLY_PHASE1|PHASE[1-4]", str(record.fields.get("phase", "")).upper())
    return min((_CTGOV_PHASES[p] for p in phases), key=STAGES.index, default=None)


def highest_stage(stages: Iterable[str | None]) -> str | None:
    return max((s for s in stages if s), key=STAGES.index, default=None)


def stage_rank(stage: str | None) -> int:
    return STAGES.index(stage) if stage else -1


def lead_trial(trials: Iterable[Record]) -> Record | None:
    """The most advanced trial, the latest registered among equals."""
    return max(trials, key=lambda t: (stage_rank(trial_stage(t)), t.source_date), default=None)


def normalize_name(name: str) -> str:
    """'FIX-101', 'fix 101' and 'FIX101' all become 'fix101'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


class AliasIndex:
    """Maps any known name of a drug (preferred name or synonym) to one entity ID.

    Built from ChEMBL records, so one asset gets one row however a trial spells it.
    A name ChEMBL doesn't know resolves to 'name:<normalized name>'.
    """

    def __init__(self, drugs: Iterable[Record]):
        self._ids: dict[str, str] = {}
        for drug in drugs:
            for name in [drug.fields.get("name"), *drug.fields.get("synonyms", [])]:
                if name:
                    self._ids.setdefault(normalize_name(name), drug.id)

    def resolve(self, name: str) -> str:
        key = normalize_name(name)
        return self._ids.get(key, f"name:{key}")


def mechanism_terms(text: str) -> list[str]:
    """Distinctive words of a mechanism as written, for search: 'Anti-IL-33' -> ['il-33'].

    A subtype number stays with its word: 'phosphodiesterase type 4' -> 'phosphodiesterase-4'.
    """
    text = re.sub(r"\binterleukin[- ]?", "il-", re.sub(r"\banti-", "", text.lower()))
    text = re.sub(r"\b([a-z]+)[\s-]+(?:(?:sub)?type\s+)?(\d+[a-z]?)\b", r"\1-\2", text)
    words = re.split(r"[\s/,;()]+", text)
    return list(dict.fromkeys(w for w in words if _distinctive(normalize_name(w))))


def _distinctive(word: str) -> bool:
    """Not generic, nor a bare number or letter (the '4' in 'phosphodiesterase 4')."""
    return len(word) > 1 and not word.isdigit() and word not in _MECHANISM_WORDS


def mechanism_tokens(text: str) -> frozenset[str]:
    """Distinctive words of a mechanism, normalized: 'Anti-IL-33 antagonist' -> {'il33'}."""
    return frozenset(map(normalize_name, mechanism_terms(text)))


def same_mechanism(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether two mechanisms' words name the same mechanism.

    A shared subtype ('pde4', 'il33', 'm3') is enough, as is one side's words all
    being shared ('glucocorticoid'); a shared family word alone is not, so
    'Beta-1 adrenergic' and 'Beta-2 adrenergic' differ.
    """
    shared = a & b
    return bool(shared) and (any(c.isdigit() for w in shared for c in w) or shared in (a, b))


def interventions(trial: Record) -> list[list[str]]:
    """The names of each drug a trial tests, preferred name first; devices and comparators out.

    Reads ClinicalTrials.gov's {type, name, other_names} (person 1's ingest) or a bare name.
    """
    found = []
    for item in trial.fields.get("interventions", []):
        if isinstance(item, str):
            item = {"name": item}
        name = item.get("name") or ""
        if not name or item.get("type") in _NOT_DRUGS:
            continue
        if not any(c in name.lower() for c in _COMPARATORS):
            found.append([name, *(item.get("other_names") or [])])
    return found


@dataclass
class Candidate:
    """One asset and the records that place it: its ChEMBL entry, trials and approvals."""

    entity_id: str
    name: str
    drug: Record | None
    trials: list[Record] = field(default_factory=list)
    approvals: list[Record] = field(default_factory=list)

    @classmethod
    def of(cls, drug: Record) -> "Candidate":
        """An asset known only from its ChEMBL entry, so far."""
        return cls(drug.id, drug.fields.get("name", drug.id), drug)

    @property
    def mechanism_text(self) -> str:
        """ChEMBL's mechanism as written; "" when it has none, so missing ones never match."""
        return (self.drug and self.drug.fields.get("mechanism")) or ""

    @property
    def mechanism(self) -> str:
        return self.mechanism_text or UNKNOWN

    @property
    def modality(self) -> str:
        return (self.drug and self.drug.fields.get("molecule_type")) or UNKNOWN

    @property
    def targets(self) -> frozenset[str]:
        return frozenset(self.drug.fields.get("targets", [])) if self.drug else frozenset()

    @property
    def stage(self) -> str | None:
        return "approved" if self.approvals else highest_stage(map(trial_stage, self.trials))

    @property
    def lead_trial(self) -> Record | None:
        return lead_trial(self.trials)

    @property
    def stage_source(self) -> Record | None:
        """The record that shows the stage: the latest approval, else the lead trial."""
        return max(self.approvals, key=lambda a: a.source_date, default=None) or self.lead_trial

    @property
    def latest(self) -> Record:
        return max([*self.trials, *self.approvals], key=lambda r: r.source_date)

    def keys(self) -> list[str]:
        """Every name and ID the model might use for this asset."""
        names = (
            [self.drug.fields.get("name"), *self.drug.fields.get("synonyms", [])]
            if self.drug
            else []
        )
        return [self.entity_id, self.name, *filter(None, names)]


# Where an asset sits relative to the hypothesis mechanism:
# "on"   code places it on the mechanism (shared target, or same_mechanism by ChEMBL's words);
# "off"  code rules it out (both sides list targets, none shared);
# "open" code cannot tell (no ChEMBL entry, or targets missing on a side), so the model decides.
Placement = Literal["on", "off", "open"]


@dataclass(frozen=True)
class Mechanism:
    tokens: frozenset[str]
    targets: frozenset[str]
    query: str = field(default="", compare=False)  # its words as written, for text search

    @classmethod
    def of(cls, text: str, targets: Iterable[str]) -> "Mechanism":
        return cls(mechanism_tokens(text), frozenset(targets), " ".join(mechanism_terms(text)))

    def covers(self, candidate: Candidate) -> bool:
        if candidate.drug is None:
            return False
        return bool(self.targets & candidate.targets) or same_mechanism(
            self.tokens, mechanism_tokens(candidate.mechanism_text)
        )

    def place(self, candidate: Candidate) -> Placement:
        if self.covers(candidate):
            return "on"
        if candidate.drug and self.targets and candidate.targets:
            return "off"
        return "open"


def collect_assets(
    trials: Iterable[Record], approvals: Iterable[Record], drugs: Iterable[Record]
) -> list[Candidate]:
    """Group trial interventions and approvals into assets, one per entity ID."""
    drugs = list(drugs)
    index = AliasIndex(drugs)
    by_id = {d.id: d for d in drugs}
    found: dict[str, Candidate] = {}

    def asset(name: str, *aliases: str) -> Candidate:
        """The asset for a name: the first of its names ChEMBL knows, else the name itself."""
        known = (e for e in map(index.resolve, (name, *aliases)) if e in by_id)
        entity = next(known, index.resolve(name))
        if entity not in found:
            drug = by_id.get(entity)
            found[entity] = Candidate(entity, drug.fields.get("name", name) if drug else name, drug)
        return found[entity]

    for trial in trials:
        for names in interventions(trial):
            candidate = asset(*names)
            if trial not in candidate.trials:
                candidate.trials.append(trial)
    for approval in approvals:
        if name := approval.fields.get("drug"):
            candidate = asset(name)
            if approval not in candidate.approvals:
                candidate.approvals.append(approval)
    return list(found.values())


async def drug_index(ctx: RunContext, mechanism: str) -> list[Record]:
    """ChEMBL drugs for the disease or the mechanism, one per ID.

    By indication, by each distinctive mechanism word, and by the retrieval API's
    hybrid keyword and vector search on the mechanism text, which finds drugs
    described with synonyms ('phosphodiesterase-4' for 'PDE4').
    """
    retrieval = ctx.retrieval
    found = await asyncio.gather(
        retrieval.drugs(indication=ctx.config.disease),
        *(retrieval.drugs(mechanism=term) for term in mechanism_terms(mechanism)),
        retrieval.search(mechanism, ["chembl"], DRUG_SEARCH_K),
    )
    return list({d.id: d for hits in found for d in hits}.values())


def match_replies(
    keys_by_ref: Mapping[str, Iterable[str | None]],
    replies: Iterable[tuple[T, Iterable[str | None]]],
) -> tuple[dict[str, T], list[T]]:
    """Pair the model's replies with code's candidates, never silently.

    Each reply tries its keys in order (e.g. entity ID, then name); a key matches
    when it names exactly one candidate not yet taken. Names are compared
    normalized, so 'FIX-101' finds fixtafilast through its ChEMBL synonym.
    Returns {ref: reply} and the replies that matched no candidate.
    """
    owners: defaultdict[str, set[str]] = defaultdict(set)
    for ref, keys in keys_by_ref.items():
        for key in filter(None, keys):
            owners[normalize_name(key)].add(ref)
    matched: dict[str, T] = {}
    unmatched: list[T] = []
    for reply, keys in replies:
        for key in filter(None, keys):
            free = owners.get(normalize_name(key), set()) - matched.keys()
            if len(free) == 1:
                matched[free.pop()] = reply
                break
        else:
            unmatched.append(reply)
    return matched, unmatched


def placement_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Checked placement weights: one for "code" and one for "model", each 0 to 1."""
    if weights.keys() != PLACEMENT_WEIGHTS.keys() or not all(0 <= w <= 1 for w in weights.values()):
        raise ValueError(f"placement weights need {sorted(PLACEMENT_WEIGHTS)}, each 0 to 1")
    return dict(weights)


def model_placement_claim(agent: str, name: str) -> Claim:
    """Marks an asset the model, not code, placed on the hypothesis mechanism."""
    return Claim(
        id="",
        agent=agent,
        text=(
            f"Reasoning: {name} was placed on the hypothesis mechanism by model judgment "
            "(no shared target or ChEMBL mechanism word), so it counts at reduced weight."
        ),
        kind="reasoning",
        confidence=0.5,
        source_ids=[],
    )


def evidence_claim(agent: str, text: str, source_ids: list[str]) -> Claim:
    """A fact code established from records, numbered later by number_claims."""
    return Claim(
        id="", agent=agent, text=text, kind="evidence", confidence=0.9, source_ids=source_ids
    )


def shown_claims(agent: str, claims: Iterable[Claim], shown: set[str]) -> list[Claim]:
    """The model's claims that cite only records it was shown, attributed to the agent."""
    return [c.model_copy(update={"agent": agent}) for c in claims if set(c.source_ids) <= shown]


def number_claims(claims: Iterable[Claim], prefix: str) -> None:
    """Give claims run-unique IDs (the claims table key): prefix-c1, prefix-c2, ..."""
    for number, claim in enumerate(claims, start=1):
        claim.id = f"{prefix}-c{number}"


def shown(records: Iterable[Record | None]) -> list[Record]:
    """The first MAX_EVIDENCE distinct records, in the order given (most telling first)."""
    return list({r.id: r for r in records if r}.values())[:MAX_EVIDENCE]


def evidence(records: Iterable[Record]) -> list[dict]:
    """Records as the model sees them, trimmed to keep the prompt bounded; cited by id."""

    def short(value: Any) -> bool:
        return len(json.dumps(value, default=str)) <= _FIELD_CHARS

    return [
        {
            "id": r.id,
            "date": r.source_date.isoformat(),
            "title": r.title,
            "text": (r.body or "")[:EVIDENCE_CHARS],
            "fields": {k: v for k, v in r.fields.items() if short(v)},
        }
        for r in records
    ]


def judge_messages(
    agent: str, ctx: RunContext, inp: A4Input | A5Input, **items: list[dict]
) -> list[dict]:
    """The agent's fixed prompt, then the case (disease, cutoff, hypothesis, profile) and items."""
    payload = {
        "disease": ctx.config.disease,
        "as_of": ctx.config.as_of.isoformat(),
        "hypothesis": inp.card.model_dump(mode="json", exclude={"claims"}),
        "product_profile": inp.profile.model_dump(mode="json", exclude={"claims"}),
        **items,
    }
    return [
        {"role": "system", "content": (PROMPTS / f"{agent}.md").read_text()},
        {"role": "user", "content": json.dumps(payload, indent=1, default=str)},
    ]


def owner_type(
    sponsor: str, sponsor_class: str | None, filings: Iterable[Record] = ()
) -> OwnerType:
    """Classify a sponsor. Pending Adam's sign-off on the rules:

    big pharma by name; non-industry sponsor classes are academic; a company with
    a 10-K on file is public (mid_size); one without is private (startup).
    """
    key = normalize_name(sponsor)
    if any(name in key for name in BIG_PHARMA):
        return "big_pharma"
    if (sponsor_class or "").upper() in _NON_INDUSTRY:
        return "academic"
    return "mid_size" if any(f.fields.get("form") == "10-K" for f in filings) else "startup"


def customize_messages(s, messages: list[dict]) -> list[dict]:
    """Our conventions: a system prompt edited on a node wins over the file; reviewer feedback goes last."""
    if s.get("system"):
        messages[0] = {"role": "system", "content": s["system"]}
    if s.get("feedback"):
        messages[-1] = {"role": "user", "content": messages[-1]["content"]
                        + "\n\nREVIEWER FEEDBACK (address this explicitly):\n" + s["feedback"]}
    return messages

