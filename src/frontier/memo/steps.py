"""Agent 8 (the memo) as visible steps, like every other agent.

Each step is one LangGraph node and one Langflow node, and reports to the live monitor what it read,
what it decided and why:

    read      what the run gave agent 8 (and what is missing)
    score     upside / risk rating and the reasons, per surviving hypothesis (code)
    pick      the menu of ways in, the 2-3 pursuits chosen, the caution check, the catalysts (code)
    prompt    the exact messages the memo model would get (the rules plus the brief)
    llm       the model call (none in a stub run)
    check     the rules applied to the model's wording: names, numbers, citations, order
    merge     model wording, or the template wording when the model failed the rules
    charts    the charts
    docx      the Word memo
    pdf       the PDF (needs LibreOffice)
    xlsx      the program table
    commit    the files, the summary

`check` can send the run back to `llm` once with the rule breaks listed (the same retry a8_memo.run
makes). Agent 8 never gets a search client and never uses the web: it reads only the run's own saved
outputs. Nothing here depends on the monitor; with send=None every step is silent.

`work` is the one small JSON dict the steps pass along (the graph keeps it in state["a8"]).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from frontier.memo import a8_memo, charts, export_xlsx, render_docx, render_pdf
from frontier.memo.candidates import candidates
from frontier.memo.draft import MemoDraft, catalysts, caution_for, choose, draft_from_numbers
from frontier.memo.inputs import from_graph_state
from frontier.memo.models import MemoInput
from frontier.memo.scoring import scorecards
from frontier.memo.sections import build_outline

STEPS = [
    "read",
    "score",
    "pick",
    "prompt",
    "llm",
    "check",
    "merge",
    "charts",
    "docx",
    "pdf",
    "xlsx",
    "commit",
]
MAX_ATTEMPTS = 2  # the model gets one retry, with the rule breaks listed

Send = Callable[..., None]
RULES = [
    "pursuits, names, forms and order are exactly the code's",
    "every company, drug and name must appear in the agents' output",
    "every number must appear in the agents' output (small counts excepted)",
    "an evidence line must cite a claim of its own pursuit that the citation check kept",
    "a reasoning line may not cite anything",
    "at most 4 bullets per list",
]


# ---- helpers shared with run_memo ------------------------------------------------------------
class NoRetrieval:
    """A8 reads the MemoInput and nothing else: no retrieval, no search, no web."""

    def __getattr__(self, name: str):
        raise RuntimeError(f"agent 8 must not use retrieval ({name})")


def _model_of(backend, agent: str) -> str:
    try:
        return backend.model_for(agent)
    except Exception:  # noqa: BLE001 - the stub backend has no model
        return "stub (no model call)"


class ObservedBackend:
    """Wraps a model backend so each call is reported to the monitor (messages, answer)."""

    def __init__(self, inner, send: Send) -> None:
        self.inner, self.send, self.calls = inner, send, 0

    async def complete(self, *, run_id, agent, messages, schema):
        self.calls += 1
        fmt = {"type": "json_schema", "name": getattr(schema, "__name__", "schema")}
        self.send(
            "request",
            {
                "model": _model_of(self.inner, agent),
                "messages": messages,
                "response_format": fmt,
                "attempt": self.calls,
            },
        )
        try:
            raw = await self.inner.complete(
                run_id=run_id, agent=agent, messages=messages, schema=schema
            )
        except Exception as exc:  # noqa: BLE001 - reported, then re-raised for the step to handle
            self.send("response", {"finish_reason": f"error: {type(exc).__name__}", "usage": None})
            raise
        self.send("delta", {"channel": "output", "text": raw})
        self.send("response", {"finish_reason": "stop", "usage": None})
        return raw


def backend_for(live: bool):
    from frontier.core.config import Settings
    from frontier.core.context import StubLLM

    if not live:
        return StubLLM()
    from frontier.core.router import Router

    return Router.from_settings(Settings())


def _is_live(state: dict) -> bool:
    return (state.get("llm") or {}).get("mode") == "live"


def _inp(state: dict) -> MemoInput:
    return from_graph_state(dict(state))


def _say(send: Send | None, **inputs) -> None:
    if send:
        send("inputs", {"inputs": inputs})


# ---- the steps -------------------------------------------------------------------------------
async def _read(state, work, run_dir, send):
    inp = _inp(state)
    status = inp.claim_status()
    present = {
        "A1 why now": bool(inp.why_now),
        "A2 mechanisms": len(inp.hypotheses),
        "A3 product profiles": sum(1 for h in inp.hypotheses if h.profile),
        "A4 inactive assets": sum(1 for h in inp.hypotheses if h.inactive),
        "A5 competitor landscapes": sum(1 for h in inp.hypotheses if h.landscape),
        "A6 red-team verdicts": sum(1 for h in inp.hypotheses if h.verdict),
        "A7 judge rulings": sum(1 for h in inp.hypotheses if h.judgment),
        "citation-check rows": len(inp.ledger),
    }
    _say(
        send,
        **{
            "agent 8 reads": "the run's saved agent outputs only (no search, no web)",
            "evidence window": inp.evidence_window,
            "what it has": present,
            "hypotheses": f"{len(inp.hypotheses)} ({len(inp.survivors)} survived, "
            f"{len(inp.killed)} killed)",
            "decisions logged": len(inp.decisions),
            "claims checked": len(status),
            "claims verified": sum(1 for v in status.values() if v == "verified"),
            "claims partly supported": sum(1 for v in status.values() if v == "partly"),
            "claims removed (never cited)": sum(1 for v in status.values() if v == "removed"),
            "gaps stated in the memo": inp.gaps or "none",
        },
    )
    work.update(
        attempt=0,
        messages=[],
        text=None,
        problems=[],
        retry=False,
        fallback=None,
        files={},
        charts={},
        draft=None,
    )
    return work, (
        f"read {len(inp.hypotheses)} hypotheses, {len(status)} checked claims, "
        f"{len(inp.gaps)} gap(s); window {inp.evidence_window}"
    )


async def _score(state, work, run_dir, send):
    inp = _inp(state)
    cards = scorecards(inp)
    _say(
        send,
        **{
            "how it scores": "code, not the model: upside and risk are points from the evidence "
            "tier, crowding, an asset that fits, red-team objections and citation support",
            "survivors, best first": [
                {
                    "hypothesis": f"{c.hypothesis_id} {c.mechanism}",
                    "evidence": f"tier {c.tier}, {c.level}",
                    "crowding": c.crowding,
                    "upside": c.upside,
                    "risk": c.risk,
                    "verdict": c.return_vs_risk,
                    "why the upside": c.upside_drivers,
                    "why the risk": c.risk_drivers,
                    "already approved on this mechanism": c.approved or "no",
                    "claims fully supported": f"{c.claims_supported} of {c.claims_checked}",
                }
                for c in cards
            ],
            "killed by the red team": [f"{k.hypothesis_id} {k.mechanism}" for k in inp.killed]
            or "none",
        },
    )
    top = (
        f"; top: {cards[0].hypothesis_id} (upside {cards[0].upside}, risk {cards[0].risk})"
        if cards
        else ""
    )
    return work, f"scored {len(cards)} surviving hypotheses{top}"


async def _pick(state, work, run_dir, send):
    inp = _inp(state)
    cards = scorecards(inp)
    menu = candidates(inp)
    picks = choose(inp, cards, menu)
    caution = caution_for(inp, picks)
    dated = catalysts(inp)
    _say(
        send,
        **{
            "the menu of ways in": {
                hid: [
                    f"{c.form}: {c.name} (fit {c.fit if c.fit is not None else 'n/a'}) "
                    f"- {c.why_listed}"
                    for c in cs
                ]
                or "nothing clears the fit bar"
                for hid, cs in menu.items()
            },
            "chosen by code (one per top hypothesis, no repeated asset)": [
                f"{c.form}: {c.name} (hypothesis {s.hypothesis_id}, upside {s.upside}, "
                f"risk {s.risk})"
                for s, c in picks
            ],
            "proceed with caution": caution.proceed_with_caution,
            "caution reasons": caution.reasons or "none: every pursuit clears the quality bars",
            "judged against": caution.basis,
            "catalysts in the next 12 months": [f"{c.on}: {c.what}" for c in dated] or "none",
        },
    )
    work["picked"] = [c.id for _, c in picks]
    flag = "PROCEED WITH CAUTION" if caution.proceed_with_caution else "clears the bars"
    return work, f"picked {len(picks)} pursuit(s): " + "; ".join(
        f"{c.form} {c.name}" for _, c in picks
    ) + f" · {flag}"


async def _prompt(state, work, run_dir, send):
    from frontier.core.context import load_prompt

    inp = _inp(state)
    base = draft_from_numbers(inp)
    brief = a8_memo.build_brief(inp, base)
    messages = [
        {"role": "system", "content": load_prompt(a8_memo.AGENT)},
        {"role": "user", "content": "BRIEF:\n" + json.dumps(brief, default=str, indent=1)},
    ]
    work["messages"], work["attempt"] = messages, 0
    if send:
        send("messages", {"messages": messages})
    claims = sum(len(p["claims"]) for p in brief["pursuits"])
    _say(
        send,
        **{
            "the model may use": "only the brief below: pursuits, scores, red-team risks, "
            "dated catalysts, and the claims it may cite (the citation check's removed claims "
            "are left out)",
            "pursuits in the brief": [p["candidate_id"] for p in brief["pursuits"]],
            "citable claims": claims,
            "brief size (characters)": len(messages[1]["content"]),
            "the model is told": [
                "use only the BRIEF",
                "cite only listed claims",
                "do not change names, ratings, the caution flag or the order",
            ],
        },
    )
    return work, f"brief ready: {len(brief['pursuits'])} pursuits, {claims} citable claims"


async def _llm(state, work, run_dir, send):
    from frontier.core.context import RunContext, SchemaError, StubLLM

    if not _is_live(state):
        work.update(text=None, fallback="stub mode: no model was called", retry=False)
        _say(send, **{"model call": "none: this is a stub run, so the memo uses template wording"})
        return work, "no model call (stub run): template wording will be used"
    inp = _inp(state)
    inner = backend_for(True)
    backend = ObservedBackend(inner, send) if send and not isinstance(inner, StubLLM) else inner
    ctx = RunContext(
        config=inp.config,
        retrieval=NoRetrieval(),
        backend=backend,
        run_id=str(state.get("run_id") or inp.run_id),
    )
    work["attempt"] = int(work.get("attempt") or 0) + 1
    try:
        text = await ctx.llm(
            agent=a8_memo.AGENT, messages=work["messages"], schema=a8_memo.MemoText
        )
    except SchemaError as err:  # ctx.llm already retried once with the validation error
        work.update(
            text=None,
            retry=False,
            fallback=f"model reply was not valid memo JSON: {str(err)[:160]}",
        )
        return work, "the model's reply was not valid memo JSON: template wording will be used"
    except Exception as exc:  # noqa: BLE001 - a model or network problem must not lose the memo
        work.update(text=None, retry=False, fallback=f"model call failed: {str(exc)[:160]}")
        return work, f"the model call failed ({type(exc).__name__}): template wording will be used"
    work["text"] = text.model_dump_json()
    return work, f"attempt {work['attempt']}: the model wrote {len(work['text'])} characters"


async def _check(state, work, run_dir, send):
    raw = work.get("text")
    work["retry"] = False
    if raw is None:
        work["problems"] = []
        _say(
            send,
            **{
                "rules checked": "none: there is no model wording to check",
                "so": work.get("fallback") or "template wording",
            },
        )
        return work, "nothing to check: template wording"
    inp = _inp(state)
    base = draft_from_numbers(inp)
    brief = a8_memo.build_brief(inp, base)
    text = a8_memo.MemoText.model_validate_json(raw)
    problems = a8_memo.check(text, inp, base, brief)
    work["problems"] = problems
    attempt = int(work.get("attempt") or 1)
    if not problems:
        _say(send, **{"rules checked": RULES, "result": f"all passed on attempt {attempt}"})
        return work, f"all {len(RULES)} rules passed (attempt {attempt})"
    if attempt < MAX_ATTEMPTS:
        work["messages"] = [
            *work["messages"],
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": "Your memo broke these rules. Fix them and reply with the full "
                "JSON again, changing nothing else:\n- " + "\n- ".join(problems[:12]),
            },
        ]
        work["retry"] = True
        _say(
            send,
            **{
                "rules checked": RULES,
                "rule breaks": problems[:12],
                "so": "the model gets one retry with these listed",
            },
        )
        return work, f"{len(problems)} rule break(s); the model gets one retry"
    work["text"] = None
    work["fallback"] = "model wording rejected twice: " + "; ".join(problems[:3])
    _say(
        send,
        **{
            "rules checked": RULES,
            "rule breaks": problems[:12],
            "so": "rejected twice: the template wording is used instead",
        },
    )
    return work, f"{len(problems)} rule break(s) on the retry: template wording will be used"


async def _merge(state, work, run_dir, send):
    inp = _inp(state)
    base = draft_from_numbers(inp)
    if work.get("text") and not work.get("problems"):
        draft = a8_memo.merge(a8_memo.MemoText.model_validate_json(work["text"]), base)
    else:
        draft = base.model_copy(update={"fallback_reason": work.get("fallback")})
    work["draft"] = draft.model_dump_json()
    _say(
        send,
        **{
            "wording written by": draft.written_by,
            "fallback reason": draft.fallback_reason or "none",
            "bottom line": [s.text for s in draft.bottom_line],
            "pursuits as written": [
                {
                    "pursuit": f"{p.form}: {p.name}",
                    "hook": p.hook,
                    "the move": p.the_move,
                    "next step": p.next_step,
                    "upside / risk": f"{p.upside} / {p.risk}",
                }
                for p in draft.pursuits
            ],
            "caution": draft.caution_reasons or "none",
        },
    )
    return work, f"wording written by {draft.written_by}" + (
        f" ({draft.fallback_reason})" if draft.fallback_reason else ""
    )


def _draft(work) -> MemoDraft:
    return MemoDraft.model_validate_json(work["draft"])


async def _charts(state, work, run_dir, send):
    inp, draft = _inp(state), _draft(work)
    pngs = await asyncio.to_thread(charts.all_charts, inp, draft, Path(run_dir) / "charts")
    work["charts"] = {k: str(v) for k, v in pngs.items() if v}
    _say(send, **{"charts drawn": sorted(work["charts"]), "folder": str(Path(run_dir) / "charts")})
    return work, f"{len(work['charts'])} chart(s): " + ", ".join(sorted(work["charts"]))


async def _docx(state, work, run_dir, send):
    inp, draft = _inp(state), _draft(work)
    outline = build_outline(inp, draft, {k: Path(v) for k, v in (work.get("charts") or {}).items()})
    path = await asyncio.to_thread(render_docx.render, outline, Path(run_dir) / "memo.docx")
    work.setdefault("files", {})["docx"] = Path(path).name
    heads = [b.text for b in outline.blocks if type(b).__name__ == "Heading"]
    _say(send, **{"file": str(path), "sections": heads})
    return work, f"memo.docx written: {len(heads)} headings"


async def _pdf(state, work, run_dir, send):
    docx = Path(run_dir) / "memo.docx"
    path = await asyncio.to_thread(render_pdf.to_pdf, docx)
    if path is None:
        _say(
            send,
            **{
                "pdf": "skipped: LibreOffice not found",
                "fix": "brew install --cask libreoffice (memo.docx is still written)",
            },
        )
        return work, "skipped: LibreOffice not found (memo.docx is still written)"
    work.setdefault("files", {})["pdf"] = Path(path).name
    _say(send, **{"file": str(path)})
    return work, "memo.pdf written"


async def _xlsx(state, work, run_dir, send):
    inp = _inp(state)
    path = await asyncio.to_thread(export_xlsx.export, inp, Path(run_dir) / "programs.xlsx")
    work.setdefault("files", {})["xlsx"] = Path(path).name
    sheets: list[str] = []
    try:
        from openpyxl import load_workbook

        sheets = load_workbook(path, read_only=True).sheetnames
    except Exception:  # noqa: BLE001 - the sheet list is only for the monitor
        pass
    _say(send, **{"file": str(path), "sheets": sheets})
    return work, "programs.xlsx written" + (f": sheets {', '.join(sheets)}" if sheets else "")


async def _commit(state, work, run_dir, send):
    files = dict(work.get("files") or {})
    draft = _draft(work) if work.get("draft") else None
    _say(
        send,
        **{
            "files written": files,
            "folder": str(run_dir),
            "wording by": draft.written_by if draft else "n/a",
            "pursuits": [f"{p.form}: {p.name}" for p in draft.pursuits] if draft else [],
            "proceed with caution": draft.proceed_with_caution if draft else None,
        },
    )
    return work, "memo written: " + ", ".join(files.values()) if files else "no memo was written"


_STEP_FN = {
    "read": _read,
    "score": _score,
    "pick": _pick,
    "prompt": _prompt,
    "llm": _llm,
    "check": _check,
    "merge": _merge,
    "charts": _charts,
    "docx": _docx,
    "pdf": _pdf,
    "xlsx": _xlsx,
    "commit": _commit,
}
_NEEDS_DRAFT = {"charts", "docx", "pdf", "xlsx"}


async def run_step(
    name: str, state: dict, work: dict, run_dir: Path, send: Send | None = None
) -> tuple[dict, str]:
    """Run one step. Returns (work, one-line detail). A step before `merge` that fails leaves the
    run without a memo; the error is kept in work["error"] and the later steps skip."""
    work = dict(work or {})
    if name != "read" and work.get("error") and name != "commit":
        return work, f"skipped: an earlier step failed ({work['error']})"
    if name in _NEEDS_DRAFT and not work.get("draft"):
        return work, "skipped: there is no draft to render"
    if name == "pdf" and not (Path(run_dir) / "memo.docx").exists():
        return work, "skipped: there is no memo.docx to convert"
    return await _STEP_FN[name](state, work, Path(run_dir), send)


def route_after_check(work: dict | None) -> str:
    """Where the graph goes after `check`: back to the model once, or on to `merge`."""
    return "llm" if (work or {}).get("retry") else "merge"


async def run_all(
    state: dict,
    run_dir: Path,
    observe: Callable[[str], Send] | None = None,
    skip: frozenset[str] = frozenset(),
) -> dict:
    """All steps in order, with the check -> llm retry; for callers outside the graph.
    `observe(node)` returns a monitor emitter for that node (or None for silence)."""
    work: dict = {}
    i = 0
    while i < len(STEPS):
        name = STEPS[i]
        if name in skip:
            i += 1
            continue
        send = observe(f"a8_{name}") if observe else None
        if send:
            send("start")
        try:
            work, detail = await run_step(name, state, work, run_dir, send)
            if send:
                send("end", {"detail": detail})
        except Exception as exc:  # noqa: BLE001 - the memo must not take the run down with it
            work["error"] = f"{type(exc).__name__}: {exc}"
            if send:
                send("error", {"error": work["error"]})
        if name == "check" and route_after_check(work) == "llm":
            i = STEPS.index("llm")
            continue
        i += 1
    return work
