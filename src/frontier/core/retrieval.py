"""Retrieval client: the only way agents read evidence (owner: person 2).

Every client is bound to one as_of date when it is created, and never returns a
record whose source_date is on or after it. Agents get one from ctx.retrieval and
never pass a date themselves, so a backtest cannot see the future.

StubRetrievalClient serves tests/fixtures/retrieval/records.json. The HTTP
client for the real retrieval API (PRD build order step 5) will implement the
same methods.
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Source = Literal["pubmed", "ctgov", "fda", "opentargets", "chembl", "edgar", "retraction", "patent"]

REPO_ROOT = Path(__file__).resolve().parents[3]


def fixtures_dir() -> Path:
    """tests/fixtures, or FRONTIER_FIXTURES_DIR if set."""
    return Path(os.environ.get("FRONTIER_FIXTURES_DIR", REPO_ROOT / "tests" / "fixtures"))


class Record(BaseModel):
    """One evidence record, shaped like a row of the documents table."""

    id: str  # 'pmid:...', 'nct:...', 'fda:...', 'sec:...', 'pat:...'
    source: Source
    source_date: date  # the date the as-of filter uses
    title: str | None = None
    body: str | None = None
    url: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)  # source-specific extras


class RetrievalClient(Protocol):
    """One method per retrieval endpoint in the PRD (Services, Retrieval API)."""

    as_of: date

    async def search(
        self, q: str, sources: list[Source] | None = None, k: int = 10
    ) -> list[Record]: ...
    async def doc(self, id: str) -> Record | None: ...
    async def trials(
        self, condition: str, phase: str | None = None, status: str | None = None
    ) -> list[Record]: ...
    async def targets(self, disease_id: str) -> list[Record]: ...
    async def drugs(
        self, mechanism: str | None = None, indication: str | None = None
    ) -> list[Record]: ...
    async def approvals(self, indication: str) -> list[Record]: ...
    async def filings(self, company: str, form: str | None = None) -> list[Record]: ...
    async def retractions(self, doi: str) -> list[Record]: ...


def _contains(haystack: Any, needle: str) -> bool:
    if isinstance(haystack, list):
        return any(_contains(item, needle) for item in haystack)
    return needle.lower() in str(haystack or "").lower()


class StubRetrievalClient:
    """Serves fixture records, dropping anything dated on or after as_of."""

    def __init__(self, as_of: date, records: list[Record] | None = None):
        self.as_of = as_of
        if records is None:
            path = fixtures_dir() / "retrieval" / "records.json"
            records = [Record.model_validate(r) for r in json.loads(path.read_text())]
        self._records = [r for r in records if r.source_date < as_of]

    def _from(self, source: Source) -> list[Record]:
        return [r for r in self._records if r.source == source]

    async def search(
        self, q: str, sources: list[Source] | None = None, k: int = 10
    ) -> list[Record]:
        words = q.lower().split()
        hits = [
            r
            for r in self._records
            if (sources is None or r.source in sources)
            and any(_contains(f"{r.title} {r.body}", w) for w in words)
        ]
        return hits[:k]

    async def doc(self, id: str) -> Record | None:
        return next((r for r in self._records if r.id == id), None)

    async def trials(
        self, condition: str, phase: str | None = None, status: str | None = None
    ) -> list[Record]:
        return [
            r
            for r in self._from("ctgov")
            if _contains(r.fields.get("conditions"), condition)
            and (phase is None or r.fields.get("phase") == phase)
            and (status is None or r.fields.get("status") == status)
        ]

    async def targets(self, disease_id: str) -> list[Record]:
        return [r for r in self._from("opentargets") if r.fields.get("disease_id") == disease_id]

    async def drugs(
        self, mechanism: str | None = None, indication: str | None = None
    ) -> list[Record]:
        return [
            r
            for r in self._from("chembl")
            if (mechanism is None or _contains(r.fields.get("mechanism"), mechanism))
            and (indication is None or _contains(r.fields.get("indications"), indication))
        ]

    async def approvals(self, indication: str) -> list[Record]:
        return [r for r in self._from("fda") if _contains(r.fields.get("indication"), indication)]

    async def filings(self, company: str, form: str | None = None) -> list[Record]:
        return [
            r
            for r in self._from("edgar")
            if _contains(r.fields.get("company"), company)
            and (form is None or r.fields.get("form") == form)
        ]

    async def retractions(self, doi: str) -> list[Record]:
        return [r for r in self._from("retraction") if r.fields.get("doi") == doi]
