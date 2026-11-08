import os
import sys

# Person-3 Langflow bridge lives in langflow-person3/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "person3/langgraph-person3/langflow")
if _P3 not in sys.path:
    sys.path.insert(0, _P3)

from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, MessageTextInput, Output
from lfx.schema.data import Data


class P3A2Mechanism(Component):
    display_name = "P3 · A2 Mechanistic Analysis"
    description = "Q2 Which mechanism: 3-5 ranked hypothesis cards; tier/level graded in code."
    icon = "dna"
    name = "P3A2Mechanism"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous Person-3 node."),
        MessageTextInput(name="feedback", display_name="Reviewer feedback", advanced=True,
                         info="Optional comment; appended to the prompt (rerun with feedback)."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_agent")]

    async def run_agent(self) -> Data:
        import lf_bridge as b
        from agents.analysis import a2_mechanism
        from agents.analysis.schemas import WhyNow

        st = __import__("copy").deepcopy(self.state.data)
        if not st.get("why_now"):
            raise ValueError("A2 needs A1's output: connect P3 · A1 first")
        ctx = b.make_ctx(st, self.feedback)
        cards = await a2_mechanism.run(WhyNow.model_validate(st["why_now"]), ctx)
        st["cards"] = [c.model_dump(mode="json") for c in cards]
        st["grades"] = ctx.signals.get("a2_grades", {})
        b.finish(st, ctx)
        self.status = " · ".join(f"{c.id} t{c.evidence_tier}/{c.validation_level}" for c in cards)
        return Data(data=st)
