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
from lfx.io import DropdownInput, MessageTextInput, Output
from lfx.schema.data import Data


class AuctorRunSetup(Component):
    display_name = "Auctor · Run Setup"
    description = "Start an Auctor analysis run (disease, as-of cutoff, model, retrieval)."
    icon = "settings"
    name = "AuctorRunSetup"

    inputs = [
        MessageTextInput(name="disease", display_name="Disease", value="COPD"),
        MessageTextInput(name="disease_id", display_name="Disease ID (EFO/MONDO)", value="EFO_0000341"),
        MessageTextInput(name="as_of", display_name="As-of date", value="today",
                         info="'today' uses the current date (default); YYYY-MM-DD selects an explicit cutoff."),
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
        b = _bridge()

        st = b.new_state(disease=self.disease, disease_id=self.disease_id, as_of=self.as_of,
                         llm_mode=self.llm_mode, model=self.model, profile=self.profile,
                         retrieval=self.retrieval, audience=self.audience)
        self.status = f"run {st['run_id'][:8]} · as_of {st['config']['as_of']} · {self.llm_mode}"
        return Data(data=st)
