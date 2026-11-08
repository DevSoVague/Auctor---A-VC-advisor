"""LangGraph can do what the PRD's flow needs (Flow and checkpoints):
fixed, code-defined order; pause at a checkpoint in guided mode; resume with the
reviewer's action; skip the pause in single-shot mode. Person 5 builds the real flow."""

from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict, total=False):
    mode: str
    steps: list[str]
    review: str


def a1(state: State) -> State:
    return {"steps": [*state.get("steps", []), "a1_disease"]}


def checkpoint(state: State) -> State:
    if state["mode"] == "single_shot":
        return {"review": "approve"}
    action = interrupt({"waiting_on": "a1_disease"})  # pauses; resumes with the action
    return {"review": action}


def a2(state: State) -> State:
    return {"steps": [*state["steps"], "a2_mechanism"]}


def build():
    graph = StateGraph(State)
    graph.add_node("a1", a1)
    graph.add_node("checkpoint", checkpoint)
    graph.add_node("a2", a2)
    graph.add_edge(START, "a1")  # every edge is code; no model picks the next step
    graph.add_edge("a1", "checkpoint")
    graph.add_edge("checkpoint", "a2")
    graph.add_edge("a2", END)
    return graph.compile(checkpointer=InMemorySaver())


def test_guided_mode_pauses_at_the_checkpoint_and_resumes():
    flow = build()
    run = {"configurable": {"thread_id": "run-1"}}  # thread_id = run_id
    paused = flow.invoke({"mode": "guided"}, run)
    assert paused["steps"] == ["a1_disease"]
    assert paused["__interrupt__"][0].value == {"waiting_on": "a1_disease"}

    done = flow.invoke(Command(resume="approve"), run)
    assert done["steps"] == ["a1_disease", "a2_mechanism"]
    assert done["review"] == "approve"


def test_single_shot_runs_straight_through():
    done = build().invoke({"mode": "single_shot"}, {"configurable": {"thread_id": "run-2"}})
    assert done["steps"] == ["a1_disease", "a2_mechanism"]
    assert "__interrupt__" not in done
