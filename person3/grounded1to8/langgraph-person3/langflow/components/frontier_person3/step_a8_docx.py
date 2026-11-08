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


class P3A8Docx(Component):
    display_name = "P3 · A8.9 Word memo"
    description = "A8 Memo: Writes memo.docx."
    icon = "file-text"
    name = "P3A8Docx"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous node."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_step"),
    ]

    async def _done(self):
        lf_graph = _load_lf()

        if not hasattr(self, "_cache"):
            self._cache = await lf_graph.a8_step(self.state.data, "docx")
        return self._cache

    async def run_step(self) -> Data:
        st, status = await self._done()
        self.status = status
        return Data(data=st)
