"""Person 3's analysis agents as a LangGraph state machine, one node per agent step.

    setup → A1 chain → [review] → A2 chain → [review] → next_card ─┐
                                      ┌───────────────────────────┘
                                      ▼  (one hypothesis at a time)
        A3 chain → [review] → A4 ∥ A5 → A6 chain → route ─(revise once)→ A3 chain or A4∥A5
                                                      └→ [review] → next_card … → report

Agent chain (A1/A3/A6):  retrieve → prompt → llm → parse → postcheck/validate → commit
    parse fails    → fix → llm   (schema retry, once)
    validate fails → fix → llm   (semantic retry, once)
A1 runs postcheck before validate, A3/A6 run validate before postcheck, as in the agents.
A2 chain: retrieve → prompt → llm → parse → grade → postcheck → commit   (schema retry only)
A6 adds route after commit.

[review] = PRD checkpoint: interrupt() in guided mode (approve / approve_all / edit / rerun),
pass-through in single_shot mode (backtest).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from p3graph import nodes as N
from p3graph import steps as S
from p3graph import workflow as WF
from p3graph.state import P3State


def _chain(g: StateGraph, W: WF.Wiring, spec: S.Spec, validate_first: bool | None) -> None:
    """Register one agent's step nodes and wire them.

    validate_first: True  → parse → validate → postcheck → commit   (A3, A6)
                    False → parse → postcheck → validate → commit   (A1)
                    None  → parse → grade → postcheck → commit      (A2, no semantic retry)
    """
    n, fix = spec.node, spec.node("fix")
    for step in ("retrieve", "prompt", "postcheck", "commit"):
        g.add_node(n(step), getattr(S, f"{spec.short}_{step}"))
    g.add_node(n("llm"), S.llm_step(spec))
    g.add_node(n("parse"), S.parse_step(spec))
    g.add_node(n("fix"), S.fix_step(spec))
    g.add_edge(n("retrieve"), W.to(n("retrieve"), n("prompt")))
    g.add_edge(n("prompt"), W.to(n("prompt"), n("llm")))
    g.add_edge(n("llm"), W.to(n("llm"), n("parse")))
    g.add_edge(n("fix"), n("llm"))  # retry loop: not an insertion point

    def cond(src: str, ok: str, router) -> None:
        target = W.to(src, ok)
        g.add_conditional_edges(src, router(spec, target), [target, fix])

    if validate_first is None:  # A2
        g.add_node(n("grade"), S.a2_grade)
        cond(n("parse"), n("grade"), S.route_parse)
        g.add_edge(n("grade"), W.to(n("grade"), n("postcheck")))
        g.add_edge(n("postcheck"), W.to(n("postcheck"), n("commit")))
        return
    g.add_node(n("validate"), getattr(S, f"{spec.short}_validate"))
    if validate_first:  # A3, A6
        cond(n("parse"), n("validate"), S.route_parse)
        cond(n("validate"), n("postcheck"), S.route_validate)
        g.add_edge(n("postcheck"), W.to(n("postcheck"), n("commit")))
    else:  # A1
        cond(n("parse"), n("postcheck"), S.route_parse)
        g.add_edge(n("postcheck"), W.to(n("postcheck"), n("validate")))
        cond(n("validate"), n("commit"), S.route_validate)


def build(checkpointer=None, workflow: dict | str | None = None):
    """workflow: None → workflow.yaml (or $P3_WORKFLOW); a dict or a path also work (tests)."""
    wf = WF.load(workflow)
    WF.ACTIVE = wf  # the prompt steps read prompts/ from here
    W = WF.Wiring(wf["insert"])
    g = StateGraph(P3State)
    g.add_node("setup", N.setup)

    _chain(g, W, S.A1, validate_first=False)
    g.add_node("review_a1", N.make_review("a1_disease", "a2_retrieve", "a1_retrieve"),
               destinations=("a2_retrieve", "a1_retrieve"))

    _chain(g, W, S.A2, validate_first=None)
    g.add_node("review_a2", N.make_review("a2_mechanism", "next_card", "a2_retrieve"),
               destinations=("next_card", "a2_retrieve"))
    g.add_node("next_card", N.next_card, destinations=("a3_retrieve", "report"))

    _chain(g, W, S.A3, validate_first=True)
    g.add_node("review_a3", N.make_review("a3_profile", ["a4_inactive", "a5_active"], "a3_retrieve"),
               destinations=("a4_inactive", "a5_active", "a3_retrieve"))
    g.add_node("a4_inactive", N.a4)
    g.add_node("a5_active", N.a5)

    _chain(g, W, S.A6, validate_first=True)
    g.add_node("a6_route", S.a6_route, destinations=("review_a6", "a3_retrieve", "a4_inactive", "a5_active"))
    g.add_node("review_a6", N.make_review("a6_redteam", "next_card", "a6_retrieve"),
               destinations=("next_card", "a6_retrieve"))
    g.add_node("report", N.report)

    g.add_edge(START, "setup")
    g.add_edge("setup", W.to("setup", "a1_retrieve"))
    g.add_edge("a1_commit", W.to("a1_commit", "review_a1"))
    g.add_edge("a2_commit", W.to("a2_commit", "review_a2"))
    g.add_edge("a3_commit", W.to("a3_commit", "review_a3"))
    g.add_edge(["a4_inactive", "a5_active"], "a6_retrieve")  # fan-in: waits for both
    g.add_edge("a6_commit", W.to("a6_commit", "a6_route"))
    g.add_edge("report", W.to("report", END))
    W.finish(g)  # adds your inserted nodes from workflow.yaml
    return g.compile(checkpointer=checkpointer, name="person3_analysis")


# For LangGraph Studio / `langgraph dev` (the platform supplies its own checkpointer).
graph = build()
