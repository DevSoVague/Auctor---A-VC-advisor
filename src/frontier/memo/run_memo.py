"""Build the memo for a finished run (agent 8, step 5).

    files = await memo_for_run(values, run_dir)

`values` is the pipeline's final state. Writes memo.docx, memo.pdf (if LibreOffice is installed)
and programs.xlsx into the run's folder, next to the workbook and report. The model only
rewrites wording (and only when the run itself was live); a model failure of any kind leaves the
template memo, so a finished run always gets a memo. Agent 8 never gets a search client: its
context holds an object that raises if anything tries to use it.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from frontier.memo import a8_memo
from frontier.memo.build import write_memo
from frontier.memo.draft import draft_from_numbers
from frontier.memo.inputs import from_graph_state

log = logging.getLogger(__name__)


class NoRetrieval:
    """A8 reads the MemoInput and nothing else: no retrieval, no search, no web."""

    def __getattr__(self, name: str):
        raise RuntimeError(f"agent 8 must not use retrieval ({name})")


def _backend(live: bool):
    from frontier.core.config import Settings
    from frontier.core.context import StubLLM

    if not live:
        return StubLLM()
    from frontier.core.router import Router

    return Router.from_settings(Settings())


async def memo_for_run(values: dict, run_dir: Path, pdf: bool = True) -> dict[str, str]:
    """Write the memo files for one finished run; returns {kind: file name}."""
    from frontier.core.context import RunContext

    inp = from_graph_state(values)
    live = (values.get("llm") or {}).get("mode") == "live"
    draft = draft_from_numbers(inp)
    try:
        ctx = RunContext(
            config=inp.config,
            retrieval=NoRetrieval(),
            backend=_backend(live),
            run_id=str(values.get("run_id") or inp.run_id),
        )
        draft = await a8_memo.run(inp, ctx)
    except Exception as exc:  # noqa: BLE001 - the template memo is always available
        log.warning("memo wording failed, using the template: %s", exc)
        draft = draft.model_copy(update={"fallback_reason": f"memo agent failed: {exc}"[:200]})
    # charts, Word and the PDF conversion take seconds: keep them off the event loop
    files = await asyncio.to_thread(write_memo, inp, Path(run_dir), draft, pdf)
    return {k: Path(files[k]).name for k in ("docx", "pdf", "xlsx") if files.get(k)}
