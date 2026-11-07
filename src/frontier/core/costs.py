"""Prices and the cost log: every model call becomes one llm_calls row (owner: person 2)."""

from dataclasses import dataclass
from typing import Protocol

import psycopg

from frontier.core.config import load_yaml


class UnpricedModel(RuntimeError):
    """A model has no entry in prices.yaml, so the USD cap could not be enforced."""


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input: float
    cached_input: float
    output: float

    def cost(self, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        uncached = max(input_tokens - cached_tokens, 0)
        return (
            uncached * self.input + cached_tokens * self.cached_input + output_tokens * self.output
        ) / 1_000_000


def load_prices(raw: dict | None = None) -> dict[str, Price]:
    raw = load_yaml("prices") if raw is None else raw
    return {
        model: Price(
            input=float(p["input"]),
            cached_input=float(p.get("cached_input", p["input"])),
            output=float(p["output"]),
        )
        for model, p in raw.items()
    }


def price_for(prices: dict[str, Price], model: str) -> Price:
    if model not in prices:
        raise UnpricedModel(
            f"No price for model {model!r} in core/prices.yaml. Add it (USD per 1M tokens) "
            "so the per-run USD cap can be enforced."
        )
    return prices[model]


@dataclass
class CallRecord:
    """One row of llm_calls."""

    run_id: str
    agent: str
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    list_price_usd: float
    latency_ms: int
    cache_hit: bool


@dataclass
class RunTotals:
    calls: int  # paid calls only; cache hits are free and don't count
    usd: float


class CostLog(Protocol):
    async def record(self, call: CallRecord) -> None: ...
    async def totals(self, run_id: str) -> RunTotals: ...


class MemoryCostLog:
    """In-process cost log for tests."""

    def __init__(self) -> None:
        self.rows: list[CallRecord] = []

    async def record(self, call: CallRecord) -> None:
        self.rows.append(call)

    async def totals(self, run_id: str) -> RunTotals:
        paid = [r for r in self.rows if r.run_id == run_id and not r.cache_hit]
        return RunTotals(calls=len(paid), usd=sum(r.list_price_usd for r in paid))


class PostgresCostLog:
    """The llm_calls table."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def record(self, call: CallRecord) -> None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                "INSERT INTO llm_calls (run_id, agent, model, input_tokens, cached_tokens,"
                " output_tokens, list_price_usd, latency_ms, cache_hit)"
                " VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    call.run_id,
                    call.agent,
                    call.model,
                    call.input_tokens,
                    call.cached_tokens,
                    call.output_tokens,
                    call.list_price_usd,
                    call.latency_ms,
                    call.cache_hit,
                ),
            )

    async def totals(self, run_id: str) -> RunTotals:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            cur = await conn.execute(
                "SELECT count(*), coalesce(sum(list_price_usd), 0) FROM llm_calls"
                " WHERE run_id = %s::uuid AND NOT cache_hit",
                (run_id,),
            )
            calls, usd = await cur.fetchone()
        return RunTotals(calls=calls, usd=float(usd))
