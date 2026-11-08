"""Natural-language narration of every node, so a run reads like a chat instead of JSON.

`say(...)` turns one finished node into a short message: who is speaking (the node), what it did,
and the actual content (documents found, what the model answered, what the checks changed).
Each message is
  * streamed live as a custom event {"event": "narration", ...}  (CLI, LangGraph Studio, any client)
  * appended to runs/<run_id>/chat.md                           (tail -f it, or `python -m p3graph chat`)

Nothing here changes what a node does: it only reads the node's result.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

RUNS = Path(__file__).resolve().parents[1] / "runs"

AGENT_NAME = {
    "a1_disease": "A1 · Why now",
    "a2_mechanism": "A2 · Mechanisms",
    "a3_profile": "A3 · Product profile",
    "a4_inactive": "A4 · Inactive assets",
    "a5_active": "A5 · Active landscape",
    "a6_redteam": "A6 · Red team",
    "a6b_judge": "A6b · Judge",
    "a0_intake": "A0 · Intake",
}
STEP_ICON = {"retrieve": "🔎", "prompt": "📝", "llm": "🤖", "parse": "🧩", "fix": "🔧", "postcheck": "✅",
             "validate": "📏", "grade": "🏷️", "commit": "💾", "route": "🚦", "toolloop": "🛠️", "judge": "⚖️", "cite": "📚",
             "gather": "📥", "triage": "🗂️", "shortlist": "🗂️", "establish": "📋", "merge": "🔗", "score": "📊"}


def _t(s: Any, n: int = 160) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _list(items: list[str], n: int = 4) -> str:
    items = [str(i) for i in items]
    more = f" (+{len(items) - n} more)" if len(items) > n else ""
    return "\n".join(f"   • {_t(i)}" for i in items[:n]) + more


def _docs(pack: dict, n: int = 5) -> str:
    items = sorted((pack or {}).get("items", {}).values(), key=lambda r: str(r.get("source_date", "")), reverse=True) \
        if isinstance(pack, dict) and "items" in pack else []
    return _list([f"{d.get('title', d.get('id'))} [{d['id']}, {str(d.get('source_date', ''))[:10]}]" for d in items], n)


def cap(agent: str, hid: str | None, step: str | None = None) -> str:
    """Speaker label, e.g. 'A3 · Product profile · h2 · llm'."""
    return AGENT_NAME.get(agent, agent) + (f" · {hid}" if hid else "") + (f" · {step}" if step else "")


def _pc(x: Any) -> str:
    """One post-check log entry as a sentence."""
    if not isinstance(x, dict):
        return _t(x, 120)
    cid, a = x.get("claim_id", "a claim"), x.get("action", "")
    if a == "stripped_sources":
        return f"{cid}: removed unknown source(s) {', '.join(x.get('ids', []))}"
    if a == "downgraded_to_reasoning":
        return f"{cid}: no valid source left, so it is now 'reasoning' with confidence capped at 0.5"
    if a == "capped_confidence":
        return f"{cid}: confidence capped"
    return f"{cid}: {a} " + _t({k: v for k, v in x.items() if k not in ('claim_id', 'action')}, 80)


# ---------------------------------------------------------------------------- per step
def narrate_step(agent: str, step: str, hid: str | None, row: dict, delta: dict, artifact: dict, w: dict) -> str:
    """w = the agent's work area AFTER this step (delta already merged)."""
    ms = row.get("ms", 0)
    out = w.get("out")
    if step == "retrieve":
        pack = w.get("pack") or {}
        n = len(pack.get("items", {})) if isinstance(pack, dict) else 0
        web = sum(1 for d in (pack.get("items") or {}).values() if d.get("source") == "web") if n else 0
        body = (f"I searched for evidence and kept {n} document(s)"
                f"{f' ({web} from the web)' if web else ''}. These are the only sources I may cite.")
        if n:
            body += "\nNewest documents:\n" + _docs(pack)
        else:
            body += "\n   ⚠ nothing came back, so the model will have no evidence to cite."
        if agent == "a6_redteam" and w.get("checklist"):
            hits = {k: len(v) for k, v in w["checklist"].items() if v}
            body += f"\nRed-team checklist hits: {hits or 'none'}"
        if agent == "a2_mechanism":
            body += f"\nAlso pulled {len(w.get('assoc', []))} Open Targets associations and {len(w.get('disease_drugs', []))} known drugs."
        return body
    if step == "prompt":
        msgs = w.get("msgs") or []
        chars = sum(len(m["content"]) for m in msgs)
        fb = " It includes feedback from a reviewer / the red team." if "feedback" in row.get("detail", "") else ""
        return f"I wrote the prompt: {len(msgs)} messages, about {chars:,} characters.{fb} Sending it to the model next."
    if step == "llm":
        raw = w.get("raw") or {}
        s = (f"The model ({raw.get('model')}) answered in {raw.get('latency_ms')} ms, "
             f"{raw.get('input_tokens')} tokens in / {raw.get('output_tokens')} out, finish reason '{raw.get('finish_reason')}'.")
        if raw.get("finish_reason") == "length":
            s += "\n   ⚠ the answer was cut off (hit max_tokens); parsing will probably fail."
        text = _t(raw.get("text", ""), 220)
        if text:
            s += f"\nStart of its raw answer: “{text}”"
        r = artifact.get("reasoning") or ""
        if r:
            s += f"\nIts reasoning (first lines): “{_t(r, 300)}”"
        return s
    if step == "parse":
        if w.get("parse_error"):
            return f"The answer was not valid JSON for the schema, so I'll give the model the error and retry once.\n   {_t(w['parse_error'], 240)}"
        return "The answer matches the expected schema. " + _summary_out(agent, out)
    if step == "fix":
        return row.get("detail", "") + ". Calling the model again with that note."
    if step == "postcheck":
        log = w.get("postcheck_log") or []
        if not log:
            return "Citation check: every claim cites a document that was in the evidence pack. Nothing changed."
        return (f"Citation check made {len(log)} change(s): the model cited things it wasn't given, or claimed too much.\n"
                + _list([_pc(x) for x in log], 5))
    if step == "validate":
        p = w.get("problems") or []
        if not p:
            return "The output passes this agent's own quality bars."
        return f"The output failed {len(p)} quality bar(s); I'll send them back to the model once:\n" + _list(p)
    if step == "grade":
        cards = w.get("cards") or []
        rej = w.get("rejected") or []
        s = f"I graded the proposed mechanisms in code (evidence tier, validation level, tractability): {len(cards)} kept, {len(rej)} rejected.\n"
        s += _list([f"#{(w.get('grades', {}).get(c['id'], {}) or {}).get('rank', '?')} {c['id']} · tier {c['evidence_tier']}/{c['validation_level']} · {c['mechanism']}" for c in cards], 6)
        if rej:
            s += "\nRejected:\n" + _list([f"{r.get('hypothesis_id')} {r.get('mechanism')}: {r.get('reason')}"
                                           if isinstance(r, dict) else str(r) for r in rej], 3)
        return s
    if step == "commit":
        return _commit_text(agent, hid, w, row)
    if step == "route":
        return "Red-team routing decision: " + row.get("detail", "")
    return row.get("detail", "")


def _summary_out(agent: str, out: dict | None) -> str:
    if not out:
        return ""
    if agent == "a1_disease":
        return f"It proposes {len(out.get('shifts', []))} shifts and {len(out.get('segments', []))} patient segments."
    if agent == "a2_mechanism":
        return f"It proposes {len(out.get('cards', []))} mechanism cards."
    if agent == "a3_profile":
        return f"It lists {len(out.get('must_haves', []))} must-have(s)."
    if agent == "a6_redteam":
        return f"Its verdict is '{out.get('verdict')}' with {len(out.get('risks', []))} risk(s)."
    return ""


def _commit_text(agent: str, hid: str | None, w: dict, row: dict) -> str:
    out = w.get("out") or {}
    if agent == "a1_disease":
        sh = out.get("shifts", [])
        s = f"Saved. Why now: {len(sh)} shifts in the disease landscape.\n"
        s += _list([f"{x['on']} · {x['type']}: {x['summary']}" for x in sh], 5)
        seg = out.get("segments", [])
        if seg:
            s += "\nPatient segments: " + ", ".join(x["name"] for x in seg)
        return s
    if agent == "a2_mechanism":
        cards = w.get("cards", [])
        return (f"Saved {len(cards)} ranked hypothesis cards; they will now go through A3 → A6 one at a time.\n"
                + _list([f"{c['id']}: {c['mechanism']} (targets {', '.join(c['target_ids'])}, {c['direction']})" for c in cards], 6))
    if agent == "a3_profile":
        return ("Saved the product profile. The product must have:\n" + _list(out.get("must_haves", []))
                + f"\nGap in standard of care at launch: {_t(out.get('soc_gap_at_launch', ''), 200)}")
    if agent == "a6_redteam":
        s = f"The red team says: {out.get('verdict', '?').upper()}.\n"
        s += _list([f"{r['text']} → kill if: {r['kill_criterion']} → cheapest test: {r['cheapest_test']}" for r in out.get("risks", [])], 3)
        if out.get("premortem"):
            s += f"\nPre-mortem: {_t(out['premortem'], 220)}"
        return s
    return row.get("detail", "")


# ---------------------------------------------------------------------------- non-step nodes
def narrate_node(node: str, row: dict | None, state: dict | None = None) -> str:
    detail = (row or {}).get("detail", "")
    if node == "setup":
        return f"Starting the run: {detail}."
    if node == "next_card":
        return f"Picking the next hypothesis. {detail}."
    if node.startswith("review_"):
        return "Review checkpoint passed (single-shot mode, or already approved)."
    if node == "report":
        return "Writing the report, workbook and result files."
    return detail or "done"


# ---------------------------------------------------------------------------- output
def format_msg(who: str, text: str, ms: int | None = None, stamp: str | None = None) -> str:
    head = f"[{stamp or time.strftime('%H:%M:%S')}] **{who}**" + (f"  _{ms} ms_" if ms else "")
    body = "\n".join("    " + ln if i else ln for i, ln in enumerate(text.splitlines()))
    return f"{head}\n    {body}\n"


def chat_path(run_id: str) -> Path:
    return RUNS / run_id / "chat.md"


def append_chat(run_id: str, md: str) -> None:
    try:
        p = chat_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(md + "\n")
    except OSError:
        pass  # best effort, like the step files


def say(writer, run_id: str, who: str, icon: str, node: str, text: str, ms: int | None = None,
        hid: str | None = None) -> dict:
    """Emit one chat message: live stream + chat.md. Returns the entry (steps also keep it in state['chat'])."""
    entry = {"who": who, "icon": icon, "node": node, "hid": hid, "ms": ms, "text": text,
             "t": time.strftime("%H:%M:%S")}
    append_chat(run_id, format_msg(f"{icon} {who}", text, ms, entry["t"]))
    if writer:
        writer({"event": "narration", **entry})
    return entry


def transcript(entries: list[dict]) -> str:
    """Chat entries as readable markdown (what Langflow shows when you click a node's 'What happened' output)."""
    return "\n".join(format_msg(f"{e['icon']} {e['who']}", e["text"], e.get("ms"), e.get("t")) for e in entries) \
        or "(nothing ran in this node yet)"


def log_full(run_id: str, node: str, hid: str | None, row: dict, delta: dict, artifact: dict) -> None:
    """Log EVERYTHING a node did, untruncated: runs/<run_id>/log.md (readable) and log.jsonl (machine).

    The *_llm node also gets the full prompt messages, the model's raw text and its reasoning."""
    import json

    try:
        d = RUNS / run_id
        d.mkdir(parents=True, exist_ok=True)
        rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "node": node, "hid": hid, **row, "changed": delta, **artifact}
        with (d / "log.jsonl").open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        md = [f"## [{rec['t']}] {node}{' · ' + hid if hid else ''} · {row.get('ms', 0)} ms", "", row.get("detail", ""), ""]
        for m in artifact.get("messages") or []:
            md += [f"### prompt · {m['role']}", "", "```", m["content"], "```", ""]
        raw = (delta.get("raw") or {}).get("text")
        if raw:
            md += ["### raw model answer", "", "```", raw, "```", ""]
        if artifact.get("reasoning"):
            md += ["### model reasoning", "", "```", artifact["reasoning"], "```", ""]
        keep = {k: v for k, v in delta.items() if k not in ("raw", "msgs", "pack")}
        if keep:
            md += ["### what this step changed", "", "```json", json.dumps(keep, indent=1, default=str), "```", ""]
        if "pack" in delta:
            items = (delta["pack"] or {}).get("items", {})
            md += [f"### evidence pack ({len(items)} documents)", ""] + \
                  [f"- {k} · {str(v.get('source_date', ''))[:10]} · {v.get('title', '')}" for k, v in items.items()] + [""]
        with (d / "log.md").open("a") as f:
            f.write("\n".join(md) + "\n")
    except OSError:
        pass  # best effort
