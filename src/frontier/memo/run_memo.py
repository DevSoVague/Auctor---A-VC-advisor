"""Build the memo for a finished run, outside the graph (agent 8's API fallback and scripts).

    files = await memo_for_run(values, run_dir, observe=None)

Inside the pipeline agent 8 is twelve graph nodes (a8_read ... a8_commit, see steps.py). This runs
the same twelve steps in order for a caller that has only the finished run's state, so a memo is
the same whichever way it is built. Writes memo.docx, memo.pdf (if LibreOffice is installed) and
programs.xlsx into the run's folder. A model failure of any kind leaves the template memo.
`observe(node)` optionally returns a live-monitor emitter for that node; None means silent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from frontier.memo import steps
from frontier.memo.steps import NoRetrieval, ObservedBackend, Send  # noqa: F401  (re-exported)


async def memo_for_run(
    values: dict,
    run_dir: Path,
    pdf: bool = True,
    observe: Callable[[str], Send] | None = None,
) -> dict[str, str]:
    """Write the memo files for one finished run; returns {kind: file name}."""
    work = await steps.run_all(
        values, Path(run_dir), observe, frozenset() if pdf else frozenset({"pdf"})
    )
    if work.get("error") and not work.get("files"):
        raise RuntimeError(work["error"])
    return dict(work.get("files") or {})
