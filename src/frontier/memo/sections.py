"""The memo as a plain outline: blocks that any renderer can draw (owner: person 5).

No Word code here, so render_docx.py and the website can share one outline.

Front (about two pages): title, caution banner, bottom line, pursuits table, one block per
pursuit, the two decision charts, catalysts.
Appendix: one section per agent layer, then the decision log, killed hypotheses, citations,
method and cost.

Citations: every Statement's claim ids are resolved to their source ids, numbered in order of
first use, and listed with dates in appendix H. "[3]" in the text is source 3.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from frontier.memo.charts import landscape_rows
from frontier.memo.draft import MemoDraft, Statement
from frontier.memo.models import MemoInput

Run = tuple[str, str]  # (style, text): style is "", "b" (bold) or "i" (italic)


@dataclass
class Heading:
    text: str
    level: int = 1


@dataclass
class Para:
    runs: list[Run]


@dataclass
class Bullets:
    items: list[list[Run]]


@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]
    widths: list[float] | None = None  # inches; None lets Word decide


@dataclass
class Figure:
    path: Path
    caption: str
    width_in: float = 6.0


@dataclass
class Callout:
    title: str
    lines: list[str]
    tone: Literal["caution", "note"] = "note"


@dataclass
class PageBreak:
    pass


Block = Heading | Para | Bullets | Table | Figure | Callout | PageBreak


@dataclass
class Outline:
    title: str
    subtitle: str
    blocks: list[Block] = field(default_factory=list)


class Sources:
    """Numbers sources in order of first citation."""

    def __init__(self, inp: MemoInput) -> None:
        self.claims = inp.all_claims()
        self.dates = {r.source_id: r.source_date for r in inp.ledger if r.source_date}
        self.order: list[str] = []

    def cite(self, claim_ids: list[str]) -> str:
        nums = []
        for cid in claim_ids:
            claim = self.claims.get(cid)
            for sid in claim.source_ids if claim else []:
                if sid not in self.order:
                    self.order.append(sid)
                nums.append(self.order.index(sid) + 1)
        nums = sorted(set(nums))
        return f" [{', '.join(map(str, nums))}]" if nums else ""


def _stmt(s: Statement, src: Sources) -> list[Run]:
    tag = src.cite(s.claim_ids) if s.kind == "evidence" else ""
    return [("", s.text + tag)] + ([("i", " (analysis)")] if s.kind == "reasoning" else [])


def _front(inp: MemoInput, d: MemoDraft, charts: dict[str, Path], src: Sources) -> list[Block]:
    b: list[Block] = []
    if inp.test_data:
        b.append(
            Callout(
                "Test data: not a recommendation",
                [
                    "This memo was built from test fixtures or a test-mode run. The companies, "
                    "drugs, trials and dates are made up, so nothing here is a real finding."
                ],
                tone="note",
            )
        )
    if d.proceed_with_caution:
        b.append(
            Callout(
                "Proceed with caution",
                d.caution_reasons[:5] + [f"Judged against {d.caution_basis}."],
                tone="caution",
            )
        )
    b.append(Heading("Bottom line", 2))
    b.append(Bullets([_stmt(s, src) for s in d.bottom_line]))

    pitch = inp.pitch
    if pitch and pitch.accepted and pitch.profile:
        pp = pitch.profile
        counts: dict[str, int] = {}
        for item in pitch.cited:
            counts[str(item.get("status", "unknown"))] = counts.get(str(item.get("status")), 0) + 1
        b.append(Heading("The pitch we tested", 2))
        b.append(
            Bullets(
                [
                    [("b", "Company: "), ("", pp.company)],
                    [
                        ("b", "Claim: "),
                        (
                            "",
                            f"{pp.mechanism} for {pp.indication or 'this disease'}"
                            + (f" ({pp.modality}, {pp.stage})" if pp.stage else ""),
                        ),
                    ],
                    [
                        ("b", "What it cites: "),
                        ("", ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "nothing"),
                    ],
                ]
                + [[("i", "Warning: "), ("", w)] for w in pitch.warnings[:3]]
            )
        )

    b.append(Heading("Recommended pursuits", 2))
    b.append(
        Table(
            ["#", "Move", "Name", "Mechanism", "Upside / risk", "Next step"],
            [
                [str(i), p.form.title(), p.name, p.mechanism, f"{p.upside} / {p.risk}", p.next_step]
                for i, p in enumerate(d.pursuits, 1)
            ],
            widths=[0.3, 0.6, 1.3, 1.4, 0.8, 2.5],
        )
    )
    for i, p in enumerate(d.pursuits, 1):
        b.append(Heading(f"{i}. {p.form.title()}: {p.name}", 3))
        b.append(Para([("i", p.hook)]))
        b.append(
            Para(
                [
                    ("b", "The move: "),
                    ("", p.the_move + " "),
                    ("b", "Next step: "),
                    ("", p.next_step),
                ]
            )
        )
        b.append(Para([("b", "Why it's a good investment")]))
        b.append(Bullets([_stmt(s, src) for s in p.why_good]))
        b.append(Para([("b", "What could go wrong")]))
        b.append(
            Bullets(
                [_stmt(s, src) for s in p.what_could_go_wrong]
                or [[("", "No red-team risks in this run.")]]
            )
        )
        b.append(Para([("b", "Is the return worth the risk? ")] + _stmt(p.return_vs_risk, src)))
        b.append(Para([("b", "Gap to the ideal product: "), ("", p.gap_to_profile)]))
        b.append(Para([("b", "What would change our call: "), ("", p.what_would_change_the_call)]))
        crowd = f"{p.crowding:.2f}" if p.crowding is not None else "n/a"
        b.append(
            Para(
                [
                    ("b", "Validation: "),
                    ("", p.validation_level + "   "),
                    ("b", "Crowding: "),
                    ("", f"{crowd} (0 = open field)"),
                ]
            )
        )
    if "return_risk" in charts:
        b.append(Figure(charts["return_risk"], "Each pursuit's upside against its risk.", 4.2))
    if "opportunity_map" in charts:
        b.append(
            Figure(
                charts["opportunity_map"],
                "Where every hypothesis sits; hollow marks were killed or incomplete.",
            )
        )
    b.append(Heading("Upcoming catalysts", 2))
    if "catalysts" in charts:
        b.append(Figure(charts["catalysts"], "Dated events that could change the call.", 6.0))
    b.append(
        Bullets(
            [[("b", f"{c.on:%b %-d, %Y}: "), ("", c.what)] for c in d.catalysts[:8]]
            or [[("", "No dated catalysts found by the competitor agent.")]]
        )
    )
    return b


def _appendix(inp: MemoInput, d: MemoDraft, charts: dict[str, Path], src: Sources) -> list[Block]:
    b: list[Block] = [PageBreak(), Heading("Appendix", 1)]

    b.append(Heading("A. Why now (agent 1)", 2))
    if inp.why_now:
        b.append(
            Table(
                ["Date", "Shift", "Changes", "What happened"],
                [
                    [
                        f"{s.on:%b %Y}",
                        s.type,
                        ", ".join(s.effect_on),
                        s.summary + src.cite([c.id for c in s.claims]),
                    ]
                    for s in inp.why_now.shifts
                ],
                widths=[0.7, 0.9, 1.1, 4.2],
            )
        )
        b.append(Para([("b", "Patient segments")]))
        b.append(
            Bullets(
                [
                    [("b", seg.name + ": "), ("", "; ".join(seg.inclusion_criteria))]
                    for seg in inp.why_now.segments
                ]
            )
        )

    b.append(Heading("B. Mechanisms (agent 2)", 2))
    b.append(
        Table(
            ["Rank", "ID", "Mechanism", "Direction", "Tier / level", "Status"],
            [
                [
                    str(h.rank),
                    h.id,
                    h.card.mechanism,
                    h.card.direction,
                    f"{h.card.evidence_tier} / {h.card.validation_level}",
                    h.status,
                ]
                for h in inp.hypotheses
            ],
        )
    )

    b.append(Heading("C. Ideal product profiles (agent 3)", 2))
    for h in inp.survivors:
        if h.profile:
            b.append(Para([("b", f"{h.id} {h.card.mechanism}")]))
            b.append(
                Bullets(
                    [[("b", "Must-have: "), ("", m)] for m in h.profile.must_haves]
                    + [[("b", "Expansion: "), ("", h.profile.expansion_opportunity)]]
                )
            )

    b.append(Heading("D. Assets and competitors (agents 4 and 5)", 2))
    if "landscape" in charts:
        b.append(Figure(charts["landscape"], "Competitor programs by mechanism and stage."))
    rows = landscape_rows(inp)
    if rows:
        b.append(
            Table(
                ["Program", "Owner", "Type", "Stage", "Fit", "Flag"],
                [
                    [
                        r["program"].asset,
                        r["program"].owner,
                        r["program"].owner_type.replace("_", " "),
                        r["program"].stage,
                        r["program"].profile_fit,
                        r["program"].flag_reason or "",
                    ]
                    for r in rows[:15]
                ],
            )
        )
    inactive = [
        (h.id, kind, a)
        for h in inp.survivors
        if h.inactive
        for kind, items in (
            ("repurposing", h.inactive.repurposing),
            ("shelved", h.inactive.shelved),
        )
        for a in items
    ]
    if inactive:
        b.append(
            Table(
                ["Hyp.", "List", "Asset", "Owner", "Stage", "Fit", "Limitation"],
                [
                    [hid, kind, a.name, a.owner, a.stage, f"{a.profile_fit:.1f}", a.limitations]
                    for hid, kind, a in inactive[:15]
                ],
            )
        )

    b.append(Heading("E. Red team and judge (agent 6, A7 Judge)", 2))
    for h in inp.hypotheses:
        if not h.verdict:
            continue
        judge = (
            f" Judge: red team {h.judgment.red_team_is.replace('_', ' ')}, "
            f"decision {h.judgment.decision}."
            if h.judgment
            else ""
        )
        b.append(
            Para(
                [
                    ("b", f"{h.id} {h.card.mechanism}: "),
                    ("", f"{h.verdict.verdict}{' (revised)' if h.revised else ''}.{judge}"),
                ]
            )
        )
        b.append(
            Bullets(
                [
                    [
                        (
                            "",
                            f"{r.text} Kill if: {r.kill_criterion}"
                            + src.cite([c.id for c in r.claims]),
                        )
                    ]
                    for r in h.verdict.risks
                ]
            )
        )

    b.append(Heading("F. Decision log", 2))
    b.append(
        Table(
            ["Hyp.", "Actor", "Action", "Target", "Reason"],
            [
                [dl.hypothesis_id or "run", dl.actor, dl.action, dl.target, dl.reason]
                for dl in sorted(inp.decisions, key=lambda x: x.hypothesis_id or "")
            ]
            or [["", "", "", "", "No decisions logged."]],
            widths=[0.5, 1.0, 0.8, 1.3, 3.3],
        )
    )

    b.append(Heading("G. Killed hypotheses", 2))
    b.append(
        Bullets(
            [
                [
                    ("b", f"{k.hypothesis_id} {k.mechanism}: "),
                    ("", f"{k.reason.strip().rstrip('.')}. Kill criterion: {k.kill_criterion}"),
                ]
                for k in inp.killed
            ]
            or [[("", "None killed in this run.")]]
        )
    )
    b += _screened(inp, d)

    b.append(Heading("H. Citations (Citation Checker)", 2))
    b += _citation_summary(inp, d)
    by_agent: dict[str, list] = {}
    for r in inp.ledger:
        by_agent.setdefault(r.agent, []).append(r)
    b.append(
        Table(
            ["Agent", "Source checks", "Supported", "Partly", "Removed"],
            [
                [
                    a,
                    str(len(rs)),
                    str(sum(r.supports == "yes" for r in rs)),
                    str(sum(r.action == "downgrade" for r in rs)),
                    str(sum(r.action == "remove" for r in rs)),
                ]
                for a, rs in by_agent.items()
            ],
        )
    )
    b.append(Para([("b", "Sources cited in this memo")]))
    b.append(
        Bullets(
            [
                [
                    (
                        "",
                        f"[{i}] {sid}"
                        + (f", dated {src.dates[sid]:%Y-%m-%d}" if sid in src.dates else ""),
                    )
                ]
                for i, sid in enumerate(src.order, 1)
            ]
            or [[("", "No sources cited.")]]
        )
    )

    b.append(Heading("I. Method and cost", 2))
    b.append(
        Bullets(
            [
                [
                    (
                        "",
                        "Eight agents. The memo agent runs last, after every hypothesis has "
                        "finished and its citations have been checked.",
                    )
                ],
                [
                    (
                        "",
                        "The memo agent reads only the other agents' checked output. "
                        "It does no search and uses no outside data.",
                    )
                ],
                [
                    (
                        "",
                        f"Evidence window: {inp.evidence_window}. {_source_span(inp, src)} "
                        "Scores and ratings are computed by code from the agents' output.",
                    )
                ],
            ]
            + [
                [
                    (
                        "",
                        "Wording: written by the memo agent's model and checked by code "
                        "(names, numbers and citations must come from the agents' output)."
                        if d.written_by == "model"
                        else "Wording: written from templates over the computed scores.",
                    )
                ]
            ]
            + ([[("i", "Note: "), ("", d.fallback_reason)]] if d.fallback_reason else [])
            + [[("i", "Gap: "), ("", g)] for g in inp.gaps]
        )
    )
    if inp.usage:
        b.append(
            Table(
                ["Agent", "Model", "Calls", "Input tokens", "Output tokens"],
                [
                    [
                        a,
                        str(u.get("model", "")),
                        str(u.get("calls", 0)),
                        f"{u.get('input_tokens', 0):,}",
                        f"{u.get('output_tokens', 0):,}",
                    ]
                    for a, u in inp.usage.items()
                ],
            )
        )
    return b


def _source_span(inp: MemoInput, src: Sources) -> str:
    """What the cited sources' dates actually are (computed, so it can never overstate)."""
    from frontier.memo.models import evidence_floor

    dates = [src.dates[sid] for sid in src.order if sid in src.dates]
    if not dates:
        return "The memo cites no dated sources."
    early = sum(1 for d in dates if d < evidence_floor())
    span = (
        f"Sources cited in this memo are dated {min(dates):%B %-d, %Y} to {max(dates):%B %-d, %Y}"
    )
    if early:
        span += f"; {early} of them predate the window (reference or retraction records)"
    return span + "."


def _screened(inp: MemoInput, d: MemoDraft) -> list[Block]:
    """What the memo agent kept out: hypotheses it did not recommend, and approved drugs."""
    from frontier.memo.scoring import scorecards

    chosen = {p.hypothesis_id for p in d.pursuits}
    items: list[list[Run]] = []
    for sc in scorecards(inp):
        if sc.approved:
            items.append(
                [
                    ("b", f"{sc.hypothesis_id} {sc.mechanism}: "),
                    (
                        "",
                        f"already approved ({'; '.join(sc.approved)}). Priced in, so the approved "
                        "drug is never offered as a pursuit"
                        + (
                            "; a follow-on is recommended instead."
                            if sc.hypothesis_id in chosen
                            else "; no follow-on made the shortlist."
                        ),
                    ),
                ]
            )
        if sc.hypothesis_id not in chosen and not sc.approved:
            items.append(
                [
                    ("b", f"{sc.hypothesis_id} {sc.mechanism}: "),
                    (
                        "",
                        f"survived the red team but not recommended (upside {sc.upside}, "
                        f"risk {sc.risk}; ranked below the recommended pursuits).",
                    ),
                ]
            )
    if not items:
        return []
    return [
        Para([("b", "Screened out by the memo agent (not killed by the red team)")]),
        Bullets(items),
    ]


def _citation_summary(inp: MemoInput, d: MemoDraft) -> list[Block]:
    """What the checker caught and proof none of it reached the memo (counts are of claims)."""
    status = inp.claim_status()
    if not status:
        return []
    n = lambda kind: sum(1 for v in status.values() if v == kind)  # noqa: E731
    cited_ids = {
        c
        for p in d.pursuits
        for st in (*p.why_good, *p.what_could_go_wrong, p.return_vs_risk)
        for c in st.claim_ids
    } | {c for st in d.bottom_line for c in st.claim_ids}
    leaked = sum(1 for c in cited_ids if status.get(c) == "removed")
    return [
        Para(
            [
                (
                    "",
                    f"The checker examined {len(status)} claims: {n('verified')} fully supported, "
                    f"{n('partly')} partly supported (kept, labelled and with lower confidence), "
                    f"{n('removed')} removed because no source supported them. "
                    f"The memo cites {len(cited_ids)} claims, of which {leaked} were removed.",
                )
            ]
        )
    ]


def build_outline(inp: MemoInput, draft: MemoDraft, charts: dict[str, Path]) -> Outline:
    src = Sources(inp)
    front = _front(inp, draft, charts, src)
    back = _appendix(inp, draft, charts, src)
    subtitle = (
        f"Biopharma R&D analysis · {inp.config.disease} · evidence {inp.evidence_window}"
        f" · run {inp.run_id or 'n/a'}"
    )
    return Outline(title=draft.title, subtitle=subtitle, blocks=front + back)
