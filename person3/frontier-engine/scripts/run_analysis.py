"""Run person 3's agents end to end and watch analysis.xlsx fill in live.

    python scripts/run_analysis.py                          # stub LLM, stub retrieval, as_of 2021-01-01
    python scripts/run_analysis.py --as-of today --llm live # Nebius, stub retrieval
    python scripts/run_analysis.py --retrieval http://localhost:8000 --llm live --profile final

Env (frontier-engine/.env): NEBIUS_API_KEY, NEBIUS_BASE_URL, optional NEBIUS_MODEL_<AGENT>.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from agents.analysis.chain import run_chain  # noqa: E402
from agents.analysis.context import NebiusLLM, RunContext, StubLLM  # noqa: E402
from agents.analysis.retrieval import HttpRetrieval, StubRetrieval  # noqa: E402
from agents.analysis.schemas import RunConfig  # noqa: E402
from exports.analysis_workbook import AnalysisWorkbook  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2021-01-01", help="YYYY-MM-DD or 'today'")
    ap.add_argument("--llm", choices=["stub", "live"], default=os.getenv("LLM_MODE", "stub"))
    ap.add_argument("--profile", choices=["dev", "final"], default="dev")
    ap.add_argument("--retrieval", default="stub", help="'stub' or the retrieval API base URL")
    ap.add_argument("--audience", choices=["specialist", "generalist"], default="generalist")
    args = ap.parse_args()

    as_of = date.today() if args.as_of == "today" else date.fromisoformat(args.as_of)
    cfg = RunConfig(disease="COPD", disease_id="EFO_0000341", as_of=as_of,
                    mode="single_shot", profile=args.profile, audience=args.audience)
    retrieval = StubRetrieval(as_of) if args.retrieval == "stub" else HttpRetrieval(as_of, args.retrieval)
    llm = StubLLM() if args.llm == "stub" else NebiusLLM(args.profile)
    ctx = RunContext(config=cfg, retrieval=retrieval, llm=llm)

    out_path = ROOT / "runs" / ctx.run_id / "analysis.xlsx"
    AnalysisWorkbook(ctx, out_path).attach()
    ctx.bus.subscribe(lambda e, p: print(f"  [{e}] {p.get('agent','')} {p.get('hypothesis_id') or ''}")
                      if e != "claim_added" else None)

    print(f"run {ctx.run_id}  as_of={as_of}  llm={args.llm}  retrieval={args.retrieval}")
    res = asyncio.run(run_chain(ctx))

    print("\nsurvivors:", res["survivors"], " killed:", res["killed"])
    for hid, r in res["results"].items():
        c = r["card"]
        print(f"  {hid} tier{c.evidence_tier}/{c.validation_level:6} {r['verdict'].verdict:8} {c.mechanism}")
    (out_path.parent / "result.json").write_text(json.dumps({
        "why_now": res["why_now"].model_dump(mode="json"),
        "cards": [c.model_dump(mode="json") for c in res["cards"]],
        "results": {h: {"profile": r["profile"].model_dump(mode="json"),
                        "verdict": r["verdict"].model_dump(mode="json"), "revised": r["revised"]}
                    for h, r in res["results"].items()},
        "signals": res["signals"]}, indent=1, default=str))
    print("\nworkbook:", out_path)


if __name__ == "__main__":
    main()
