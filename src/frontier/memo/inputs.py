"""Build the MemoInput from whatever the pipeline left behind (owner: person 5).

This is the only file that knows where upstream outputs live, so when the pipeline changes,
only this file changes.

    from_graph_state(state)    Person 3's LangGraph state after the hypothesis branches join
                               (the run API's snapshot, or the graph's final values)
    from_result_json(path)     Person 3's runs/<id>/result.json
    from_a8_input(inp, config) the team contract, agents/schemas.A8Input

Missing or invalid upstream pieces never raise. They become `gaps` and the memo states them.
"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from frontier.agents.schemas import (
    A8Input,
    HypothesisCard,
    InactiveAssets,
    JudgeDecision,
    Landscape,
    ProductProfile,
    RunConfig,
    Verdict,
    WhyNow,
)
from frontier.memo.models import (
    HypothesisBundle,
    KilledHypothesis,
    LedgerRow,
    LoggedDecision,
    MemoInput,
    PitchIntake,
)

M = TypeVar("M", bound=BaseModel)


def _load(model: type[M], data: Any, what: str, gaps: list[str]) -> M | None:
    """Validate one upstream output; on failure record a gap and return None."""
    if data is None:
        return None
    try:
        return model.model_validate(data)
    except ValidationError as err:
        first = err.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        gaps.append(f"{what}: invalid output ({where}: {first['msg']}); left out")
        return None


def _asset(assets: dict, hid: str, kind: str) -> Any:
    """A4/A5 write assets['h1:inactive']; the nested assets['h1']['inactive'] is a fallback."""
    return assets.get(f"{hid}:{kind}") or (assets.get(hid) or {}).get(kind)


def _hypothesis_of(target: str, ids: set[str]) -> str | None:
    """'a3_profile:h1' -> 'h1'; 'h1' -> 'h1'; run-wide targets -> None."""
    for part in reversed(target.split(":")):
        if part in ids:
            return part
    return None


def _ledger(state: dict) -> list[LedgerRow]:
    """Every Citation Checker result in the run, whichever pipeline copy produced it.

    - person3/1to6merged with Person 2's hook: signals['citations:<agent>[:<hid>]'].
    - person3/grounded1to8: 'citation_check' events, one per check; with cite_mode 'node' an
      agent is re-checked after each restart, so only the last check per (agent, hypothesis)
      counts. result.json carries the same payloads as 'citation_checks'.
    """
    checks = state.get("citation_checks")
    if checks is None:
        checks = [p for e, p in state.get("events") or [] if e == "citation_check"]
    if checks:
        last = {(c.get("agent"), c.get("hypothesis_id")): c for c in checks}
        return [
            LedgerRow.model_validate({**e, "agent": agent, "hypothesis_id": hid})
            for (agent, hid), check in last.items()
            for e in check.get("entries") or []
        ]
    signals = state.get("signals") or {}
    rows = []
    for key, report in sorted(signals.items()):
        if not key.startswith("citations:") or not isinstance(report, dict):
            continue
        _, agent, *rest = key.split(":")
        for entry in report.get("entries") or []:
            rows.append(
                LedgerRow.model_validate(
                    {**entry, "agent": agent, "hypothesis_id": rest[0] if rest else None}
                )
            )
    return rows


def _decisions(rows: Iterable[dict], ids: set[str]) -> list[LoggedDecision]:
    out = []
    for row in rows:
        target = str(row.get("target", ""))
        out.append(
            LoggedDecision.model_validate(
                {**row, "target": target, "hypothesis_id": _hypothesis_of(target, ids)}
            )
        )
    return out


def from_graph_state(state: dict) -> MemoInput:
    """Person 3's pipeline state, after every hypothesis branch has finished."""
    gaps: list[str] = []
    config = RunConfig.model_validate(state["config"])
    why_now = _load(WhyNow, state.get("why_now"), "A1 why-now", gaps)
    if state.get("why_now") is None:
        gaps.append("A1 why-now: missing")

    grades = state.get("grades") or {}
    profiles = state.get("profiles") or {}
    assets = state.get("assets") or {}
    verdicts = state.get("verdicts") or {}
    judgments = state.get("judgments") or {}
    revised = state.get("revise_used") or {}
    killed = [KilledHypothesis.model_validate(k) for k in state.get("killed") or []]
    killed_ids = {k.hypothesis_id for k in killed}

    if not assets:
        gaps.append("A4/A5 assets and competitors: not in this run's output")
    if state.get("judgments") is None:
        gaps.append("A7 Judge: not in this run's output")

    bundles = []
    for position, raw in enumerate(state.get("cards") or [], start=1):
        card = _load(HypothesisCard, raw, f"A2 card {raw.get('id', position)}", gaps)
        if card is None:
            continue
        hid = card.id
        grade = grades.get(hid) or {}
        verdict = _load(Verdict, verdicts.get(hid), f"A6 verdict {hid}", gaps)
        bundle = HypothesisBundle(
            id=hid,
            rank=int(grade.get("rank") or position),
            status=(
                "killed"
                if hid in killed_ids or (verdict and verdict.verdict == "kill")
                else "survives"
                if verdict
                else "incomplete"
            ),
            card=card,
            grade=grade,
            profile=_load(ProductProfile, profiles.get(hid), f"A3 profile {hid}", gaps),
            inactive=_load(
                InactiveAssets, _asset(assets, hid, "inactive"), f"A4 assets {hid}", gaps
            ),
            landscape=_load(
                Landscape, _asset(assets, hid, "landscape"), f"A5 landscape {hid}", gaps
            ),
            verdict=verdict,
            judgment=_load(JudgeDecision, judgments.get(hid), f"A7 Judge {hid}", gaps),
            revised=bool(revised.get(hid)),
        )
        if bundle.status == "survives":
            missing = [
                name
                for name, value in (
                    ("A3 profile", bundle.profile),
                    ("A4 assets", bundle.inactive),
                    ("A5 landscape", bundle.landscape),
                )
                if value is None
            ]
            if assets and missing:  # a run with no A4/A5 at all is reported once, above
                gaps.append(f"{hid}: missing {', '.join(missing)}")
            elif not assets and bundle.profile is None:
                gaps.append(f"{hid}: missing A3 profile")
        elif bundle.status == "incomplete":
            gaps.append(f"{hid}: no red-team verdict; not treated as a survivor")
        bundles.append(bundle)

    bundles.sort(key=lambda b: b.rank)
    ids = {b.id for b in bundles}
    signals = state.get("signals") or {}
    ledger = _ledger(state)
    pitch = _load(PitchIntake, state.get("intake"), "A0 pitch intake", gaps)
    if pitch and not pitch.accepted:
        gaps.append("A0 pitch intake: pitch rejected (" + "; ".join(pitch.reasons) + ")")
    if not ledger:
        gaps.append("Citation Checker: no ledger in this run; no claim counts as verified")

    return MemoInput(
        run_id=str(state.get("run_id") or ""),
        config=config,
        pitch=pitch,
        why_now=why_now,
        hypotheses=bundles,
        killed=killed,
        ledger=ledger,
        decisions=_decisions(state.get("decisions") or [], ids),
        pub_counts=signals.get("pub_counts") or {},
        usage=state.get("usage") or {},
        gaps=gaps,
        test_data=(state.get("llm") or {}).get("mode") == "stub",
    )


def from_result_json(path: str | Path) -> MemoInput:
    """Person 3's runs/<id>/result.json (a subset of the graph state)."""
    return from_graph_state(json.loads(Path(path).read_text()))


def from_a8_input(inp: A8Input, config: RunConfig, run_id: str = "") -> MemoInput:
    """The team contract in agents/schemas.py: dossiers for survivors and killed hypotheses."""
    killed_ids = {d.card.id for d in inp.killed}
    bundles = [
        HypothesisBundle(
            id=d.card.id,
            rank=rank,
            status="killed" if d.card.id in killed_ids else "survives",
            card=d.card,
            profile=d.profile,
            inactive=d.inactive,
            landscape=d.landscape,
            verdict=d.verdict,
            judgment=d.judgment,
        )
        for rank, d in enumerate([*inp.survivors, *inp.killed], start=1)
    ]
    ids = {b.id for b in bundles}
    return MemoInput(
        run_id=run_id,
        config=config,
        why_now=inp.why_now,
        hypotheses=bundles,
        killed=[
            KilledHypothesis(
                hypothesis_id=d.card.id,
                mechanism=d.card.mechanism,
                reason=d.verdict.premortem,
                kill_criterion=d.verdict.risks[0].kill_criterion if d.verdict.risks else "",
            )
            for d in inp.killed
        ],
        ledger=[LedgerRow(**e.model_dump(), agent="unknown") for e in inp.ledger],
        decisions=_decisions((d.model_dump() for d in inp.decisions), ids),
        gaps=([] if any(b.judgment for b in bundles) else ["A7 Judge: not in this run's output"]),
    )
