"""Build the Person-3 Langflow workflow and export it as an importable flow JSON.

    conda run -n langflow python build_flow.py            # writes flows/person3_analysis.json
    conda run -n langflow python build_flow.py --run      # also runs it headless (stub LLM: 0 tokens)

Flow:  P3 Run Setup -> A1 Disease -> A2 Mechanism -> A3 TPP -> A6 Red Team -> Report -> Chat Output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "components"))

from frontier_person3.a1_disease import P3A1Disease  # noqa: E402
from frontier_person3.a2_mechanism import P3A2Mechanism  # noqa: E402
from frontier_person3.a3_profile import P3A3Profile  # noqa: E402
from frontier_person3.a6_redteam import P3A6RedTeam  # noqa: E402
from frontier_person3.report import P3Report  # noqa: E402
from frontier_person3.run_setup import P3RunSetup  # noqa: E402
from lfx.components.input_output import ChatOutput  # noqa: E402
from lfx.graph import Graph  # noqa: E402

OUT = HERE / "flows" / "person3_analysis.json"


def build(llm_mode: str = "stub", as_of: str = "2021-01-01", model: str = "profile default") -> Graph:
    setup = P3RunSetup(_id="p3-setup")
    setup.set(llm_mode=llm_mode, as_of=as_of, model=model)
    a1 = P3A1Disease(_id="p3-a1")
    a1.set(state=setup.build_state)
    a2 = P3A2Mechanism(_id="p3-a2")
    a2.set(state=a1.run_agent)
    a3 = P3A3Profile(_id="p3-a3")
    a3.set(state=a2.run_agent)
    a6 = P3A6RedTeam(_id="p3-a6")
    a6.set(state=a3.run_agent)
    report = P3Report(_id="p3-report")
    report.set(state=a6.run_agent)
    chat = ChatOutput(_id="p3-chat")
    chat.set(input_value=report.build_report)
    return Graph(start=setup, end=chat, flow_name="Person 3 · Analysis agents (A1-A2-A3-A6)",
                 description="UPMC Frontier Hypothesis Engine, person 3: why now, mechanism, TPP, red team.")


def export(graph: Graph) -> Path:
    dump = graph.dump(name="Person 3 · Analysis agents (A1-A2-A3-A6)",
                      description="UPMC Frontier Hypothesis Engine, person 3: A1 why-now, A2 mechanism "
                                  "cards, A3 TPP, A6 red team. Writes a live analysis.xlsx.")
    data = dump if isinstance(dump, dict) else json.loads(json.dumps(dump, default=lambda o: o.__dict__))
    to_ui_format(data["data"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, default=str))
    return OUT


ORDER = ["p3-setup", "p3-a1", "p3-a2", "p3-a3", "p3-a6", "p3-report", "p3-chat"]


def _handle(d: dict) -> str:
    """Langflow's canvas handle string, exactly as the frontend rebuilds it to validate edges:
    keys sorted, no spaces, every quote replaced by 'œ'. Any other spacing -> edge dropped."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).replace('"', "œ")


def to_ui_format(g: dict) -> None:
    """graph.dump() gives a minimal payload; add what the React Flow canvas needs
    (node type/size/labels, edge ids and handle strings), matching Langflow starter flows."""
    for node in g["nodes"]:
        i = ORDER.index(node["id"]) if node["id"] in ORDER else len(ORDER)
        pos = {"x": 80 + i * 420, "y": 200 + (i % 2) * 120}
        meta = node["data"]["node"]
        node.update(type="genericNode", position=pos, positionAbsolute=pos, dragging=False,
                    selected=False, measured={"width": 320, "height": 360})
        node["data"].update(display_name=meta.get("display_name", node["data"].get("type")),
                            description=meta.get("description", ""))
    for e in g["edges"]:
        sh, th = e["data"]["sourceHandle"], e["data"]["targetHandle"]
        e.update(sourceHandle=_handle(sh), targetHandle=_handle(th),
                 id=f"reactflow__edge-{e['source']}{_handle(sh)}-{e['target']}{_handle(th)}",
                 className="", animated=False, selected=False)


async def run(graph: Graph) -> None:
    async for result in graph.async_start():
        vid = getattr(result, "vertex", None)
        if vid is not None:
            print(f"  done: {vid.id:10} {str(getattr(vid, 'custom_component', None) and vid.custom_component.status)[:110]}")
    rep = graph.get_vertex("p3-report").custom_component
    print("\nworkbook:", rep.status)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="run the flow headless after exporting")
    ap.add_argument("--llm", choices=["stub", "live"], default="stub")
    ap.add_argument("--as-of", default="2021-01-01")
    ap.add_argument("--model", default="profile default")
    a = ap.parse_args()
    print("exported:", export(build()))  # exported flow always defaults to stub (safe)
    if a.run:
        asyncio.run(run(build(a.llm, a.as_of, a.model)))


if __name__ == "__main__":
    main()
