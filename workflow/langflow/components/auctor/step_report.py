import os
import sys


def _load_lf():
    """Put langflow/ on sys.path, then import the bridge. Done inside a function because Langflow runs only
    imports, assignments and defs from a component's code when it is loaded from a flow (not `if` blocks).
    The bridge lives in workflow/langflow/; run_langflow.sh exports AUCTOR_LANGFLOW_HOME pointing there."""
    home = os.getenv("AUCTOR_LANGFLOW_HOME", "")
    if not home:
        raise RuntimeError("AUCTOR_LANGFLOW_HOME is not set. Start Langflow with workflow/run_langflow.sh "
                           "(or workflow/langflow/run_langflow.sh), or export AUCTOR_LANGFLOW_HOME=<repo>/workflow/langflow.")
    if home not in sys.path:
        sys.path.insert(0, home)
    import lf_graph

    return lf_graph

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, HandleInput, MessageTextInput, MultilineInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class AuctorStepReport(Component):
    display_name = "Auctor · Report + Workbook"
    description = "Writes runs/<run_id>/analysis.xlsx, report.md, steps.md and result.json."
    icon = "file-spreadsheet"
    name = "AuctorStepReport"

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
