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


class AuctorA2Mechanism(Component):
    display_name = "Auctor · A2 Mechanistic Analysis"
    description = "Q2 Which mechanism: 3-5 ranked hypothesis cards; tier/level graded in code."
    icon = "dna"
    name = "AuctorA2Mechanism"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous node."),
        MessageTextInput(name="feedback", display_name="Reviewer feedback", advanced=True,
                         info="Optional comment; appended to the prompt (rerun with feedback)."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_agent")]

    async def run_agent(self) -> Data:
        b = _bridge()
        from agents.analysis import a2_mechanism
        from agents.analysis.schemas import WhyNow

        st = __import__("copy").deepcopy(self.state.data)
        if not st.get("why_now"):
            raise ValueError("A2 needs A1's output: connect Auctor · A1 first")
        ctx = b.make_ctx(st, self.feedback)
        cards = await a2_mechanism.run(WhyNow.model_validate(st["why_now"]), ctx)
        st["cards"] = [c.model_dump(mode="json") for c in cards]
        st["grades"] = ctx.signals.get("a2_grades", {})
        b.finish(st, ctx)
        self.status = " · ".join(f"{c.id} t{c.evidence_tier}/{c.validation_level}" for c in cards)
        return Data(data=st)
