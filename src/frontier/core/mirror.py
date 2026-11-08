"""Copy a run's token usage from Person 3's pipeline into the shared cost log (llm_calls).

Person 3's agents call Nebius through their own client, so the router's llm_calls table only saw
the memo agent. After a run, `mirror_usage` writes one row per pipeline agent (calls, tokens, list
price in USD, latency) so the cost log covers the whole run. A rerun replaces those rows; the memo
agent's own router rows are never touched. The pipeline's short run id is mapped to a stable UUID.
"""

import uuid

import psycopg

from frontier.core.costs import load_prices

MEMO_AGENT = "a8_orchestrator"  # logged by the router itself


def run_uuid(run_id: str) -> str:
    """The pipeline's run id (e.g. '99c6c721') as a stable UUID for llm_calls.run_id."""
    try:
        return str(uuid.UUID(run_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"frontier-run:{run_id}"))


def rows_for(run_id: str, usage: dict, prices=None) -> list[tuple]:
    """One tuple per agent that made calls: the llm_calls columns after run_id."""
    prices = load_prices() if prices is None else prices
    rows = []
    for agent, u in (usage or {}).items():
        if agent == MEMO_AGENT or not u.get("calls"):
            continue
        price = prices.get(u.get("model", ""))
        usd = (
            price.cost(
                u.get("input_tokens", 0), u.get("cached_tokens", 0), u.get("output_tokens", 0)
            )
            if price
            else None
        )
        rows.append(
            (
                run_uuid(run_id),
                agent,
                u.get("model"),
                u.get("input_tokens", 0),
                u.get("cached_tokens", 0),
                u.get("output_tokens", 0),
                usd,
                u.get("latency_ms", 0),
                False,
            )
        )
    return rows


async def mirror_usage(database_url: str, run_id: str, usage: dict) -> int:
    """Replace this run's pipeline rows in llm_calls; returns how many rows were written."""
    rows = rows_for(run_id, usage)
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        await conn.execute(
            "DELETE FROM llm_calls WHERE run_id = %s::uuid AND agent <> %s",
            (run_uuid(run_id), MEMO_AGENT),
        )
        for row in rows:
            await conn.execute(
                "INSERT INTO llm_calls (run_id, agent, model, input_tokens, cached_tokens,"
                " output_tokens, list_price_usd, latency_ms, cache_hit)"
                " VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)",
                row,
            )
    return len(rows)
