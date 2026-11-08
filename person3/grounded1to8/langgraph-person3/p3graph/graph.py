"""Person 3's analysis agents as a LangGraph state machine, one node per agent step.

    setup → A1 chain → [review] → A2 chain → [review] → next_card ─┐
                                      ┌───────────────────────────┘
                                      ▼  (one hypothesis at a time)
        A3 chain → [review] → A4 chain → A5 chain → A6 chain → route ─(revise once)→ A3 chain or A4 chain
                                                      └→ [review] → next_card … → report

Agent chain (A1/A3/A6):  retrieve → prompt → toolloop → llm → parse → postcheck/validate → cite → commit
    cite = A7 citation check; a hallucinated citation restarts the node at llm (max 2), then strips it
    toolloop = DB/web tool calls (state['tools']; A1 also writes new evidence, A6 runs checkers), else pass-through
A6 commit → a6b_judge (state['judge']: reads only A3 + A6, its decision is what a6_route routes) → a6_route
    parse fails    → fix → llm   (schema retry, once)
    validate fails → fix → llm   (semantic retry, once)
A1 runs postcheck before validate, A3/A6 run validate before postcheck, as in the agents.
A2 chain: retrieve → prompt → llm → parse → grade → postcheck → commit   (schema retry only)
A6 adds route after commit.
A4 chain: gather → triage → establish → prompt → llm → merge → commit            (p3graph/competitor.py)
A5 chain: gather → shortlist → establish → prompt → llm → merge → score → commit

[review] = PRD checkpoint: interrupt() in guided mode (approve / approve_all / edit / rerun),
pass-through in single_shot mode (backtest).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from p3graph import competitor as C
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
    g.add_node(n("toolloop"), S.tools_step(spec))
    g.add_node(n("llm"), S.llm_step(spec))
    g.add_node(n("parse"), S.parse_step(spec))
    g.add_node(n("fix"), S.fix_step(spec))
    g.add_node(n("cite"), S.cite_step(spec))  # A7 citation gate: restart at llm on hallucination
    g.add_conditional_edges(n("cite"), S.route_cite(spec, W.to(n("cite"), n("commit"))),
                            [W.to(n("cite"), n("commit")), n("llm")])
    g.add_edge(n("retrieve"), W.to(n("retrieve"), n("prompt")))
    g.add_edge(n("prompt"), W.to(n("prompt"), n("toolloop")))
    g.add_edge(n("toolloop"), W.to(n("toolloop"), n("llm")))
    g.add_edge(n("llm"), W.to(n("llm"), n("parse")))
    g.add_edge(n("fix"), n("llm"))  # retry loop: not an insertion point

    def cond(src: str, ok: str, router) -> None:
        target = W.to(src, ok)
        g.add_conditional_edges(src, router(spec, target), [target, fix])

    if validate_first is None:  # A2
        g.add_node(n("grade"), S.a2_grade)
        cond(n("parse"), n("grade"), S.route_parse)
        g.add_edge(n("grade"), W.to(n("grade"), n("postcheck")))
        g.add_edge(n("postcheck"), W.to(n("postcheck"), n("cite")))
        return
    g.add_node(n("validate"), getattr(S, f"{spec.short}_validate"))
    if validate_first:  # A3, A6
        cond(n("parse"), n("validate"), S.route_parse)
        cond(n("validate"), n("postcheck"), S.route_validate)
        g.add_edge(n("postcheck"), W.to(n("postcheck"), n("cite")))
    else:  # A1
        cond(n("parse"), n("postcheck"), S.route_parse)
        g.add_edge(n("postcheck"), W.to(n("postcheck"), n("validate")))
        cond(n("validate"), n("cite"), S.route_validate)


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
    g.add_node("next_card", N.next_card, destinations=("a3_retrieve", "a7_final"))

    _chain(g, W, S.A3, validate_first=True)
    g.add_node("review_a3", N.make_review("a3_profile", C.first("a4"), "a3_retrieve"),
               destinations=(C.first("a4"), "a3_retrieve"))
    C.add_to_graph(g, W, after="review_a3", before="a6_retrieve")  # sequential for V1: A4 steps, then A5 steps

    _chain(g, W, S.A6, validate_first=True)
    g.add_node("a6_route", S.a6_route, destinations=("review_a6", "a3_retrieve", C.first("a4")))
    g.add_node("review_a6", N.make_review("a6_redteam", "next_card", "a6_retrieve"),
               destinations=("next_card", "a6_retrieve"))
    g.add_node("a7_final", N.final_citations)  # cite_mode 'end': one A7 pass over everything
    g.add_node("report", N.report)
    for step in N.A8_STEPS:  # agent 8, the memo writer, as visible steps; it runs last
        g.add_node(f"a8_{step}", N.a8_node(step))
    g.add_edge("a7_final", "report")

    g.add_edge(START, "setup")
    g.add_node("intake", N.intake)  # A0 input gate: a rejected pitch ends the run here
    g.add_edge("setup", "intake")
    first = W.to("setup", "a1_retrieve")
    g.add_conditional_edges("intake", lambda s: END if (s.get("intake") or {}).get("accepted") is False else first,
                            [first, END])
    g.add_edge("a1_commit", W.to("a1_commit", "review_a1"))
    g.add_edge("a2_commit", W.to("a2_commit", "review_a2"))
    g.add_edge("a3_commit", W.to("a3_commit", "review_a3"))
    g.add_node("a6b_judge", S.a6b_judge)
    g.add_edge("a6_commit", W.to("a6_commit", "a6b_judge"))
    g.add_edge("a6b_judge", W.to("a6b_judge", "a6_route"))
    g.add_edge("report", W.to("report", "a8_read"))
    for here, there in zip(N.A8_STEPS, N.A8_STEPS[1:]):
        if here != "check":
            g.add_edge(f"a8_{here}", f"a8_{there}")
    g.add_conditional_edges("a8_check", N.a8_route, ["a8_llm", "a8_merge"])  # one retry, then merge
    g.add_edge("a8_commit", W.to("a8_commit", END))
    W.finish(g)  # adds your inserted nodes from workflow.yaml
    return g.compile(checkpointer=checkpointer, name="person3_analysis")


# For LangGraph Studio / `langgraph dev` (the platform supplies its own checkpointer).
graph = build()
