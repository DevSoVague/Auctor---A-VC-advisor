"""Same prompt, different models: run A1, A2, A3, A6 once per model and record everything.

Inputs are fixed per agent (fixture inputs, stub retrieval, current-date cutoff), so every
model sees byte-identical prompts. A2 gets the fixture A1 output, not the model's own A1.

    python scripts/model_compare.py                          # default model set
    python scripts/model_compare.py --models deepseek-ai/DeepSeek-V4-Flash-0731 zai-org/GLM-5.1
    python scripts/model_compare.py --agents a1_disease      # one agent only

Writes runs/model_compare/<timestamp>/:
    prompts/<agent>.json        the exact messages sent (identical across models)
    <model>/trace.json          every HTTP call: raw output, tokens, latency, finish_reason, errors
    <model>/<agent>.json        parsed output (after post-check) or the error
    summary.json, summary.md, summary.xlsx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402

from agents.analysis import a1_disease, a2_mechanism, a3_profile, a6_redteam  # noqa: E402
from agents.analysis._common import iter_claims  # noqa: E402
from agents.analysis.context import FIXTURES, Budget, BudgetExceeded, NebiusLLM, RunContext  # noqa: E402
from agents.analysis.retrieval import StubRetrieval  # noqa: E402
from agents.analysis.schemas import HypothesisCard, RedTeamInput, RunConfig, WhyNow  # noqa: E402

DEFAULT_MODELS = [
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "zai-org/GLM-5.1",
    "nvidia/Nemotron-3_5-Lightning",
    "Qwen/Qwen3.5-397B-A17B",
    "openai/gpt-oss-120b",
]
AGENTS = ["a1_disease", "a2_mechanism", "a3_profile", "a6_redteam"]
AS_OF = date.today()
A3_CARD = "h2"  # IL-4Ra: clean case
A6_CARD = "h5"  # IL-5: should be killed (failed Ph3 + retracted support)


def fx(name):
    return json.loads((FIXTURES / name).read_text())


def slug(m: str) -> str:
    return m.replace("/", "__")


async def run_agent(agent: str, ctx) -> object:
    why = WhyNow.model_validate(fx("a1_disease_out.json"))
    cards = {c["id"]: c for c in fx("a2_mechanism_out.json")}
    if agent == "a1_disease":
        return await a1_disease.run(ctx.config, ctx)
    if agent == "a2_mechanism":
        return await a2_mechanism.run(why, ctx)
    if agent == "a3_profile":
        return await a3_profile.run(HypothesisCard.model_validate(cards[A3_CARD]), ctx)
    if agent == "a6_redteam":
        return await a6_redteam.run(RedTeamInput(card=cards[A6_CARD],
                                                 profile=fx("a3_profile_out.json")[A6_CARD],
                                                 why_now=why), ctx)
    raise ValueError(agent)


def score(agent: str, out, ctx) -> dict:
    """Cheap, objective signals per agent (not a quality grade)."""
    claims = list(iter_claims(out if not isinstance(out, list) else out))
    pc = ctx.signals.get("postcheck", {}).get(agent, [])
    if isinstance(pc, dict):
        pc = [x for v in pc.values() for x in v]
    s = {"claims": len(claims),
         "evidence_claims": sum(c.kind == "evidence" for c in claims),
         "citations_stripped": sum(len(x.get("ids", [])) for x in pc if x["action"] == "stripped_sources"),
         "downgraded": sum(x["action"] == "downgraded_to_reasoning" for x in pc)}
    if agent == "a1_disease":
        s.update(shifts=len(out.shifts), negative=sum(x.type == "negative" for x in out.shifts),
                 segments=len(out.segments), ps_problems=a1_disease.check(out))
    elif agent == "a2_mechanism":
        s.update(cards=len(out), ranking=[f"{c.id}:{c.mechanism[:30]} t{c.evidence_tier}/{c.validation_level}"
                                          for c in out],
                 rejected=[p["hypothesis_id"] + ": " + p["reason"]
                           for _, e, p in ctx.bus.log if e == "card_rejected"])
    elif agent == "a3_profile":
        s.update(must_haves=out.must_haves)
    elif agent == "a6_redteam":
        cited = {sid for c in claims for sid in c.source_ids}
        s.update(verdict=out.verdict, revise_target=out.revise_target, risks=len(out.risks),
                 cites_retraction="fx:rw-2020-001" in cited,
                 cites_prior_failure=bool(cited & {"fx:pm-galathea-2019", "fx:pm-metrex-2017",
                                                   "fx:pm-crl-mepo-2018", "fx:nct-galathea"}))
    return s


async def run_model(model: str, agents: list[str], out_dir: Path, budget_kw: dict,
                    prompts: dict) -> list[dict]:
    d = out_dir / slug(model)
    d.mkdir(parents=True, exist_ok=True)
    llm = NebiusLLM("dev", budget=Budget(**budget_kw), model_override=model)
    rows = []
    for agent in agents:
        cfg = RunConfig(disease="COPD", disease_id="EFO_0000341", as_of=AS_OF,
                        mode="single_shot", profile="dev")
        ctx = RunContext(config=cfg, retrieval=StubRetrieval(AS_OF), llm=llm)
        n0, t0 = len(llm.trace), time.time()
        row = {"model": model, "agent": agent}
        try:
            out = await run_agent(agent, ctx)
            row.update(status="ok", **score(agent, out, ctx))
            dump = [c.model_dump(mode="json") for c in out] if isinstance(out, list) \
                else out.model_dump(mode="json")
            (d / f"{agent}.json").write_text(json.dumps({"output": dump, "signals": ctx.signals},
                                                        indent=1, default=str))
        except BudgetExceeded as e:
            row.update(status="budget_stop", error=str(e))
        except Exception as e:  # noqa: BLE001 - record and move on
            row.update(status="error", error=f"{type(e).__name__}: {str(e)[:400]}")
            (d / f"{agent}.json").write_text(json.dumps({"error": row["error"]}, indent=1))
        calls = llm.trace[n0:]
        if calls and agent not in prompts:
            prompts[agent] = calls[0]["messages"]
        row.update(calls=len(calls), input_tokens=sum(c.get("input_tokens", 0) for c in calls),
                   output_tokens=sum(c.get("output_tokens", 0) for c in calls),
                   seconds=round(time.time() - t0, 1),
                   finish=",".join(str(c.get("finish_reason")) for c in calls),
                   schema_retries=sum(c.get("valid") is False for c in calls))
        rows.append(row)
        print(f"  {model:40} {agent:13} {row['status']:11} calls={row['calls']} "
              f"in={row['input_tokens']} out={row['output_tokens']} {row['seconds']}s "
              f"{row.get('error', '')[:90]}", flush=True)
        if row["status"] == "budget_stop":
            break
    (d / "trace.json").write_text(json.dumps(
        [{k: v for k, v in c.items() if k != "messages"} for c in llm.trace], indent=1, default=str))
    (d / "budget.json").write_text(json.dumps(llm.budget.summary(), indent=1))
    return rows


def write_summary(out_dir: Path, rows: list[dict]) -> None:
    (out_dir / "summary.json").write_text(json.dumps(rows, indent=1, default=str))
    keys = ["model", "agent", "status", "calls", "schema_retries", "input_tokens", "output_tokens",
            "seconds", "finish", "claims", "evidence_claims", "citations_stripped", "downgraded",
            "shifts", "negative", "segments", "ps_problems", "cards", "ranking", "rejected",
            "must_haves", "verdict", "revise_target", "risks", "cites_retraction",
            "cites_prior_failure", "error"]
    wb = Workbook()
    ws = wb.active
    ws.title = "by_model_agent"
    ws.append(keys)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([json.dumps(r[k]) if isinstance(r.get(k), (list, dict)) else r.get(k, "")
                   for k in keys])
    ws.freeze_panes = "C2"
    tot = wb.create_sheet("totals_by_model")
    tot.append(["model", "ok", "of", "calls", "input_tokens", "output_tokens", "seconds"])
    for m in dict.fromkeys(r["model"] for r in rows):
        rs = [r for r in rows if r["model"] == m]
        tot.append([m, sum(r["status"] == "ok" for r in rs), len(rs), sum(r["calls"] for r in rs),
                    sum(r["input_tokens"] for r in rs), sum(r["output_tokens"] for r in rs),
                    round(sum(r["seconds"] for r in rs), 1)])
    wb.save(out_dir / "summary.xlsx")

    md = ["| model | agent | status | calls | out tok | s | key result |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        key = {"a1_disease": lambda: f"{r.get('shifts')} shifts ({r.get('negative')} neg), "
                                     f"{r.get('segments')} segs, problems={len(r.get('ps_problems') or [])}",
               "a2_mechanism": lambda: f"{r.get('cards')} cards; rejected {len(r.get('rejected') or [])}",
               "a3_profile": lambda: "; ".join(r.get("must_haves") or [])[:80],
               "a6_redteam": lambda: f"{r.get('verdict')} ({r.get('risks')} risks) "
                                     f"retraction={r.get('cites_retraction')} failure={r.get('cites_prior_failure')}",
               }[r["agent"]]() if r["status"] == "ok" else r.get("error", "")[:80]
        md.append(f"| {r['model'].split('/')[-1]} | {r['agent']} | {r['status']} | {r['calls']} | "
                  f"{r['output_tokens']} | {r['seconds']} | {key} |")
    (out_dir / "summary.md").write_text("\n".join(md) + "\n")


async def main_async(args) -> None:
    out_dir = ROOT / "runs" / "model_compare" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    budget_kw = dict(max_calls=args.max_calls, max_output_tokens=args.max_out,
                     max_total_tokens=args.max_total, max_tokens_per_call=args.per_call)
    print(f"out: {out_dir}\nper-model budget: {budget_kw}\n")
    prompts: dict = {}
    results = await asyncio.gather(*(run_model(m, args.agents, out_dir, budget_kw, prompts)
                                     for m in args.models))
    rows = [r for rs in results for r in rs]
    (out_dir / "prompts").mkdir(exist_ok=True)
    for agent, msgs in prompts.items():
        (out_dir / "prompts" / f"{agent}.json").write_text(json.dumps(msgs, indent=1))
    write_summary(out_dir, rows)
    print("\n" + (out_dir / "summary.md").read_text())
    print("totals: calls=%d in=%d out=%d" % (sum(r["calls"] for r in rows),
                                             sum(r["input_tokens"] for r in rows),
                                             sum(r["output_tokens"] for r in rows)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--agents", nargs="+", default=AGENTS, choices=AGENTS)
    ap.add_argument("--max-calls", type=int, default=0, help="per model")
    ap.add_argument("--max-out", type=int, default=0, help="output tokens per model")
    ap.add_argument("--max-total", type=int, default=0, help="in+out tokens per model")
    ap.add_argument("--per-call", type=int, default=16_000, help="max_tokens per call")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
