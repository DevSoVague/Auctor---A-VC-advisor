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


class P3StepSetup(Component):
    display_name = "P3 · Setup"
    description = "Start a run: disease, as-of date, model, retrieval. First node of the step-by-step flow."
    icon = "settings"
    name = "P3StepSetup"

    inputs = [
        HandleInput(name="message", display_name="Message", input_types=["Message"], required=False,
                    info="Optional chat message. Anything other than 'run' is sent to every agent as an extra "
                         "instruction (like reviewer feedback)."),
        MessageTextInput(name="disease", display_name="Disease", value="COPD"),
        MessageTextInput(name="disease_id", display_name="Disease ID (EFO/MONDO)", value="EFO_0000341"),
        MessageTextInput(name="as_of", display_name="As-of date", value="today",
                         info="'today' uses the current date (default); YYYY-MM-DD selects an explicit cutoff."),
        DropdownInput(name="llm_mode", display_name="LLM mode", options=["stub", "live"], value="stub",
                      info="stub = fixture outputs, no tokens. live = configured provider or LOCAL_LLM_BASE_URL."),
        MessageTextInput(name="model", display_name="Model (all agents)",
                      value="profile default", info="profile default, or an exact provider/local model ID."),
        DropdownInput(name="profile", display_name="Profile", options=["dev", "final"], value="dev"),
        MessageTextInput(name="retrieval", display_name="Retrieval (corpus / RAG)", value="stub",
                         info="'stub' (fixture corpus) or the URL of person 1's retrieval API (the RAG index)."),
        DropdownInput(name="search", display_name="Search source",
                      options=["evstore", "evstore+web", "corpus", "web", "both", "database", "database+web"],
                      value="evstore", info="evstore = the COPD evidence store (../evidence-store on :5433); corpus = "
                      "fixtures; web = Tavily (needs TAVILY_API_KEY)."),
        MessageTextInput(name="pitch", display_name="Startup pitch (path or URL)", value="",
                         info="PDF, DOCX, TXT, MD or URL. A0 gates it (type, size, text, topic, injection); a rejected "
                              "pitch stops the run. Empty = open scan."),
        DropdownInput(name="store_pitch", display_name="Store pitch in shared DB", options=["off", "on"], value="off"),
        DropdownInput(name="tools", display_name="Agent tools (DB/web)", options=["on", "off"], value="on"),
        DropdownInput(name="cite_mode", display_name="A7 citation check", options=["every node", "end only", "off"],
                      value="every node", info="every node = check after each agent, restart on hallucination (max 2); "
                      "end only = agents are not blocked, one check over everything before the report; off = none."),
        DropdownInput(name="judge", display_name="A6b judge decides", options=["on", "off"], value="on"),
        MessageTextInput(name="max_cards", display_name="Max cards (0 = all)", value="1"),
        DropdownInput(name="audience", display_name="Audience", options=["generalist", "specialist"],
                      value="generalist"),
    ]
    outputs = [Output(display_name="Run state", name="state", method="build_state")]

    async def build_state(self) -> Data:
        lf_graph = _load_lf()

        cfg = {"disease": self.disease, "disease_id": self.disease_id, "as_of": self.as_of,
               "mode": "single_shot", "profile": self.profile, "audience": self.audience}
        llm = {"mode": self.llm_mode, "model": None if self.model == "profile default" else self.model,
               "profile": self.profile}
        msg = getattr(self.message, "text", None) if self.message else None
        mode = {"every node": "node", "end only": "end", "off": "off"}.get(self.cite_mode, "node")
        extras = {"tools": self.tools == "on", "cite": mode != "off", "cite_mode": mode, "judge": self.judge == "on",
                  "max_cards": int((self.max_cards or "0").strip() or 0), "pitch": (self.pitch or "").strip() or None,
                  "store_pitch": self.store_pitch == "on"}
        st, status = await lf_graph.setup(cfg, llm, self.retrieval.strip() or "stub", message=msg or "",
                                          search=self.search, extras=extras)
        self.status = lf_graph.chat_text({}, st) or status
        return Data(data=st)
