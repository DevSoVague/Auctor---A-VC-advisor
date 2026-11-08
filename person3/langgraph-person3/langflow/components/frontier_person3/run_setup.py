import os
import sys

# Person-3 Langflow bridge lives in langflow-person3/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "person3/langgraph-person3/langflow")
if _P3 not in sys.path:
    sys.path.insert(0, _P3)

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MessageTextInput, Output
from lfx.schema.data import Data


class P3RunSetup(Component):
    display_name = "P3 · Run Setup"
    description = "Start a Person-3 analysis run (disease, as-of cutoff, model, retrieval)."
    icon = "settings"
    name = "P3RunSetup"

    inputs = [
        MessageTextInput(name="disease", display_name="Disease", value="COPD"),
        MessageTextInput(name="disease_id", display_name="Disease ID (EFO/MONDO)", value="EFO_0000341"),
        MessageTextInput(name="as_of", display_name="As-of date", value="2021-01-01",
                         info="YYYY-MM-DD, or 'today' for the live run. 2021-01-01 = backtest."),
        DropdownInput(name="llm_mode", display_name="LLM mode", options=["stub", "live"], value="stub",
                      info="stub = fixture outputs, no tokens. live = Nebius Token Factory."),
        DropdownInput(name="model", display_name="Model (all agents)",
                      options=["profile default", "openai/gpt-oss-120b",
                               "deepseek-ai/DeepSeek-V4-Flash-0731", "Qwen/Qwen3.5-397B-A17B",
                               "zai-org/GLM-5.1"], value="profile default"),
        DropdownInput(name="profile", display_name="Profile", options=["dev", "final"], value="dev"),
        MessageTextInput(name="retrieval", display_name="Retrieval", value="stub",
                         info="'stub' (fixture corpus) or person 1's retrieval API URL."),
        DropdownInput(name="audience", display_name="Audience", options=["generalist", "specialist"],
                      value="generalist"),
    ]
    outputs = [Output(display_name="Run state", name="state", method="build_state")]

    def build_state(self) -> Data:
        import lf_bridge as b

        st = b.new_state(disease=self.disease, disease_id=self.disease_id, as_of=self.as_of,
                         llm_mode=self.llm_mode, model=self.model, profile=self.profile,
                         retrieval=self.retrieval, audience=self.audience)
        self.status = f"run {st['run_id'][:8]} · as_of {st['config']['as_of']} · {self.llm_mode}"
        return Data(data=st)
