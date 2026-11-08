"""Build the step-by-step Person-3 flow (every LangGraph step is its own Langflow node).

    conda run -n langflow python build_steps_flow.py                  # writes flows/person3_steps.json
    conda run -n langflow python build_steps_flow.py --run            # also run it headless (stub: 0 tokens)
    conda run -n langflow python build_steps_flow.py --run --llm live --model openai/gpt-oss-120b

Layout: Chat Input on top, then one row per agent, steps left to right. After A2 the flow forks: A3, A4 and A5
run side by side (A4/A5 prompt waits for A3's profile), then P3 · Join merges them for A6. Add your own node with "P3 · Custom Node" from the sidebar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib import import_module
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "components"))

from build_flow import _handle  # noqa: E402
from gen_components import ORDER  # noqa: E402
from lfx.components.input_output import ChatInput, ChatOutput  # noqa: E402
from lfx.graph import Graph  # noqa: E402

NAME = "Person 3 · Analysis agents (step by step)"
OUT = HERE / "flows" / "person3_steps.json"
ROWS = ["a1", "a2", "a3", "a4", "a5", "a6"]
PARALLEL = ("a4", "a5")  # start beside A3 right after A2; joined with A3 before A6
COL, ROW = 400, 560


def _cls(module: str, name: str):
    return getattr(import_module(f"frontier_person3.{module}"), name)


def build(llm_mode: str = "stub", as_of: str = "today", model: str = "profile default"):
    """Returns (graph, positions {node id: (col,row)})."""
    pos: dict[str, tuple[int, int]] = {}
    chat_in = ChatInput(_id="p3-chat-in")
    chat_in.set(input_value="run")
    pos["p3-chat-in"] = (0, 0)
    setup = _cls("step_setup", "P3StepSetup")(_id="p3-setup")
    setup.set(llm_mode=llm_mode, as_of=as_of, model=model, message=chat_in.message_response)
    pos["p3-setup"] = (0, 1)
    comps: dict[str, object] = {}

    def row(a: str, r: int, state_in, start: int = 0):
        """One agent's steps left to right, chained; returns the last step's output."""
        out = state_in
        for c, step in enumerate(ORDER[a]):
            comp = _cls(f"step_{a}_{step}", f"P3{a.upper()}{step.capitalize()}")(_id=f"p3-{a}-{step}")
            comps[f"{a}-{step}"] = comp
            comp.set(state=out)
            out = comp.run_step
            pos[f"p3-{a}-{step}"] = (start + c + 1, r)
        return out

    a1 = row("a1", 1, setup.build_state)
    fork = row("a2", 2, a1)  # A3, A4 and A5 all start from A2's last step and run side by side
    a3 = row("a3", 3, fork)
    for a in PARALLEL:
        row(a, ROWS.index(a) + 1, fork)
        comps[f"{a}-prompt"].set(a3_state=a3)  # A4/A5 prompt waits for A3's profile
    join = _cls("step_join", "P3StepJoin")(_id="p3-join")
    join.set(base=fork, branches=[a3, *(comps[f"{a}-commit"].run_step for a in PARALLEL)])
    pos["p3-join"] = (len(ORDER["a5"]) + 1, ROWS.index("a6") + 0.5)
    last = row("a6", ROWS.index("a6") + 1.5, join.run_step)
    report = _cls("step_report", "P3StepReport")(_id="p3-report")
    report.set(state=last)
    pos["p3-report"] = (1, len(ROWS) + 1.5)
    a8 = _cls("step_a8_memo", "P3StepA8Memo")(_id="p3-a8")
    a8.set(state=report.build_state)  # agent 8 runs last, from the run's own checked output
    pos["p3-a8"] = (2, len(ROWS) + 1.5)
    chat = ChatOutput(_id="p3-chat")
    chat.set(input_value=a8.build_summary)
    pos["p3-chat"] = (3, len(ROWS) + 1.5)
    return Graph(start=chat_in, end=chat, flow_name=NAME, description="Person 3, every agent step as a node."), pos


def export(graph: Graph, pos: dict) -> Path:
    dump = graph.dump(name=NAME, description="UPMC Frontier Hypothesis Engine, person 3. Every step of A1, A2, A3 "
                      "A4, A5 and A6 is its own node, and A8 writes the memo last; A3, A4 and A5 run side by side after A2, then join for A6: edit a prompt, swap a model, "
                      "or add a Custom Node.")
    data = dump if isinstance(dump, dict) else json.loads(json.dumps(dump, default=lambda o: o.__dict__))
    g = data["data"]
    for node in g["nodes"]:
        c, r = pos.get(node["id"], (0, 0))
        p = {"x": 80 + c * COL, "y": 60 + r * ROW}
        meta = node["data"]["node"]
        node.update(type="genericNode", position=p, positionAbsolute=p, dragging=False, selected=False,
                    measured={"width": 320, "height": 360})
        node["data"].update(display_name=meta.get("display_name", node["data"].get("type")),
                            description=meta.get("description", ""))
    for e in g["edges"]:
        sh, th = e["data"]["sourceHandle"], e["data"]["targetHandle"]
        e.update(sourceHandle=_handle(sh), targetHandle=_handle(th),
                 id=f"reactflow__edge-{e['source']}{_handle(sh)}-{e['target']}{_handle(th)}",
                 className="", animated=False, selected=False)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, default=str))
    return OUT


async def run(graph: Graph) -> None:
    async for result in graph.async_start():
        vid = getattr(result, "vertex", None)
        if vid is not None:
            comp = getattr(vid, "custom_component", None)
            print(f"  {vid.id:22} {str(getattr(comp, 'status', ''))[:120]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--llm", choices=["stub", "live"], default="stub")
    ap.add_argument("--as-of", default="today")
    ap.add_argument("--model", default="profile default")
    a = ap.parse_args()
    g, pos = build()
    print("exported:", export(g, pos), "·", len(pos), "nodes")  # exported flow always defaults to stub (safe)
    if a.run:
        asyncio.run(run(build(a.llm, a.as_of, a.model)[0]))


if __name__ == "__main__":
    main()
