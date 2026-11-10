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
from lfx.io import HandleInput, MessageTextInput, Output
from lfx.schema.data import Data


class AuctorA1Disease(Component):
    display_name = "Auctor · A1 Disease Analysis"
    description = "Q1 Why now: dated shifts (incl. negative) and patient segments by inclusion criteria."
    icon = "calendar-clock"
    name = "AuctorA1Disease"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous node."),
        MessageTextInput(name="feedback", display_name="Reviewer feedback", advanced=True,
                         info="Optional comment; appended to the prompt (rerun with feedback)."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_agent")]

    async def run_agent(self) -> Data:
        b = _bridge()
        from agents.analysis import a1_disease

        st = __import__("copy").deepcopy(self.state.data)
        ctx = b.make_ctx(st, self.feedback)
        out = await a1_disease.run(ctx.config, ctx)
        st["why_now"] = out.model_dump(mode="json")
        b.finish(st, ctx)
        self.status = f"{len(out.shifts)} shifts, {len(out.segments)} segments · {b.usage_line(st)}"
        return Data(data=st)
