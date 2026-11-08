import os
import sys

# Person-3 bridge lives in langflow/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "/Users/sovague/Desktop/ctrl+alt+cure/upmc/langgraph-person3/langflow")


def _load_lf():
    """Put langflow/ on sys.path, then import the bridge. Done inside a function because Langflow runs only
    imports, assignments and defs from a component's code when it is loaded from a flow (not `if` blocks)."""
    if _P3 not in sys.path:
        sys.path.insert(0, _P3)
    import lf_graph

    return lf_graph

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, HandleInput, MessageTextInput, MultilineInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class P3StepA8Memo(Component):
    display_name = "P3 · A8 Memo"
    description = ("Agent 8: writes the investor memo (Word, PDF, Excel) from the run's own checked output. "
                   "It never searches. It reports to the monitor.")
    icon = "file-text"
    name = "P3StepA8Memo"

    inputs = [HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True)]
    outputs = [
        Output(display_name="Report + memo", name="summary", method="build_summary"),
        Output(display_name="Run state", name="state_out", method="build_state"),
    ]

    async def _done(self):
        lf_graph = _load_lf()

        if not hasattr(self, "_cache"):
            self._cache = await lf_graph.a8_memo(self.state.data)
        return self._cache

    async def build_summary(self) -> Message:
        st, status = await self._done()
        self.status = status
        files = st.get("memo") or {}
        memo = ("\n\n## Memo (agent 8)\n" + "\n".join(f"- {k}: runs/{st['run_id']}/{v}" for k, v in files.items())
                if files else f"\n\n## Memo (agent 8)\nNot written: {st.get('memo_error') or 'unknown reason'}")
        return Message(text=st.get("report", "") + memo)

    async def build_state(self) -> Data:
        st, _ = await self._done()
        return Data(data=st)
