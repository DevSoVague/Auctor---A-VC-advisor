"""Run API: drives Person 3's LangGraph pipeline for the app (owner: person 2).

    POST /runs                       start a run (settings below); returns run_id
    GET  /runs                       runs started since the API came up
    GET  /runs/{id}                  status, current step and hypothesis, steps, verdicts, cost
    GET  /runs/{id}/checkpoint       the paused review: agent output, summary, citation check
    POST /runs/{id}/checkpoint       {action, comment?, edited_output?}
                                     action: approve | approve_all | rerun | edit
    GET  /runs/{id}/citations        every A7 citation ledger in the run
    GET  /runs/{id}/report           the run's report (markdown)
    GET  /runs/{id}/files            files the run wrote (report, workbook, A8's memo and sheet)
    GET  /runs/{id}/files/{name}     download one

A run executes in the background until it finishes or pauses at a review (guided mode).
State lives in Person 3's checkpoint database (person3/.../runs/checkpoints.sqlite), so a
run started here can also be inspected or resumed with `python -m p3graph`.
"""

import asyncio
import os
import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from frontier.core.costs import load_prices

# Which copy of Person 3's pipeline to run: person3/<P3_PIPELINE>/langgraph-person3.
# grounded1to8 (default) adds A0 pitch intake and the A7 citation gate after every node;
# 1to6merged is the earlier copy that Person 2's p3_node citation hook is spliced into.
P3_PIPELINE = os.environ.get("P3_PIPELINE", "grounded1to8")
P3_ROOT = Path(__file__).resolve().parents[3] / "person3" / P3_PIPELINE / "langgraph-person3"
if str(P3_ROOT) not in sys.path:
    sys.path.insert(0, str(P3_ROOT))

COMMIT_NODES = {
    "a1_commit",
    "a2_commit",
    "a3_commit",
    "a4_commit",
    "a5_commit",
    "a6_commit",
    "a6_route",
}


class RunRequest(BaseModel):
    disease: str = "COPD"
    disease_id: str = "EFO_0000341"
    as_of: str = Field("today", description="'today' or YYYY-MM-DD (the evidence cutoff)")
    mode: Literal["guided", "single_shot"] = "guided"
    audience: Literal["generalist", "specialist"] = "generalist"
    llm: Literal["stub", "live"] = "live"
    profile: Literal["dev", "final"] = "dev"
    search: str = Field(
        "corpus", description="corpus | database | web | both | evstore | evstore+web"
    )
    retrieval: str = "stub"
    tools: bool = False
    judge: bool = True
    max_cards: int = Field(0, ge=0, description="only the top N hypotheses (0 = all)")
    pitch: str | None = Field(None, description="A0 intake: path or URL of a startup's pitch")
    store_pitch: bool = False  # also write the pitch to the shared evidence store
    cite_mode: Literal["node", "end", "off"] = "node"  # A7: after every agent, once, or off


class Decision(BaseModel):
    action: Literal["approve", "approve_all", "rerun", "edit"]
    comment: str = ""
    edited_output: Any = None


class Runner:
    """Owns the compiled graph and the background task for each run."""

    def __init__(self) -> None:
        self.graph = None
        self.saver_cm = None
        self.runs: dict[str, dict] = {}  # run_id -> {status, error, started, settings}
        self.tasks: dict[str, asyncio.Task] = {}

    async def start(self, db: Path | None = None, checkpointer=None) -> None:
        import p3graph
        from p3graph import engine as _E  # noqa: F401  (sets up Person 3's import path)
        from p3graph.graph import build

        loaded = Path(p3graph.__file__).resolve().parents[1]
        if loaded != P3_ROOT.resolve():  # both copies are named p3graph; never run the wrong one
            raise RuntimeError(f"p3graph was imported from {loaded}, expected {P3_ROOT}")

        if checkpointer is None:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            db = db or (P3_ROOT / "runs" / "checkpoints.sqlite")
            db.parent.mkdir(parents=True, exist_ok=True)
            self.saver_cm = AsyncSqliteSaver.from_conn_string(str(db))
            checkpointer = await self.saver_cm.__aenter__()
        self.graph = build(checkpointer)

    async def stop(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        if self.saver_cm is not None:
            await self.saver_cm.__aexit__(None, None, None)

    @staticmethod
    def cfg(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 400}

    def launch(self, run_id: str, inp: Any) -> None:
        self.runs.setdefault(run_id, {})["error"] = None
        self.tasks[run_id] = asyncio.create_task(self._drive(run_id, inp))

    async def _drive(self, run_id: str, inp: Any) -> None:
        from p3graph import nodes as N

        try:
            async for chunk in self.graph.astream(inp, self.cfg(run_id), stream_mode="updates"):
                if any(node in COMMIT_NODES for node in chunk):  # keep the workbook current
                    N.write_workbook((await self.graph.aget_state(self.cfg(run_id))).values)
            await self._write_memo(run_id)
        except Exception as exc:  # noqa: BLE001 - surfaced to the app as status 'error'
            self.runs[run_id]["error"] = f"{type(exc).__name__}: {exc}"

    async def _write_memo(self, run_id: str) -> None:
        """Agent 8: when the run has finished (not paused for a review), write the memo files.

        A memo problem is recorded as `memo_error` and never fails the run itself.
        """
        snap = await self.graph.aget_state(self.cfg(run_id))
        if snap.next or any(i for t in snap.tasks for i in t.interrupts):
            return  # still going, or waiting for a human
        info = self.runs.setdefault(run_id, {})
        try:
            from frontier.memo.run_memo import memo_for_run

            d = _run_dir(snap.values)
            if d is None:
                raise RuntimeError("the run has no output folder")
            info["memo"] = await memo_for_run(snap.values, d)
            info["memo_error"] = None
        except Exception as exc:  # noqa: BLE001
            info["memo_error"] = f"{type(exc).__name__}: {exc}"

    async def snapshot(self, run_id: str):
        snap = await self.graph.aget_state(self.cfg(run_id))
        if not snap.values:
            raise HTTPException(404, f"no run {run_id}")
        return snap

    async def status_of(self, run_id: str) -> str:
        """running while its task runs; error if it failed; else read from the graph state."""
        task = self.tasks.get(run_id)
        if task is not None and not task.done():
            return "running"
        if self.runs.get(run_id, {}).get("error"):
            return "error"
        snap = await self.snapshot(run_id)
        if any(i for t in snap.tasks for i in t.interrupts):
            return "paused"
        return "done" if not snap.next else "stopped"


def require_token(
    authorization: str | None = Header(None), x_api_key: str | None = Header(None)
) -> None:
    """Every /runs call needs APP_API_TOKEN once it is set (always set when the API is public).

    Accepts 'Authorization: Bearer <token>' or 'X-API-Key: <token>'. Without the token,
    anyone who found the public address could start runs that spend model credits.
    """
    expected = os.environ.get("APP_API_TOKEN", "")
    if not expected:
        return  # local development only; scripts/serve_public.sh refuses to run without it
    supplied = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "missing or wrong API token")


runner = Runner()
router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_token)])


def _cost(usage: dict) -> dict:
    """USD per agent from core/prices.yaml; None where a model has no listed price."""
    try:
        prices = load_prices()
    except Exception:  # noqa: BLE001
        prices = {}
    per_agent, total = {}, 0.0
    for agent, u in (usage or {}).items():
        price = prices.get(u.get("model", ""))
        usd = (
            price.cost(
                u.get("input_tokens", 0), u.get("cached_tokens", 0), u.get("output_tokens", 0)
            )
            if price
            else None
        )
        per_agent[agent] = {**u, "usd": usd}
        total += usd or 0.0
    return {"per_agent": per_agent, "total_usd": round(total, 6)}


def _citations(values: dict) -> dict:
    """'agent[:hypothesis]' -> citation report, from either pipeline copy.

    1to6merged + Person 2's hook: signals['citations:...'] (already in this shape).
    grounded1to8: 'citation_check' events; after a restart the last check is the one that counts.
    """
    out = {
        k.removeprefix("citations:"): v
        for k, v in (values.get("signals") or {}).items()
        if k.startswith("citations:")
    }
    for event, p in values.get("events") or []:
        if event != "citation_check":
            continue
        entries = p.get("entries") or []
        actions = [e.get("action") for e in entries]
        key = p.get("agent", "") + (f":{p['hypothesis_id']}" if p.get("hypothesis_id") else "")
        out[key] = {
            "checked_pairs": len(entries),
            "kept": actions.count("keep"),
            "downgraded": actions.count("downgrade"),
            "flagged": actions.count("remove"),
            "entries": entries,
        }
    return out


def _hypothesis(card: dict, values: dict) -> dict:
    """One hypothesis card for the app: the card's grading plus A6's verdict and top risk."""
    hid = card.get("id")
    verdict = (values.get("verdicts") or {}).get(hid) or {}
    risks = verdict.get("risks") or []
    judgment = (values.get("judgments") or {}).get(hid) or {}
    return {
        "id": hid,
        "mechanism": card.get("mechanism"),
        "direction": card.get("direction"),
        "modality": card.get("modality"),
        "evidence_tier": card.get("evidence_tier"),
        "validation_level": card.get("validation_level"),
        "biggest_uncertainty": card.get("biggest_uncertainty"),
        "verdict": verdict.get("verdict"),
        "top_risk": risks[0].get("text") if risks else None,
        "cheapest_test": risks[0].get("cheapest_test") if risks else None,
        "judge_decision": judgment.get("decision") or judgment.get("verdict"),
    }


def _run_dir(values: dict) -> Path | None:
    if values.get("workbook_path"):
        return Path(values["workbook_path"]).parent
    run_id = values.get("run_id")
    d = P3_ROOT / "runs" / run_id if run_id else None
    return d if d and d.exists() else None


@router.post("", status_code=201)
async def start_run(req: RunRequest) -> dict:
    run_id = uuid.uuid4().hex[:8]
    inp = {
        "config": {
            "disease": req.disease,
            "disease_id": req.disease_id,
            "as_of": req.as_of,
            "mode": req.mode,
            "profile": req.profile,
            "audience": req.audience,
        },
        "llm": {"mode": req.llm, "model": None, "profile": req.profile},
        "retrieval": req.retrieval,
        "search": req.search,
        "tools": req.tools,
        "judge": req.judge,
        "max_cards": req.max_cards,
        "pitch": req.pitch,
        "store_pitch": req.store_pitch,
        "cite_mode": req.cite_mode,
    }
    runner.runs[run_id] = {
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "settings": req.model_dump(),
    }
    runner.launch(run_id, inp)
    return {"run_id": run_id, "status": "running"}


@router.get("")
async def list_runs() -> list[dict]:
    out = []
    for run_id, info in sorted(
        runner.runs.items(), key=lambda kv: kv[1].get("started", ""), reverse=True
    ):
        try:
            status = await runner.status_of(run_id)
        except HTTPException:  # started, nothing saved yet
            status = "running"
        out.append(
            {
                "run_id": run_id,
                "status": status,
                "started": info.get("started"),
                "settings": info.get("settings"),
            }
        )
    return out


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    try:
        snap = await runner.snapshot(run_id)
    except HTTPException:
        task = runner.tasks.get(run_id)
        if task is not None and not task.done():  # started, nothing saved yet
            return {"run_id": run_id, "status": "running", "steps": []}
        raise
    v = snap.values
    steps = v.get("steps") or []
    return {
        "run_id": run_id,
        "status": await runner.status_of(run_id),
        "error": runner.runs.get(run_id, {}).get("error"),
        "memo": runner.runs.get(run_id, {}).get("memo"),
        "memo_error": runner.runs.get(run_id, {}).get("memo_error"),
        "settings": runner.runs.get(run_id, {}).get("settings"),
        "next": list(snap.next),
        "current_hypothesis": v.get("current"),
        "queue": v.get("queue") or [],
        "hypotheses": [_hypothesis(c, v) for c in v.get("cards") or []],
        "verdicts": {h: x.get("verdict") for h, x in (v.get("verdicts") or {}).items()},
        "killed": v.get("killed") or [],
        "steps": [
            {k: s.get(k) for k in ("node", "agent", "hid", "ms", "detail")} for s in steps[-60:]
        ],
        "step_count": len(steps),
        "citations": {
            k: {f: r.get(f) for f in ("checked_pairs", "kept", "downgraded", "flagged")}
            for k, r in _citations(v).items()
        },
        "cost": _cost(v.get("usage")),
    }


@router.get("/{run_id}/checkpoint")
async def get_checkpoint(run_id: str) -> dict:
    snap = await runner.snapshot(run_id)
    pending = [i.value for t in snap.tasks for i in t.interrupts]
    if not pending:
        raise HTTPException(409, f"run {run_id} is not paused at a review")
    p = pending[0]
    agent, hid = p.get("checkpoint"), p.get("hypothesis_id")
    key = f"{agent}:{hid}" if hid else agent
    # A4/A5 run between A3's review and A6, so A6's review also shows their checks.
    keys = [key] + ([f"a4_inactive:{hid}", f"a5_active:{hid}"] if agent == "a6_redteam" else [])
    cites = _citations(snap.values)
    return {**p, "citations": {k: cites[k] for k in keys if k in cites}}


@router.post("/{run_id}/checkpoint")
async def resume(run_id: str, decision: Decision) -> dict:
    from langgraph.types import Command

    if await runner.status_of(run_id) != "paused":
        raise HTTPException(409, f"run {run_id} is not paused at a review")
    if decision.action == "edit" and decision.edited_output is None:
        raise HTTPException(422, "action 'edit' needs edited_output")
    payload = decision.model_dump(exclude_none=True)
    runner.launch(run_id, Command(resume=payload))
    return {"run_id": run_id, "status": "running", "action": decision.action}


@router.get("/{run_id}/citations")
async def citations(run_id: str) -> dict:
    return _citations((await runner.snapshot(run_id)).values)


@router.get("/{run_id}/report", response_class=PlainTextResponse)
async def report(run_id: str) -> str:
    v = (await runner.snapshot(run_id)).values
    if not v.get("report"):
        raise HTTPException(409, f"run {run_id} has no report yet")
    return v["report"]


@router.get("/{run_id}/files")
async def files(run_id: str) -> list[dict]:
    d = _run_dir((await runner.snapshot(run_id)).values)
    if d is None:
        return []
    return [{"name": f.name, "bytes": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file()]


@router.get("/{run_id}/files/{name}")
async def download(run_id: str, name: str) -> FileResponse:
    d = _run_dir((await runner.snapshot(run_id)).values)
    path = (d / name).resolve() if d else None
    if path is None or path.parent != d.resolve() or not path.is_file():
        raise HTTPException(404, f"no file {name} in run {run_id}")
    return FileResponse(path, filename=name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runner.start()
    yield
    await runner.stop()
