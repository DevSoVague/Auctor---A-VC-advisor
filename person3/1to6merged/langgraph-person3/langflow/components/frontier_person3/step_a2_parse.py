import os
import sys

# Person-3 bridge lives in langflow/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "person3/1to6merged/langgraph-person3/langflow")


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


class P3A2Parse(Component):
    display_name = "P3 · A2.4 Parse"
    description = "A2 Mechanism: JSON-schema check of the raw output. On a schema error: one retry with the error appended."
    icon = "braces"
    name = "P3A2Parse"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous node."),
        MessageTextInput(name="model", display_name="Model for this step",
                      value="inherit", advanced=True, info="inherit = Setup; or enter any provider/local model ID."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_step")]

    async def run_step(self) -> Data:
        lf_graph = _load_lf()

        st, status = await lf_graph.execute(self.state.data, "a2", "parse", model=self.model)
        # click the node: its status is what happened here, in plain language (full log: runs/<run_id>/log.md)
        self.status = lf_graph.chat_text(self.state.data, st) or status
        return Data(data=st)
