"""Graph state. Plain JSON-able dicts/lists so the SQLite checkpointer can persist every step."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge(a: dict | None, b: dict | None) -> dict:
    """Shallow dict merge; parallel branches (A4 || A5) can both write."""
    return {**(a or {}), **(b or {})}


def merge_usage(a: dict | None, b: dict | None) -> dict:
    """Sum token usage per agent across nodes."""
    out = {k: dict(v) for k, v in (a or {}).items()}
    for agent, u in (b or {}).items():
        cur = out.setdefault(agent, {"model": u.get("model", ""), "calls": 0, "input_tokens": 0,
                                     "cached_tokens": 0, "output_tokens": 0, "latency_ms": 0})
        cur["model"] = u.get("model") or cur["model"]
        for k in ("calls", "input_tokens", "cached_tokens", "output_tokens", "latency_ms"):
            cur[k] += u.get(k, 0)
    return out


class P3State(TypedDict, total=False):
    # ---- run setup (input)
    run_id: str
    config: dict  # RunConfig as JSON; config["mode"] = guided | single_shot
    llm: dict  # {"mode": stub|live, "model": str|None, "profile": dev|final}
    retrieval: str  # "stub" or person 1's retrieval API URL (the corpus / RAG side)
    search: str  # where search() looks: corpus | web (Tavily) | both | database | evstore | evstore+web
    tools: bool  # agents call DB/web tools between prompt and llm (agents/analysis/db_tools.py)
    max_cards: int  # process only the top N cards through A3..A6b (0/None = all)
    cite: bool  # A7 citation check after every node (default on; False = off)
    judge: bool  # A6b judge decides each card from A3's profile and A6's verdict (drives a6_route)

    # ---- agent outputs
    why_now: dict | None  # A1
    cards: list[dict]  # A2 (ranked)
    grades: dict  # A2 code-computed grades per hypothesis
    profiles: Annotated[dict, merge]  # A3: hid -> ProductProfile
    assets: Annotated[dict, merge]  # A4/A5 (person 4): hid -> {"inactive":..., "landscape":...}
    verdicts: Annotated[dict, merge]  # A6: hid -> Verdict
    judgments: Annotated[dict, merge]  # A6b: hid -> JudgeDecision

    # ---- per-hypothesis loop
    queue: list[str]  # hypotheses still to process, in rank order
    current: str | None  # hypothesis being processed

    # ---- control + audit
    feedback: Annotated[dict, merge]  # "agent" or "agent:hid" -> reviewer/red-team comment (None = used)
    reruns: Annotated[dict, merge]  # "agent:hid" -> reruns used (PRD: max 2)
    revise_used: Annotated[dict, merge]  # hid -> True once the single revise round is spent
    auto_approve: Annotated[dict, merge]  # agent -> True ("approve the rest for this agent")
    killed: Annotated[list, operator.add]  # [{hypothesis_id, mechanism, reason, kill_criterion}]
    decisions: Annotated[list, operator.add]  # PRD decisions table rows
    events: Annotated[list, operator.add]  # every agent event ([event, payload]) for traces/workbook
    signals: Annotated[dict, merge]  # pub_counts, a2_grades, a6_checklist, postcheck
    usage: Annotated[dict, merge_usage]  # tokens per agent
    prompts: dict  # agent -> system prompt typed into a node (Langflow); wins over prompts/ files
    loops: Annotated[dict, merge]  # custom-node loop counters: "node[:hid]" -> rounds used
    work: Annotated[dict, merge]  # per-step scratch: "agent[:hid]" -> {pack, msgs, raw, out, problems, ...}
    steps: Annotated[list, operator.add]  # one row per node run: {node, agent, hid, ms, detail}
    chat: Annotated[list, operator.add]  # plain-language message per node: {who, icon, node, hid, ms, text, t}

    # ---- output
    report: str
    workbook_path: str


Decision = dict[str, Any]  # {"action": approve|approve_all|edit|rerun, "edited_output"?, "comment"?}
