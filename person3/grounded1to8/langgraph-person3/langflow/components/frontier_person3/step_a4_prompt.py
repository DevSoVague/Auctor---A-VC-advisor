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


def _default_prompt() -> str:
    try:
        lf_graph = _load_lf()

        return lf_graph.default_prompt("a4_inactive")
    except Exception:  # noqa: BLE001
        return ""


class P3A4Prompt(Component):
    display_name = "P3 · A4.4 Prompt"
    description = "A4 Inactive assets: Builds the messages: system prompt (editable here) + input + evidence pack + feedback."
    icon = "message-square"
    name = "P3A4Prompt"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous node."),
        MultilineInput(name="system_prompt", display_name="System prompt (edit me)",
                       value=_default_prompt(), info="Edit to change this agent's prompt for the run."),
        MessageTextInput(name="feedback", display_name="Feedback", advanced=True,
                         info="Optional comment appended to the prompt."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_step")]

    async def run_step(self) -> Data:
        lf_graph = _load_lf()

        st, status = await lf_graph.execute(self.state.data, "a4", "prompt", feedback=self.feedback, system_prompt=self.system_prompt)
        # click the node: its status is what happened here, in plain language (full log: runs/<run_id>/log.md)
        self.status = lf_graph.chat_text(self.state.data, st) or status
        return Data(data=st)
