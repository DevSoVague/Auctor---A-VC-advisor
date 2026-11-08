"""CLI for the person-3 LangGraph.

    python -m p3graph run                                   # backtest, single shot, stub LLM (0 tokens)
    python -m p3graph run --mode guided -i                  # pause at every checkpoint, review in terminal
    python -m p3graph run --mode guided                     # pause at the first checkpoint and exit
    python -m p3graph resume <thread> --action approve      # ... then resume later (state is in SQLite)
    python -m p3graph resume <thread> --action rerun --comment "restrict to former smokers"
    python -m p3graph resume <thread> --action edit --edited fixed.json
    python -m p3graph status <thread>
    python -m p3graph nodes                                 # every node + what workflow.yaml inserted
    python -m p3graph prompt a3_profile                     # the system prompt in use, and where it comes from
    python -m p3graph --workflow my.yaml run                # use another workflow file
    python -m p3graph steps <thread>                        # every node that ran, with timing and detail
    python -m p3graph chat [run_id] [-f]                    # the run as a chat in plain language (-f = live follow)
    python -m p3graph diagram                               # docs/graph.md (mermaid)
    python -m p3graph run --llm live --model openai/gpt-oss-120b --as-of today
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from p3graph import engine as E
from p3graph import narrate as NR
from p3graph import nodes as N
from p3graph import workflow as WF
from p3graph.graph import build

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "runs" / "checkpoints.sqlite"
WORKBOOK_NODES = {"a1_commit", "a2_commit", "a3_commit", "a4_commit", "a5_commit", "a6_commit", "a6_route"}
QUIET = {"claim_added", "agent_started"}
COMPACT = False  # --compact: the old one-line-per-node view instead of the chat view


def _print_event(ev: dict) -> None:
    e = ev.get("event")
    if e == "narration":
        if not COMPACT:
            print(NR.format_msg(f"{ev['icon']} {ev['who']}", ev["text"], ev.get("ms")), flush=True)
        return
    if e in QUIET or not COMPACT:  # chat view: the narration already says it
        return
    hid = ev.get("hypothesis_id") or ""
    extra = ev.get("reason") or ev.get("kill_criterion") or ev.get("msg") or ev.get("revise_target") or ""
    print(f"    · {e:14} {ev.get('agent', ''):13} {hid:4} {str(extra)[:90]}", flush=True)


async def _drive(graph, inp, cfg: dict, interactive: bool) -> None:
    """Stream until the graph ends or pauses at a checkpoint."""
    run_id = ((await graph.aget_state(cfg)).values or {}).get("run_id")
    while True:
        async for mode, chunk in graph.astream(inp, cfg, stream_mode=["updates", "custom"]):
            if mode == "custom":
                _print_event(chunk)
                continue
            for node in chunk:
                if node == "__interrupt__":
                    continue
                rows = (chunk[node] or {}).get("steps") if isinstance(chunk[node], dict) else None
                row = rows[0] if rows else {}
                if isinstance(chunk[node], dict) and chunk[node].get("run_id"):
                    run_id = chunk[node]["run_id"]
                if not COMPACT:
                    if not (row and row.get("agent")) and run_id and node not in ("setup", "next_card", "report"):  # step nodes narrate themselves
                        txt = NR.narrate_node(node, row)
                        NR.append_chat(run_id, NR.format_msg(f"▸ {node}", txt, row.get("ms") if row else None))
                        print(NR.format_msg(f"▸ {node}", txt), flush=True)
                elif row:
                    print(f"  ▸ {node:14} {str(row.get('hid') or ''):3} {row.get('ms', ''):>6} ms  "
                          f"{str(row.get('detail', ''))[:110]}", flush=True)
                else:
                    print(f"  ▸ {node}", flush=True)
                if node in WORKBOOK_NODES:  # live workbook after every commit
                    st = (await graph.aget_state(cfg)).values
                    N.write_workbook(st)
        snap = await graph.aget_state(cfg)
        if not snap.next:
            st = snap.values
            print("\n" + st.get("report", ""))
            return
        pending = [i for t in snap.tasks for i in t.interrupts]
        if not pending:
            return
        payload = pending[0].value
        _show_checkpoint(payload)
        if run_id:
            NR.append_chat(run_id, NR.format_msg(f"⏸ review · {payload['checkpoint']}",
                           "Paused for a human: " + "; ".join(str(x) for x in payload["summary"][:4])))
        tid = cfg["configurable"]["thread_id"]
        if not interactive:
            print(f"\nPaused. Resume with:\n  python -m p3graph resume {tid} --action approve"
                  f"\n  (or approve_all | rerun --comment '...' | edit --edited file.json)")
            return
        inp = Command(resume=_ask(payload))


def _show_checkpoint(p: dict) -> None:
    hid = f" · {p['hypothesis_id']}" if p.get("hypothesis_id") else ""
    print(f"\n━━ CHECKPOINT  {p['checkpoint']}{hid} ━━")
    for line in p["summary"]:
        print(f"   - {line}")
    pc = p.get("citation_check") or []
    print(f"   citation check: {len(pc)} fix(es) "
          + ", ".join(f"{x['action']}" for x in pc[:5]))


def _ask(p: dict) -> dict:
    a = input("   [a]pprove  [A]pprove all for this agent  [r]erun with comment  [e]dit from file  > ").strip()
    if a == "A":
        return {"action": "approve_all"}
    if a == "r":
        return {"action": "rerun", "comment": input("   comment: ")}
    if a == "e":
        tmp = ROOT / "runs" / f"edit_{p['checkpoint']}_{p.get('hypothesis_id') or 'all'}.json"
        tmp.write_text(json.dumps(p["output"], indent=1))
        input(f"   edit {tmp} then press Enter ")
        return {"action": "edit", "edited_output": json.loads(tmp.read_text()),
                "comment": "edited in terminal"}
    return {"action": "approve"}


async def _chat(a) -> None:
    """Replay (or follow) a run's chat.md. `target` = run id, or empty for the newest run."""
    runs = NR.RUNS
    if a.target:
        p = NR.chat_path(a.target)
    else:
        cands = sorted(runs.glob("*/chat.md"), key=lambda f: f.stat().st_mtime)
        if not cands:
            print("no chat.md yet: run something first")
            return
        p = cands[-1]
    if not p.exists():
        print(f"{p} not found")
        return
    print(f"# {p}\n")
    pos = 0
    while True:
        with p.open() as f:
            f.seek(pos)
            chunk = f.read()
            pos = f.tell()
        if chunk:
            print(chunk, end="", flush=True)
        if not a.follow:
            return
        await asyncio.sleep(0.5)


async def main_async(a) -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(DB)) as saver:
        graph = build(saver)
        if a.cmd == "diagram":
            out = ROOT / "docs" / "graph.md"
            out.write_text("# Person 3 graph\n\n```mermaid\n" + graph.get_graph().draw_mermaid() + "```\n")
            print("wrote", out)
            return
        if a.cmd == "nodes":
            names = sorted(n for n in graph.get_graph().nodes if not n.startswith("__"))
            print("nodes:", ", ".join(names))
            inserted = [i for i in WF.load()["insert"]]
            print("\ninserted from workflow.yaml:", ", ".join(f"{i['name']} (after {i['after']})" for i in inserted) or "none")
            return
        if a.cmd == "prompt":
            sysmsg = WF.system_for(a.agent) or E.system_prompt(a.agent)
            print(f"# source: {WF.prompt_source(a.agent)}\n\n{sysmsg}")
            return
        if a.cmd == "chat":
            await _chat(a)
            return
        if a.cmd == "steps":
            snap = await graph.aget_state({"configurable": {"thread_id": a.thread}})
            print(N.steps_md(snap.values))
            return
        if a.cmd == "status":
            snap = await graph.aget_state({"configurable": {"thread_id": a.thread}})
            v = snap.values
            print("next:", snap.next, "| current:", v.get("current"), "| queue:", v.get("queue"))
            print("verdicts:", {h: x["verdict"] for h, x in (v.get("verdicts") or {}).items()})
            print("decisions:", len(v.get("decisions", [])), "| workbook:", v.get("workbook_path"))
            return
        if a.cmd == "run":
            tid = a.thread or str(uuid.uuid4())[:8]
            cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 400}
            inp = {"config": {"disease": a.disease, "disease_id": a.disease_id, "as_of": a.as_of,
                              "mode": a.mode, "profile": a.profile, "audience": a.audience},
                   "llm": {"mode": a.llm, "model": a.model, "profile": a.profile},
                   "retrieval": a.retrieval, "search": a.search, "tools": a.tools, "judge": not a.no_judge, "cite": not a.no_cite, "pitch": a.pitch, "store_pitch": a.store_pitch, "max_cards": a.max_cards}
            print(f"thread {tid} · {a.mode} · as_of {a.as_of} · llm {a.llm}"
                  f"{' ' + a.model if a.model else ''} · retrieval {a.retrieval} · search {a.search}"
                  f" · tools {'on' if a.tools else 'off'} · judge {'off' if a.no_judge else 'on'}")
            await _drive(graph, inp, cfg, a.interactive)
        elif a.cmd == "resume":
            cfg = {"configurable": {"thread_id": a.thread}, "recursion_limit": 400}
            decision = {"action": a.action, "comment": a.comment or ""}
            if a.action == "edit":
                decision["edited_output"] = json.loads(Path(a.edited).read_text())
            await _drive(graph, Command(resume=decision), cfg, a.interactive)


def main() -> None:
    ap = argparse.ArgumentParser(prog="p3graph")
    ap.add_argument("--workflow", default=None, help="workflow file to use instead of workflow.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--as-of", default="today",
                   help="'today' uses the current date (default); YYYY-MM-DD selects an explicit cutoff.")
    r.add_argument("--mode", choices=["single_shot", "guided"], default="single_shot")
    r.add_argument("--llm", choices=["stub", "live"], default="stub")
    r.add_argument("--model", default=None, help="force one Nebius model for all agents")
    r.add_argument("--profile", choices=["dev", "final"], default="dev")
    r.add_argument("--retrieval", default="stub", help="'stub' or retrieval API URL (the corpus / RAG side)")
    r.add_argument("--search", choices=list(E.SEARCH_SOURCES), default="corpus",
                   help="where search() looks: the corpus, Tavily web search (needs TAVILY_API_KEY), both, the Postgres+Milvus database (database, database+web), or the COPD evidence store (evstore, evstore+web)")
    r.add_argument("--tools", action="store_true",
                   help="agents call evidence-store / web tools (A1 also ingests new evidence); use with --search evstore")
    r.add_argument("--pitch", default=None, help="startup pitch document (PDF, DOCX, TXT, MD or URL): the run tests it")
    r.add_argument("--store-pitch", action="store_true", help="also write the pitch to the shared evidence store")
    r.add_argument("--no-cite", action="store_true", help="skip the A7 citation check after every node")
    r.add_argument("--no-judge", action="store_true", help="skip A6b: route on A6's own verdict")
    r.add_argument("--max-cards", type=int, default=0, help="run only the top N cards through A3..A6b (0 = all)")
    r.add_argument("--disease", default="COPD")
    r.add_argument("--disease-id", default="EFO_0000341")
    r.add_argument("--audience", choices=["generalist", "specialist"], default="generalist")
    r.add_argument("--thread", default=None)
    r.add_argument("-i", "--interactive", action="store_true", help="review checkpoints in the terminal")
    r.add_argument("--compact", action="store_true", help="old one-line-per-node output instead of the chat view")
    s = sub.add_parser("resume")
    s.add_argument("thread")
    s.add_argument("--action", choices=["approve", "approve_all", "rerun", "edit"], default="approve")
    s.add_argument("--comment", default="")
    s.add_argument("--edited", help="JSON file with the edited output (for --action edit)")
    s.add_argument("-i", "--interactive", action="store_true")
    t = sub.add_parser("status")
    t.add_argument("thread")
    sub.add_parser("nodes", help="list every node and the nodes inserted from workflow.yaml")
    pr = sub.add_parser("prompt", help="print the system prompt an agent will use (and where it comes from)")
    pr.add_argument("agent", choices=["a1_disease", "a2_mechanism", "a3_profile", "a6_redteam"])
    u = sub.add_parser("steps", help="print every step of a run: node, card, ms, what happened")
    u.add_argument("thread")
    c = sub.add_parser("chat", help="read a run's chat (natural-language trace); --follow to watch it live")
    c.add_argument("target", nargs="?", default=None, help="run id (default: newest run)")
    c.add_argument("-f", "--follow", action="store_true", help="keep printing as the run writes (use in a 2nd terminal)")
    sub.add_parser("diagram")
    a = ap.parse_args()
    global COMPACT
    COMPACT = bool(getattr(a, "compact", False))
    if a.workflow:
        os.environ["P3_WORKFLOW"] = str(Path(a.workflow).resolve())
    if a.cmd == "run" and a.llm == "live" and a.mode == "guided" and not a.interactive:
        print("note: guided + live pauses after A1; resume when ready.", file=sys.stderr)
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
