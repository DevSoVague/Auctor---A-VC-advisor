import os
import sys

# Person-3 Langflow bridge lives in langflow-person3/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "person3/1to6merged/langgraph-person3/langflow")
if _P3 not in sys.path:
    sys.path.insert(0, _P3)

from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class P3Report(Component):
    display_name = "P3 · Report + Workbook"
    description = "Writes runs/<run_id>/analysis.xlsx and a markdown summary of Q1, Q2, Q3, Q5."
    icon = "file-spreadsheet"
    name = "P3Report"

    inputs = [HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True)]
    outputs = [
        Output(display_name="Report", name="report", method="build_report"),
        Output(display_name="Run state", name="state_out", method="build_state"),
    ]

    def build_report(self) -> Message:
        import lf_bridge as b

        st = self.state.data
        path = b.write_workbook(st)
        self.status = path
        return Message(text=b.report_md(st))

    def build_state(self) -> Data:
        return Data(data=self.state.data)
