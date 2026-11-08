import json
from datetime import date
from pathlib import Path

import pytest

from agents.analysis.schemas import RunConfig
from agents.competitor.context import CompetitorCtx
from agents.competitor.records import Record, StubRetrievalClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "competitor"


def competitor_records() -> list[Record]:
    """The fake COPD world in tests/fixtures/competitor/records.json."""
    return [Record.model_validate(r) for r in json.loads((FIXTURES / "records.json").read_text())]


@pytest.fixture
def records() -> list[Record]:
    return competitor_records()


class CannedLLM:
    """Answers every call with one fixed JSON file and records what it was sent."""

    def __init__(self, answer: str):
        self.answer = (FIXTURES / answer).read_text()
        self.calls: list[dict] = []

    async def complete(self, *, run_id, agent, messages, schema) -> str:
        self.calls.append({"agent": agent, "messages": messages, "schema": schema})
        return self.answer


@pytest.fixture
def trial():
    """Build a fake ClinicalTrials.gov record: trial(source_date=..., status=..., phase=...)."""

    def make(id: str = "nct:FIXTURE-T1", source_date: date = date(2019, 1, 1), **fields) -> Record:
        return Record(id=id, source="ctgov", source_date=source_date, fields=fields)

    return make


@pytest.fixture
def competitor_ctx():
    """A RunContext over the fake COPD world whose model gives a canned answer."""

    def make(as_of: date, answer: str) -> CompetitorCtx:
        config = RunConfig(
            disease="COPD", disease_id="EFO_0000341", as_of=as_of, mode="single_shot", profile="dev"
        )
        retrieval = StubRetrievalClient(as_of, competitor_records())
        return CompetitorCtx(config=config, retrieval=retrieval, backend=CannedLLM(answer))

    return make
