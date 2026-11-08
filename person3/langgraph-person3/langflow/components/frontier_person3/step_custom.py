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


class P3CustomNode(Component):
    display_name = "P3 · Custom Node"
    description = ("Your own step: a function from custom_nodes.py (safety gate, loop, tool call, ...). "
                   "Drop it anywhere in the chain and say which step it follows.")
    icon = "puzzle"
    name = "P3CustomNode"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True),
        MessageTextInput(name="node_name", display_name="Name", value="my_node"),
        DropdownInput(name="after", display_name="Sits after step", options=['a1_retrieve', 'a1_prompt', 'a1_llm', 'a1_parse', 'a1_postcheck', 'a1_validate', 'a1_commit', 'a2_retrieve', 'a2_prompt', 'a2_llm', 'a2_parse', 'a2_grade', 'a2_postcheck', 'a2_commit', 'a3_retrieve', 'a3_prompt', 'a3_llm', 'a3_parse', 'a3_validate', 'a3_postcheck', 'a3_commit', 'a6_retrieve', 'a6_prompt', 'a6_llm', 'a6_parse', 'a6_validate', 'a6_postcheck', 'a6_commit', 'a6_route', 'setup', 'report'],
                      value="a3_postcheck", info="The step wired just before this node. Needed so loops know where to jump back to."),
        MessageTextInput(name="function", display_name="Function", value="custom_nodes:safety_gate",
                         info="file:function in custom_nodes.py. Examples: safety_gate, refine_loop, run_tools."),
        MultilineInput(name="params", display_name="Params (JSON)", value='{"on_fail": "flag"}',
                       info="Same as `params:` in workflow.yaml."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_step")]

    async def run_step(self) -> Data:
        lf_graph = _load_lf()

        st, status = await lf_graph.execute_custom(self.state.data, name=self.node_name, after=self.after,
                                                   node=self.function, params=self.params)
        self.status = lf_graph.chat_text(self.state.data, st) or status
        return Data(data=st)
