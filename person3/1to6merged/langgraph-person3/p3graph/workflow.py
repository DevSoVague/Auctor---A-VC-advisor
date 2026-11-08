"""Workflow editing: prompts, spliced-in custom nodes, tools. Read from workflow.yaml (or P3_WORKFLOW).

    prompts/<agent>.md           replaces that agent's system prompt
    prompts/<agent>.append.md    appends to it
    workflow.yaml `insert:`      splices your own node after any step; see docs/EDITING.md
"""

from __future__ import annotations

import os
import sys
import time
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

import yaml
from langgraph.types import Command

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "workflow.yaml"
ACTIVE: dict = {}  # set by graph.build(); read by the prompt steps


def load(workflow: dict | str | Path | None = None) -> dict:
    """workflow: a dict (tests), a path, or None → $P3_WORKFLOW → ./workflow.yaml (missing = defaults)."""
    if isinstance(workflow, dict):
        wf = dict(workflow)
    else:
        path = Path(workflow or os.getenv("P3_WORKFLOW") or DEFAULT_FILE)
        wf = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    wf.setdefault("prompts_dir", "prompts")
    wf["insert"] = [i for i in (wf.get("insert") or []) if i.get("enabled", True)]
    return wf


# ---------------------------------------------------------------------------- prompts
def prompts_dir(wf: dict | None = None) -> Path:
    p = Path((wf or ACTIVE).get("prompts_dir", "prompts"))
    return p if p.is_absolute() else ROOT / p


def system_for(agent: str, wf: dict | None = None) -> str | None:
    """Edited system prompt for this agent, or None to use the engine's default."""
    d = prompts_dir(wf)
    full, extra = d / f"{agent}.md", d / f"{agent}.append.md"
    if not full.exists() and not extra.exists():
        return None
    from p3graph import engine as E

    base = full.read_text() if full.exists() else E.system_prompt(agent)
    return base + ("\n\n" + extra.read_text() if extra.exists() else "")


def prompt_source(agent: str, wf: dict | None = None) -> str:
    d = prompts_dir(wf)
    parts = [str(p) for p in (d / f"{agent}.md", d / f"{agent}.append.md") if p.exists()]
    return " + ".join(parts) if parts else "engine default (frontier-engine/agents/prompts)"


# ---------------------------------------------------------------------------- custom nodes
AGENT_OF = {"a1": "a1_disease", "a2": "a2_mechanism", "a3": "a3_profile", "a6": "a6_redteam"}


def resolve(spec: str) -> Callable:
    """'custom_nodes:safety_gate' → the function (module is looked up in the project root)."""
    mod, fn = spec.split(":")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return getattr(import_module(mod), fn)


def make_node(item: dict, natural_next: str):
    """Wrap fn(state, params) into a graph node: logs a step row, defaults to the natural next node.
    fn may return a dict (state update, optional 'detail'), a Command(goto=..., update=...), or None."""
    name, fn = item["name"], resolve(item["node"])
    params = {**(item.get("params") or {}), "_name": name, "_after": item["after"], "_next": natural_next}

    async def node(state):
        from p3graph import live
        send = live.observer(state, name, hid=state.get("current"))
        send("start")
        t0 = time.time()
        try:
            res = await fn(state, params)
        except BaseException as exc:
            send("error", {"error": f"{type(exc).__name__}: {exc}"})
            raise
        goto = natural_next
        if isinstance(res, Command):
            goto, upd = (res.goto or natural_next), dict(res.update or {})
        else:
            upd = dict(res or {})
        detail = upd.pop("detail", "")
        send("end", {"detail": detail, "result": upd})
        row = {"node": name, "agent": None, "hid": state.get("current"), "ms": int((time.time() - t0) * 1000),
               "detail": detail, "custom": True}
        upd["steps"] = list(upd.get("steps", [])) + [row]
        return Command(goto=goto, update=upd)

    node.__name__ = name
    return node


class Wiring:
    """Routes every edge through the inserted nodes. `to(src, dst)` returns where src should go."""

    def __init__(self, inserts: list[dict]):
        self.by_after: dict[str, list[dict]] = {}
        for i in inserts:
            self.by_after.setdefault(i["after"], []).append(i)
        self.points: set[str] = set()  # every place an insert is allowed
        self.natural: dict[str, str] = {}
        self.items = inserts

    def to(self, src: str, dst: str) -> str:
        self.points.add(src)
        chain = self.by_after.get(src)
        if not chain:
            return dst
        names = [i["name"] for i in chain]
        for n, nxt in zip(names, names[1:] + [dst]):
            self.natural[n] = nxt
        return names[0]

    def finish(self, g) -> None:
        bad = [i["after"] for i in self.items if i["after"] not in self.points]
        if bad:
            raise ValueError(f"workflow.yaml: cannot insert after {bad}. Valid places: {sorted(self.points)}")
        names = [i["name"] for i in self.items]
        if len(set(names)) != len(names):
            raise ValueError(f"workflow.yaml: insert names must be unique, got {names}")
        for i in self.items:
            nxt = self.natural[i["name"]]
            g.add_node(i["name"], make_node(i, nxt),
                       destinations=tuple(dict.fromkeys([nxt, *(i.get("may_goto") or [])])))
