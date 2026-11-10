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


class AuctorStepJoin(Component):
    display_name = "Auctor · Join (A3 + A4 + A5)"
    description = ("A3, A4 and A5 run side by side after A2. This waits for all of them and merges their results "
                   "into one run state for A6.")
    icon = "merge"
    name = "AuctorStepJoin"

    inputs = [
        HandleInput(name="base", display_name="Fork point (A2)", input_types=["Data"], required=True,
                    info="A2's last step: the state the branches started from."),
        HandleInput(name="branches", display_name="Branches", input_types=["Data"], required=True, is_list=True,
                    info="The last step of each branch: A3, A4 and A5."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_step")]

    async def run_step(self) -> Data:
        lf_graph = _load_lf()

        branches = self.branches if isinstance(self.branches, list) else [self.branches]
        st, status = lf_graph.join(self.base.data, [b.data for b in branches if b is not None])
        self.status = status
        return Data(data=st)
