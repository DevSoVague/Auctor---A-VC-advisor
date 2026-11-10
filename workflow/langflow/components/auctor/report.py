import os
import sys


def _bridge():
    """Put langflow/ on sys.path, then import the bridge (inside a function: Langflow runs only imports,
    assignments and defs from a component's code when it is loaded from a flow, not `if` blocks).
    The bridge lives in workflow/langflow/; run_langflow.sh exports AUCTOR_LANGFLOW_HOME pointing there."""
    home = os.getenv("AUCTOR_LANGFLOW_HOME", "")
    if not home:
        raise RuntimeError("AUCTOR_LANGFLOW_HOME is not set. Start Langflow with workflow/langflow/run_langflow.sh, "
                           "or export AUCTOR_LANGFLOW_HOME=<repo>/workflow/langflow.")
    if home not in sys.path:
        sys.path.insert(0, home)
    import lf_bridge

    return lf_bridge


from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class AuctorReport(Component):
    display_name = "Auctor · Report + Workbook"
    description = "Writes runs/<run_id>/analysis.xlsx and a markdown summary of Q1, Q2, Q3, Q5."
    icon = "file-spreadsheet"
    name = "AuctorReport"

    inputs = [HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True)]
    outputs = [
        Output(display_name="Report", name="report", method="build_report"),
        Output(display_name="Run state", name="state_out", method="build_state"),
    ]

    def build_report(self) -> Message:
        b = _bridge()

        st = self.state.data
        path = b.write_workbook(st)
        self.status = path
        return Message(text=b.report_md(st))

    def build_state(self) -> Data:
        return Data(data=self.state.data)
