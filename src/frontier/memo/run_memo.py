"""Build the memo for a finished run (agent 8, step 5).

    files = await memo_for_run(values, run_dir, send=None)

`values` is the pipeline's final state. Writes memo.docx, memo.pdf (if LibreOffice is installed)
and programs.xlsx into the run's folder, next to the workbook and report. The model only
rewrites wording (and only when the run itself was live); a model failure of any kind leaves the
template memo, so a finished run always gets a memo. Agent 8 never gets a search client: its
context holds an object that raises if anything tries to use it.

`send(event, data)` is optional. It is the live monitor's emitter (p3graph.live.observer): when
given, the memo agent reports what it read, what the code picked, the model call (the exact
messages and the answer), the rules it applied and the files it wrote, so it shows up in the
monitor beside the other agents. Nothing here depends on the monitor; with send=None it is silent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from frontier.memo import a8_memo
from frontier.memo.build import write_memo
from frontier.memo.draft import MemoDraft, draft_from_numbers
from frontier.memo.inputs import from_graph_state
from frontier.memo.models import MemoInput

log = logging.getLogger(__name__)

Send = Callable[..., None]


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
        except Exception as exc:  # noqa: BLE001 - reported, then re-raised for a8_memo to handle
            self.send("response", {"finish_reason": f"error: {type(exc).__name__}", "usage": None})
            raise
        self.send("delta", {"channel": "output", "text": raw})
        self.send("response", {"finish_reason": "stop", "usage": None})
        return raw


def _backend(live: bool):
    from frontier.core.config import Settings
    from frontier.core.context import StubLLM

    if not live:
        return StubLLM()
    from frontier.core.router import Router

    return Router.from_settings(Settings())


def reads(inp: MemoInput, draft: MemoDraft) -> dict:
    """What agent 8 read, in numbers (shown in the monitor as the step's inputs)."""
    status = inp.claim_status()
    return {
        "agent 8 reads": "the run's saved agent outputs only (no search, no web)",
        "evidence window": inp.evidence_window,
        "hypotheses": len(inp.hypotheses),
        "survivors": len(inp.survivors),
        "killed": len(inp.killed),
        "decisions logged": len(inp.decisions),
        "claims checked": len(status),
        "claims verified": sum(1 for v in status.values() if v == "verified"),
        "claims partly supported": sum(1 for v in status.values() if v == "partly"),
        "claims removed (never cited)": sum(1 for v in status.values() if v == "removed"),
        "gaps": inp.gaps,
        "code picked": [f"{p.form}: {p.name}" for p in draft.pursuits],
        "proceed with caution": draft.proceed_with_caution,
        "caution reasons": draft.caution_reasons,
    }


async def memo_for_run(
    values: dict, run_dir: Path, pdf: bool = True, send: Send | None = None
) -> dict[str, str]:
    """Write the memo files for one finished run; returns {kind: file name}."""
    from frontier.core.context import RunContext

    say: Send = send or (lambda *_a, **_k: None)
    inp = from_graph_state(values)
    live = (values.get("llm") or {}).get("mode") == "live"
    draft = draft_from_numbers(inp)
    say("inputs", {"inputs": reads(inp, draft)})
    backend = None
    try:
        from frontier.core.context import StubLLM

        inner = _backend(live)
        # a stub run makes no model call (a8_memo checks for StubLLM itself), so only a real
        # backend is wrapped for the monitor
        backend = ObservedBackend(inner, say) if send and not isinstance(inner, StubLLM) else inner
        ctx = RunContext(
            config=inp.config,
            retrieval=NoRetrieval(),
            backend=backend,
            run_id=str(values.get("run_id") or inp.run_id),
        )
        draft = await a8_memo.run(inp, ctx)
    except Exception as exc:  # noqa: BLE001 - the template memo is always available
        log.warning("memo wording failed, using the template: %s", exc)
        draft = draft.model_copy(update={"fallback_reason": f"memo agent failed: {exc}"[:200]})
    say(
        "inputs",
        {
            "inputs": {
                "wording written by": draft.written_by,
                "fallback reason": draft.fallback_reason,
                "model calls": getattr(backend, "calls", 0),
                "rules the code applied": [
                    "pursuits, names, forms and order are the code's",
                    "every name must appear in the agents' output",
                    "every number must appear in the agents' output",
                    "an evidence line must cite a claim the checker did not remove",
                ],
            }
        },
    )
    # charts, Word and the PDF conversion take seconds: keep them off the event loop
    files = await asyncio.to_thread(write_memo, inp, Path(run_dir), draft, pdf)
    out = {k: Path(files[k]).name for k in ("docx", "pdf", "xlsx") if files.get(k)}
    say(
        "inputs",
        {
            "inputs": {
                "files written": out,
                "folder": str(run_dir),
                "pursuits": [f"{p.form}: {p.name}" for p in draft.pursuits],
            }
        },
    )
    return out
