import asyncio
import json
from datetime import date

import pytest

from frontier.agents.competitor import a4_inactive, a5_active
from frontier.agents.competitor.steps import audit_graph, jsonable, run_steps
from frontier.agents.schemas import A4Input, A5Input
from frontier.core.context import load_fixture
from frontier.core.retrieval import Record

LIVE = date(2026, 11, 7)
ANSWER = "a5_model_output.json"
PDE_CARD = A5Input.model_validate(load_fixture("a5_active.input"))
NODES = ["gather", "shortlist", "establish", "judge", "merge", "score"]
A4_NODES = ["gather", "triage", "establish", "judge", "merge"]


def test_run_steps_merges_outputs_in_order():
    async def one(s):
        return {"a": 1}

    async def two(s):
        return {"b": s["a"] + 1}

    assert asyncio.run(run_steps([("one", one), ("two", two)], {})) == {"a": 1, "b": 2}


def test_jsonable_collapses_records_to_ids():
    record = Record(id="nct:FIXTURE-C1", source="ctgov", source_date=date(2020, 1, 1))
    value = {"trials": (record,), "on": date(2021, 6, 1), "tokens": frozenset({"pde4", "pde3"})}
    assert jsonable(value) == {
        "trials": ["nct:FIXTURE-C1"],
        "on": "2021-06-01",
        "tokens": ["pde3", "pde4"],
    }


@pytest.mark.parametrize(("agent", "nodes"), [(a5_active, NODES), (a4_inactive, A4_NODES)])
def test_graph_maps_every_step_in_order(agent, nodes):
    drawn = agent.graph().get_graph()
    assert [n for n in drawn.nodes if not n.startswith("__")] == nodes
    mermaid = drawn.draw_mermaid()
    assert all(node in mermaid for node in nodes)


def test_a4_graph_matches_plain_run_and_shows_what_was_dropped(competitor_ctx):
    card = A4Input.model_validate(load_fixture("a4_inactive.input"))
    answer = "a4_model_output.json"
    plain = asyncio.run(a4_inactive.run(card, competitor_ctx(LIVE, answer)))
    state, trail = asyncio.run(
        audit_graph(a4_inactive.graph(), a4_inactive.inputs(card, competitor_ctx(LIVE, answer)))
    )

    assert state["inactive"] == plain
    assert [entry["step"] for entry in trail] == A4_NODES
    steps = {entry["step"]: entry["output"] for entry in trail}
    assert "FIX-998" in json.dumps(steps["judge"]["draft"])  # proposed by the model
    assert steps["merge"]["matching"]["unmatched"][0] == "FIX-998"  # and caught by code
    assert "FIX-998" not in json.dumps(steps["merge"]["inactive"])


def test_graph_gives_the_same_landscape_and_a_full_audit_trail(competitor_ctx):
    plain = asyncio.run(a5_active.run(PDE_CARD, competitor_ctx(LIVE, ANSWER)))
    state, trail = asyncio.run(
        audit_graph(a5_active.graph(), a5_active.inputs(PDE_CARD, competitor_ctx(LIVE, ANSWER)))
    )

    assert state["landscape"] == plain
    assert [entry["step"] for entry in trail] == NODES
    json.dumps(trail)  # the trail is JSON-safe

    steps = {entry["step"]: entry["output"] for entry in trail}
    # The trail shows the model inventing FIX-999 and the merge step catching it.
    assert "FIX-999" in json.dumps(steps["judge"]["draft"])
    assert steps["merge"]["matching"]["unmatched"] == ["FIX-999"]
    assert "FIX-999" not in json.dumps(steps["merge"]["kept"])
    crowding = steps["score"]["crowding"]
    assert crowding["score"] == plain.crowding_score
    assert set(crowding["signals"]) == set(crowding["references"]) == {"programs", "publications"}
    lead = steps["establish"]["facts"][0]
    assert lead[0]["asset"] == "fixtafilast" and "nct:FIXTURE-C101" in lead[1]
