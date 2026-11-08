"""Agents as named steps, run in plain asyncio or mapped onto LangGraph for audit and display.

An agent is a sequence of (name, step) pairs. Each step reads the shared state
and returns the keys it adds. `run_steps` runs them in order with no extra
dependency; `build_graph` maps the same steps onto a LangGraph StateGraph (one
node per step), so both paths execute identical code. LangGraph is an optional
extra: `uv sync --extra graph`.
"""

import dataclasses
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel

from frontier.core.retrieval import Record

State = dict[str, Any]
Step = Callable[[State], Awaitable[State]]
Steps = Sequence[tuple[str, Step]]


async def run_steps(steps: Steps, state: State) -> State:
    """Run the steps in order, merging each one's output into the state."""
    for _, step in steps:
        state |= await step(state)
    return state


def build_graph(steps: Steps, state_type: type):
    """The steps as a compiled LangGraph graph: START -> step 1 -> ... -> END."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as error:
        raise ImportError(
            "LangGraph is optional: install it with `uv sync --extra graph`"
        ) from error

    graph = StateGraph(state_type)
    previous = START
    for name, step in steps:
        graph.add_node(name, step)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()


async def audit_graph(graph, state: State) -> tuple[State, list[dict]]:
    """Run a compiled graph, returning the final state and a JSON-safe trail of each step."""
    trail = []
    async for update in graph.astream(state, stream_mode="updates"):
        for name, output in update.items():
            trail.append({"step": name, "output": jsonable(output)})
            state |= output
    return state, trail


def jsonable(value: Any) -> Any:
    """A JSON-safe view for the audit trail: records collapse to their IDs."""
    if isinstance(value, Record):
        return value.id
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, frozenset | set):
        return sorted((jsonable(v) for v in value), key=str)
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value
