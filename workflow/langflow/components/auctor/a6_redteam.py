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


class AuctorA6RedTeam(Component):
    display_name = "Auctor · A6 Red Teamer"
    description = ("Q5 What could kill it: verdict per card (survives/revise/kill), risks with kill "
                   "criteria and cheapest test. One revise round (reruns A3 when targeted).")
    icon = "shield-alert"
    name = "AuctorA6RedTeam"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous node."),
        MessageTextInput(name="feedback", display_name="Reviewer feedback", advanced=True,
                         info="Optional comment; appended to the prompt (rerun with feedback)."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_agent")]

    async def run_agent(self) -> Data:
        b = _bridge()
        from agents.analysis import a3_profile, a6_redteam
        from agents.analysis.schemas import HypothesisCard, ProductProfile, RedTeamInput, WhyNow

        st = __import__("copy").deepcopy(self.state.data)
        if not st.get("profiles"):
            raise ValueError("A6 needs A3's profiles: connect Auctor · A3 first")
        ctx = b.make_ctx(st, self.feedback)
        why = WhyNow.model_validate(st["why_now"])
        for c in st["cards"]:
            card = HypothesisCard.model_validate(c)
            prof = ProductProfile.model_validate(st["profiles"][card.id])
            v = await a6_redteam.run(RedTeamInput(card=card, profile=prof, why_now=why), ctx)
            if v.verdict == "revise":  # exactly one revise round
                st["revised"].append(card.id)
                if v.revise_target == "a3_profile":
                    ctx.feedback = "Red Team requested a revision: " + (
                        v.risks[0].text if v.risks else v.premortem)
                    prof = await a3_profile.run(card, ctx)
                    ctx.feedback = self.feedback or None
                    st["profiles"][card.id] = prof.model_dump(mode="json")
                v = await a6_redteam.run(RedTeamInput(card=card, profile=prof, why_now=why), ctx)
                if v.verdict == "revise":  # cap: survives, flagged
                    v.verdict, v.revise_target = "survives", None
                    v.premortem = "[revise cap reached; unresolved] " + v.premortem
                    ctx.emit("agent_done", {"agent": "a6_redteam", "hypothesis_id": card.id,
                                            "output": v.model_dump(mode="json")})
            st["verdicts"][card.id] = v.model_dump(mode="json")
            if v.verdict == "kill":
                st["killed"].append(f"{card.id} {card.mechanism}: "
                                    + (v.risks[0].kill_criterion if v.risks else v.premortem))
        b.finish(st, ctx)
        self.status = " · ".join(f"{h}:{x['verdict']}" for h, x in st["verdicts"].items())
        return Data(data=st)
