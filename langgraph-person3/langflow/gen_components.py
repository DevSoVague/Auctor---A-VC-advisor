"""Generate one small Langflow component per agent step into components/frontier_person3/.

    conda run -n langflow python gen_components.py

Each component calls lf_graph.execute(), which runs the matching LangGraph step node. Re-run after
changing the step list. Files are named step_<agent>_<step>.py (plus step_setup, step_report, step_custom).
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "components" / "frontier_person3"

AGENTS = {"a1": ("A1 Disease", "a1_disease"), "a2": ("A2 Mechanism", "a2_mechanism"),
          "a3": ("A3 TPP", "a3_profile"), "a4": ("A4 Inactive assets", "a4_inactive"),
          "a5": ("A5 Active landscape", "a5_active"), "a6": ("A6 Red Team", "a6_redteam")}
ORDER = {
    "a1": ["retrieve", "prompt", "toolloop", "llm", "parse", "postcheck", "validate", "cite", "commit"],
    "a2": ["retrieve", "prompt", "toolloop", "llm", "parse", "grade", "postcheck", "cite", "commit"],
    "a3": ["retrieve", "prompt", "toolloop", "llm", "parse", "validate", "postcheck", "cite", "commit"],
    "a4": ["gather", "triage", "establish", "prompt", "llm", "merge", "commit"],
    "a5": ["gather", "shortlist", "establish", "prompt", "llm", "merge", "score", "commit"],
    "a6": ["retrieve", "prompt", "toolloop", "llm", "parse", "validate", "postcheck", "cite", "commit", "judge", "route"],
}
INFO = {
    "retrieve": ("search", "Deterministic evidence retrieval: the only documents the model may cite."),
    "prompt": ("message-square", "Builds the messages: system prompt (editable here) + input + evidence pack + feedback."),
    "llm": ("brain", "ONE model call. Records model, finish_reason (length = truncated), tokens, latency."),
    "parse": ("braces", "JSON-schema check of the raw output. On a schema error: one retry with the error appended."),
    "postcheck": ("shield-check", "Citation check: strips unknown sources, downgrades claims, caps confidence."),
    "validate": ("check-circle", "The agent's own checks. On problems: one retry (fix prompt → model again), then fail loudly."),
    "grade": ("bar-chart", "Evidence tier, validation level and tractability gate, computed in code. Ranks the cards."),
    "commit": ("save", "Emits claims and events, writes this agent's output into the run state."),
    "gather": ("download", "Code: finds the drugs/programs from trials, approvals and ChEMBL, and places each on or off the mechanism."),
    "triage": ("list-filter", "Code: active in the disease → left to A5; the rest → repurposing (approved/elsewhere) or shelved (stopped/stalled)."),
    "shortlist": ("list-filter", "Code: on-mechanism / other-mechanism / for the model to place; most advanced first, capped per group."),
    "establish": ("clipboard-list", "Code: fixes each asset's stage, owner, last activity, flags and the dated records the model may cite."),
    "merge": ("merge", "Code: pairs the model's replies with the assets, re-imposes code's facts, drops unmatched replies, ranks."),
    "score": ("gauge", "Code: crowding score for the mechanism and the trial readouts in the next 12 months."),
    "toolloop": ("wrench", "The agent calls evidence-store / web tools (A1 also ingests new evidence; A6 runs checkers)."),
    "cite": ("book-check", "A7 citation check: every claim vs its source. Hallucination restarts this agent (max 2), then strips it."),
    "judge": ("scale", "A6b judge: reads only A3's profile and A6's attack, decides advance / revise / kill."),
    "route": ("git-branch", "Red-team decision: revise once (reruns A3 and A6 for that card), kill, or survive."),
}
MODELS = ["inherit", "openai/gpt-oss-120b", "deepseek-ai/DeepSeek-V4-Flash-0731", "Qwen/Qwen3.5-397B-A17B",
          "zai-org/GLM-5.1", "nvidia/Nemotron-3_5-Lightning"]

HEAD = '''import os
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
'''

STATE_IN = '''        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous node."),
'''


def step_file(a: str, step: str) -> str:
    title, agent = AGENTS[a]
    n = ORDER[a].index(step) + 1
    icon, desc = INFO[step]
    cls = f"P3{a.upper()}{step.capitalize()}"
    extra_in, args = "", ""
    if step == "prompt":
        extra_in = f'''        MultilineInput(name="system_prompt", display_name="System prompt (edit me)",
                       value=_default_prompt(), info="Edit to change this agent's prompt for the run."),
        MessageTextInput(name="feedback", display_name="Feedback", advanced=True,
                         info="Optional comment appended to the prompt."),
'''
        args = ", feedback=self.feedback, system_prompt=self.system_prompt"
    elif step in ("llm", "parse", "validate"):
        extra_in = f'''        MessageTextInput(name="model", display_name="Model for this step",
                      value="inherit", advanced=True, info="inherit = Setup; or enter any provider/local model ID."),
'''
        args = ", model=self.model"
    helper = ""
    if step == "prompt":
        helper = f'''

def _default_prompt() -> str:
    try:
        lf_graph = _load_lf()

        return lf_graph.default_prompt("{agent}")
    except Exception:  # noqa: BLE001
        return ""
'''
    return f'''{HEAD}{helper}

class {cls}(Component):
    display_name = "P3 · {a.upper()}.{n} {step.capitalize()}"
    description = "{title}: {desc}"
    icon = "{icon}"
    name = "{cls}"

    inputs = [
{STATE_IN}{extra_in}    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_step")]

    async def run_step(self) -> Data:
        lf_graph = _load_lf()

        st, status = await lf_graph.execute(self.state.data, "{a}", "{step}"{args})
        # click the node: its status is what happened here, in plain language (full log: runs/<run_id>/log.md)
        self.status = lf_graph.chat_text(self.state.data, st) or status
        return Data(data=st)
'''


SETUP = HEAD + '''

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
'''

REPORT = HEAD.replace("from lfx.schema.data import Data", "from lfx.schema.data import Data\nfrom lfx.schema.message import Message") + '''

class P3StepReport(Component):
    display_name = "P3 · Report + Workbook"
    description = "Writes runs/<run_id>/analysis.xlsx, report.md, steps.md and result.json."
    icon = "file-spreadsheet"
    name = "P3StepReport"

    inputs = [HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True)]
    outputs = [
        Output(display_name="Report", name="report", method="build_report"),
        Output(display_name="Run state", name="state_out", method="build_state"),
    ]

    async def _done(self):
        lf_graph = _load_lf()

        if not hasattr(self, "_cache"):
            self._cache = await lf_graph.report(self.state.data)
        return self._cache

    async def build_report(self) -> Message:
        st, path = await self._done()
        self.status = path
        return Message(text=st["report"])

    async def build_state(self) -> Data:
        st, _ = await self._done()
        return Data(data=st)
'''

AFTER = [f"{a}_{s}" for a in ORDER for s in ORDER[a]] + ["setup", "report"]
CUSTOM = HEAD + f'''

class P3CustomNode(Component):
    display_name = "P3 · Custom Node"
    description = ("Your own step: a function from custom_nodes.py (safety gate, loop, tool call, ...). "
                   "Drop it anywhere in the chain and say which step it follows.")
    icon = "puzzle"
    name = "P3CustomNode"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True),
        MessageTextInput(name="node_name", display_name="Name", value="my_node"),
        DropdownInput(name="after", display_name="Sits after step", options={AFTER!r},
                      value="a3_postcheck", info="The step wired just before this node. Needed so loops know where to jump back to."),
        MessageTextInput(name="function", display_name="Function", value="custom_nodes:safety_gate",
                         info="file:function in custom_nodes.py. Examples: safety_gate, refine_loop, run_tools."),
        MultilineInput(name="params", display_name="Params (JSON)", value='{{"on_fail": "flag"}}',
                       info="Same as `params:` in workflow.yaml."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_step")]

    async def run_step(self) -> Data:
        lf_graph = _load_lf()

        st, status = await lf_graph.execute_custom(self.state.data, name=self.node_name, after=self.after,
                                                   node=self.function, params=self.params)
        self.status = lf_graph.chat_text(self.state.data, st) or status
        return Data(data=st)
'''


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("step_*.py"):
        old.unlink()
    n = 0
    for a in ORDER:
        for step in ORDER[a]:
            (OUT / f"step_{a}_{step}.py").write_text(step_file(a, step))
            n += 1
    for name, src in (("step_setup", SETUP), ("step_report", REPORT), ("step_custom", CUSTOM)):
        (OUT / f"{name}.py").write_text(src)
        n += 1
    print(f"wrote {n} components to {OUT}")


if __name__ == "__main__":
    main()
