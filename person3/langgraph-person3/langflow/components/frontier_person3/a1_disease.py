import os
import sys

# Person-3 Langflow bridge lives in langflow-person3/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "person3/langgraph-person3/langflow")
if _P3 not in sys.path:
    sys.path.insert(0, _P3)

from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, MessageTextInput, Output
from lfx.schema.data import Data


class P3A1Disease(Component):
    display_name = "P3 · A1 Disease Analysis"
    description = "Q1 Why now: dated shifts (incl. negative) and patient segments by inclusion criteria."
    icon = "calendar-clock"
    name = "P3A1Disease"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous Person-3 node."),
        MessageTextInput(name="feedback", display_name="Reviewer feedback", advanced=True,
                         info="Optional comment; appended to the prompt (rerun with feedback)."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_agent")]

    async def run_agent(self) -> Data:
        import lf_bridge as b
        from agents.analysis import a1_disease

        st = __import__("copy").deepcopy(self.state.data)
        ctx = b.make_ctx(st, self.feedback)
        out = await a1_disease.run(ctx.config, ctx)
        st["why_now"] = out.model_dump(mode="json")
        b.finish(st, ctx)
        self.status = f"{len(out.shifts)} shifts, {len(out.segments)} segments · {b.usage_line(st)}"
        return Data(data=st)
