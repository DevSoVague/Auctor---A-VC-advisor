"""What agents 4 and 5 get as `ctx`, built from our own context (agents.analysis.context.RunContext).

Our context's retrieval returns dicts and its `llm` is a callable. A4/A5 want Records and
`await ctx.llm(agent=, messages=, schema=)`. `CompetitorCtx` is that thin view; it shares
the config and the LLM with our context, so there is still one model, one bus, one run.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from agents.competitor.records import TEAM_FIXTURES, Record, StubRetrievalClient


class SchemaError(RuntimeError):
    """The model's output failed schema validation twice."""


class RecordAdapter:
    """Our dict retrieval seen as Record retrieval (same methods, same as_of)."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.as_of = inner.as_of

    @staticmethod
    def _curated_drug(d: dict, fields: dict) -> tuple[str, dict]:
        """The evidence store's curated drugs, in the shape agents 4 and 5 read ChEMBL drugs.

        The store labels them source 'curated', which Record does not accept, so every one was
        dropped and A4/A5 found no drugs. They read: name, synonyms (brand names), mechanism
        (the pathway text), molecule_type, targets. Placement by shared target is not possible
        (the store lists none), so it falls to same_mechanism by the pathway's words.
        """
        mech = " ".join(x for x in (fields.get("pathway"), fields.get("drug_class")) if x)
        low = mech.lower()
        modality = ("antibody" if "antibod" in low else "small molecule"
                    if any(w in low for w in ("inhibitor", "antagonist", "potentiator", "agonist"))
                    else None)
        return "chembl", {**fields, "name": d.get("title") or fields.get("name"),
                          "synonyms": list(fields.get("brand_names") or []), "mechanism": mech,
                          "molecule_type": modality, "targets": list(fields.get("targets") or []),
                          "development_status": fields.get("drug_status") or fields.get("status")}

    @staticmethod
    def _rec(d: dict) -> Record | None:
        extra = {k: v for k, v in d.items()
                 if k not in ("id", "source", "source_date", "title", "body", "url")}
        fields, source = d.get("fields") or extra, d["source"]
        try:
            if source == "curated" and (fields.get("pathway") or fields.get("drug_class")):
                source, fields = RecordAdapter._curated_drug(d, fields)
            return Record(id=d["id"], source=source, source_date=str(d["source_date"])[:10],
                          title=d.get("title"), body=d.get("body"), url=d.get("url"),
                          fields=fields)
        except Exception:  # noqa: BLE001 - a row the Record model can't hold is skipped
            return None

    def _recs(self, rows) -> list[Record]:
        return [r for r in map(self._rec, rows or []) if r]

    async def search(self, q, sources=None, k=10):
        return self._recs(await self.inner.search(q, sources, k))

    async def doc(self, id):
        d = await self.inner.doc(id)
        return self._rec(d) if d else None

    async def trials(self, condition, phase=None, status=None):
        return self._recs(await self.inner.trials(condition, phase, status))

    async def targets(self, disease_id):
        return self._recs(await self.inner.targets(disease_id))

    async def drugs(self, mechanism=None, indication=None):
        return self._recs(await self.inner.drugs(mechanism, indication))

    async def approvals(self, indication):
        return self._recs(await self.inner.approvals(indication))

    async def filings(self, company, form=None):
        fn = getattr(self.inner, "filings", None)
        return self._recs(await fn(company, form)) if fn else []

    async def retractions(self, doi):
        return self._recs(await self.inner.retractions(doi))


@dataclass
class CompetitorCtx:
    config: Any  # RunConfig
    retrieval: Any  # Record-returning client bound to config.as_of
    llm_fn: Callable[..., Awaitable[BaseModel]] | None = None  # our ctx.llm
    backend: Any = None  # tests: an object with async complete(...) -> raw JSON text
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    async def llm(self, *, agent: str, messages: list[dict], schema: type[BaseModel]):
        if self.llm_fn is not None:
            return await self.llm_fn(agent=agent, messages=messages, schema=schema)
        attempt = list(messages)
        for n in (1, 2):  # retry once with the error, then fail loudly (PRD)
            raw = await self.backend.complete(run_id=self.run_id, agent=agent,
                                              messages=attempt, schema=schema)
            try:
                return schema.model_validate_json(raw)
            except ValidationError as err:
                if n == 2:
                    raise SchemaError(f"{agent}: output failed {schema.__name__} twice:\n{err}") from err
                attempt = [*messages, {"role": "assistant", "content": raw},
                           {"role": "user", "content": f"Your JSON failed validation. Fix it.\n{err}"}]


def for_agents(actx, competitor_retrieval=None) -> CompetitorCtx:
    """The A4/A5 view of one of our contexts.

    `competitor_retrieval` overrides retrieval (the stub run uses fixture Records);
    otherwise our retrieval is adapted, so a live source works unchanged.
    """
    retrieval = competitor_retrieval or getattr(actx, "competitor_retrieval", None)
    if retrieval is None:
        from agents.analysis.retrieval import StubRetrieval
        from agents.competitor.records import competitor_stub

        # stub runs use Adam's fixture world; any other source is adapted as it is
        retrieval = (competitor_stub(actx.config.as_of) if isinstance(actx.retrieval, StubRetrieval)
                     else RecordAdapter(actx.retrieval))
    return CompetitorCtx(config=actx.config, retrieval=retrieval, llm_fn=actx.llm, run_id=actx.run_id)


def load_fixture(name: str) -> dict:
    return json.loads((TEAM_FIXTURES / f"{name}.json").read_text())
