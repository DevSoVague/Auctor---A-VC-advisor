"""Build the step-by-step Person-3 flow (every LangGraph step is its own Langflow node).

    conda run -n langflow python build_steps_flow.py                  # writes flows/person3_steps.json
    conda run -n langflow python build_steps_flow.py --run            # also run it headless (stub: 0 tokens)
    conda run -n langflow python build_steps_flow.py --run --llm live --model openai/gpt-oss-120b

Layout: Chat Input on top, then one row per agent, steps left to right. Add your own node with "P3 · Custom Node" from the sidebar.
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
ROWS = ["a1", "a2", "a3", "a6"]
COL, ROW = 400, 560


def _cls(module: str, name: str):
    return getattr(import_module(f"frontier_person3.{module}"), name)


def build(llm_mode: str = "stub", as_of: str = "2021-01-01", model: str = "profile default"):
    """Returns (graph, positions {node id: (col,row)})."""
    pos: dict[str, tuple[int, int]] = {}
    chat_in = ChatInput(_id="p3-chat-in")
    chat_in.set(input_value="run")
    pos["p3-chat-in"] = (0, 0)
    setup = _cls("step_setup", "P3StepSetup")(_id="p3-setup")
    setup.set(llm_mode=llm_mode, as_of=as_of, model=model, message=chat_in.message_response)
    pos["p3-setup"] = (0, 1)
    prev_out = setup.build_state
    for r, a in enumerate(ROWS):
        for c, step in enumerate(ORDER[a]):
            comp = _cls(f"step_{a}_{step}", f"P3{a.upper()}{step.capitalize()}")(_id=f"p3-{a}-{step}")
            comp.set(state=prev_out)
            prev_out = comp.run_step
            pos[f"p3-{a}-{step}"] = (c + 1, r + 1)
    report = _cls("step_report", "P3StepReport")(_id="p3-report")
    report.set(state=prev_out)
    pos["p3-report"] = (1, len(ROWS) + 1)
    chat = ChatOutput(_id="p3-chat")
    chat.set(input_value=report.build_report)
    pos["p3-chat"] = (2, len(ROWS) + 1)
    return Graph(start=chat_in, end=chat, flow_name=NAME, description="Person 3, every agent step as a node."), pos


def export(graph: Graph, pos: dict) -> Path:
    dump = graph.dump(name=NAME, description="UPMC Frontier Hypothesis Engine, person 3. Every step of A1, A2, A3 "
                      "and A6 is its own node: edit a prompt, swap a model, or add a Custom Node.")
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
    ap.add_argument("--as-of", default="2021-01-01")
    ap.add_argument("--model", default="profile default")
    a = ap.parse_args()
    g, pos = build()
    print("exported:", export(g, pos), "·", len(pos), "nodes")  # exported flow always defaults to stub (safe)
    if a.run:
        asyncio.run(run(build(a.llm, a.as_of, a.model)[0]))


if __name__ == "__main__":
    main()
