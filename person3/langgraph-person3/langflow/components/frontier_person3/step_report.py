import os
import sys

# Person-3 bridge lives in langflow/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "person3/langgraph-person3/langflow")


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


class P3StepReport(Component):
    display_name = "P3 · Report + Workbook"
    description = "Writes runs/<run_id>/analysis.xlsx, report.md, steps.md and result.json."
    icon = "file-spreadsheet"
    name = "P3StepReport"

    inputs = [HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True)]
    outputs = [
        Output(display_name="Report", name="report", method="build_report"),
        Output(display_name="Run state", name="state_out", method="build_state"),
    ]

    async def _done(self):
        lf_graph = _load_lf()

        if not hasattr(self, "_cache"):
            self._cache = await lf_graph.report(self.state.data)
        return self._cache

    async def build_report(self) -> Message:
        st, path = await self._done()
        self.status = path
        return Message(text=st["report"])

    async def build_state(self) -> Data:
        st, _ = await self._done()
        return Data(data=st)
