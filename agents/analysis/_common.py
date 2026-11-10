"""Shared pipeline for A1, A2, A3, A6: retrieve -> reason -> post-check -> emit."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from agents.analysis.retrieval import _d
from agents.analysis.schemas import Claim

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
PROMPT_VERSION = "auctor-v1"

# Confidence caps (calibration): evidence tier -> max confidence on that card's claims.
TIER_CAP = {1: 0.9, 2: 0.75, 3: 0.6, 4: 0.4}
REASONING_CAP = 0.5  # claims with no surviving citation


# ----------------------------------------------------------------- evidence pack
class EvidencePack:
    """The only documents an agent may cite. Keyed by doc id."""

    def __init__(self, as_of: date) -> None:
        self.as_of = as_of
        self.items: dict[str, dict] = {}

    def add(self, recs: Iterable[dict] | None) -> list[dict]:
        added = []
        for r in recs or []:
            d = _d(r.get("source_date"))
            if d is None or d >= self.as_of:  # leakage guard
                continue
            self.items.setdefault(r["id"], r)
            added.append(r)
        return added

    def render(self, max_chars: int = 600) -> str:
        lines = []
        for r in sorted(self.items.values(), key=lambda r: r["source_date"]):
            body = (r.get("body") or r.get("summary") or "")[:max_chars]
            extra = {k: r[k] for k in ("phase", "status", "why_stopped", "sponsor",
                                        "intervention", "primary_endpoint",
                                        "inclusion_criteria") if r.get(k)}
            ext = f" | {json.dumps(extra)}" if extra else ""
            lines.append(f"[{r['id']} | {r['source_date'][:10]} | {r.get('title', '')}]{ext}\n{body}")
        return "\n\n".join(lines) or "(no evidence retrieved)"

    def resolve(self, doc_id: str) -> str | None:
        """Exact id, or a unique match ignoring the source prefix and case
        (models often drop prefixes: 'pm-gold2019' / 'NCT0123' for 'fx:pm-gold2019' / 'nct:NCT0123')."""
        doc_id = doc_id.strip().strip("[]")
        if doc_id in self.items:
            return doc_id
        key = doc_id.split(":", 1)[-1].lower()
        hits = [i for i in self.items if i.split(":", 1)[-1].lower() == key]
        return hits[0] if len(hits) == 1 else None

    def valid(self, doc_id: str) -> bool:
        r = self.items.get(doc_id)
        return r is not None and _d(r["source_date"]) < self.as_of

    # serialisable, so step-by-step runners (Langflow, LangGraph) can pass packs between nodes
    def to_dict(self) -> dict:
        return {"as_of": self.as_of.isoformat(), "items": self.items}

    @classmethod
    def from_dict(cls, d: dict) -> "EvidencePack":
        p = cls(date.fromisoformat(d["as_of"]))
        p.items = dict(d["items"])
        return p


# ----------------------------------------------------------------- prompts
def system_prompt(agent: str) -> str:
    return (PROMPTS / f"{agent}.md").read_text()


def build_messages(agent: str, payload: dict[str, Any], pack: EvidencePack,
                   feedback: str | None, config: Any, system: str | None = None) -> list[dict]:
    """Fixed instructions first (cache-friendly), dynamic content last.
    `system` overrides the prompt file (e.g. edited in a Langflow node)."""
    system = system or system_prompt(agent)
    user = [
        f"AS_OF_DATE: {config.as_of.isoformat()}  (you may only use evidence dated before this)",
        f"DISEASE: {config.disease} ({config.disease_id})",
        f"AUDIENCE: {config.audience} | RISK_LEVEL: {config.risk_level} | "
        f"MODALITY: {config.modality} | CONSTRAINTS: {config.constraints or 'none'}",
        "INPUT:\n" + json.dumps(payload, default=str, indent=1),
        "EVIDENCE PACK (cite only these ids):\n" + pack.render(),
    ]
    if feedback:
        user.append("REVIEWER FEEDBACK (address this explicitly):\n" + feedback)
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user)}]


# ----------------------------------------------------------------- post-check
def iter_claims(obj: Any) -> Iterable[Claim]:
    if isinstance(obj, Claim):
        yield obj
    elif isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            yield from iter_claims(getattr(obj, name))
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            yield from iter_claims(x)


def post_check(obj: BaseModel, agent: str, pack: EvidencePack, cap: float = 0.9,
               prefix: str = "") -> list[dict]:
    """Deterministic pre-filter before A7. Mutates claims in place; returns a change log.

    - stamps agent + stable claim ids
    - strips source_ids not in the pack or dated >= as_of
    - evidence claim left with no source -> downgraded to reasoning
    - caps confidence (tier cap for evidence, REASONING_CAP for reasoning)
    """
    log = []
    for i, c in enumerate(iter_claims(obj), 1):
        c.agent = agent
        c.id = f"{agent[:2]}-{prefix}{i:03d}"
        fixed = {s: pack.resolve(s) for s in c.source_ids}
        repaired = {s: r for s, r in fixed.items() if r and r != s}
        if repaired:
            c.source_ids = list(dict.fromkeys(fixed[s] or s for s in c.source_ids))
            log.append({"claim_id": c.id, "action": "repaired_ids", "ids": repaired})
        bad = [s for s in c.source_ids if not pack.valid(s)]
        if bad:
            c.source_ids = [s for s in c.source_ids if pack.valid(s)]
            log.append({"claim_id": c.id, "action": "stripped_sources", "ids": bad})
        if c.kind == "evidence" and not c.source_ids:
            c.kind = "reasoning"
            log.append({"claim_id": c.id, "action": "downgraded_to_reasoning"})
        limit = cap if c.kind == "evidence" else min(cap, REASONING_CAP)
        if c.confidence > limit:
            c.confidence = limit
    return log


def emit_claims(ctx, agent: str, obj: BaseModel, hypothesis_id: str | None = None) -> None:
    for c in iter_claims(obj):
        ctx.emit("claim_added", {"agent": agent, "hypothesis_id": hypothesis_id,
                                 "claim": c.model_dump(mode="json")})
