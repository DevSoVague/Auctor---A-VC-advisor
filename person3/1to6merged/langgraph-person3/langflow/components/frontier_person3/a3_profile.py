import os
import sys

# Person-3 Langflow bridge lives in langflow-person3/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "person3/1to6merged/langgraph-person3/langflow")
if _P3 not in sys.path:
    sys.path.insert(0, _P3)

from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, MessageTextInput, Output
from lfx.schema.data import Data


class P3A3Profile(Component):
    display_name = "P3 · A3 Target Product Profile"
    description = "Q3 Winning product: one TPP per card; SoC gap at launch; 1-2 measurable must-haves."
    icon = "target"
    name = "P3A3Profile"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous Person-3 node."),
        MessageTextInput(name="feedback", display_name="Reviewer feedback", advanced=True,
                         info="Optional comment; appended to the prompt (rerun with feedback)."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_agent")]

    async def run_agent(self) -> Data:
        import lf_bridge as b
        from agents.analysis import a3_profile
        from agents.analysis.schemas import HypothesisCard

        st = __import__("copy").deepcopy(self.state.data)
        if not st.get("cards"):
            raise ValueError("A3 needs A2's cards: connect P3 · A2 first")
        ctx = b.make_ctx(st, self.feedback)
        for c in st["cards"]:  # one hypothesis at a time
            prof = await a3_profile.run(HypothesisCard.model_validate(c), ctx)
            st["profiles"][c["id"]] = prof.model_dump(mode="json")
        b.finish(st, ctx)
        self.status = f"{len(st['profiles'])} TPPs · {b.usage_line(st)}"
        return Data(data=st)
