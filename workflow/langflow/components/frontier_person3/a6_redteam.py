import os
import sys

# Person-3 Langflow bridge lives in langflow-person3/ (override with P3_LANGFLOW_HOME).
_P3 = os.getenv("P3_LANGFLOW_HOME", "/Users/sovague/Desktop/ctrl+alt+cure/upmc/grounded1to8/workflow/langflow")
if _P3 not in sys.path:
    sys.path.insert(0, _P3)

from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, MessageTextInput, Output
from lfx.schema.data import Data


class P3A6RedTeam(Component):
    display_name = "P3 · A6 Red Teamer"
    description = ("Q5 What could kill it: verdict per card (survives/revise/kill), risks with kill "
                   "criteria and cheapest test. One revise round (reruns A3 when targeted).")
    icon = "shield-alert"
    name = "P3A6RedTeam"

    inputs = [
        HandleInput(name="state", display_name="Run state", input_types=["Data"], required=True,
                    info="State from the previous Person-3 node."),
        MessageTextInput(name="feedback", display_name="Reviewer feedback", advanced=True,
                         info="Optional comment; appended to the prompt (rerun with feedback)."),
    ]
    outputs = [Output(display_name="Run state", name="state_out", method="run_agent")]

    async def run_agent(self) -> Data:
        import lf_bridge as b
        from agents.analysis import a3_profile, a6_redteam
        from agents.analysis.schemas import HypothesisCard, ProductProfile, RedTeamInput, WhyNow

        st = __import__("copy").deepcopy(self.state.data)
        if not st.get("profiles"):
            raise ValueError("A6 needs A3's profiles: connect P3 · A3 first")
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
