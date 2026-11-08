"""Sample LIVE tests against Nebius Token Factory. Skipped unless LLM_MODE=live.

    LLM_MODE=live pytest tests/test_live_nebius.py -v -s
    LLM_MODE=live LIVE_TEST_MODEL=Qwen/Qwen3.5-397B-A17B pytest tests/test_live_nebius.py -v -s

Cost: 1 tiny call (key check) + 1-2 calls (A1 on the fixture corpus). Usage is printed.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path
from typing import Literal

import pytest
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

pytestmark = pytest.mark.skipif(os.getenv("LLM_MODE") != "live",
                                reason="live Nebius test; run with LLM_MODE=live")

MODEL = os.getenv("LIVE_TEST_MODEL", "openai/gpt-oss-120b")


class Ping(BaseModel):
    status: Literal["ok"]
    disease: str


def _usage(llm) -> str:
    return ", ".join(f"{a}: {u.calls} calls in={u.input_tokens} out={u.output_tokens} "
                     f"{u.latency_ms} ms" for a, u in llm.usage.items())


def test_key_and_structured_output():
    from agents.analysis.context import NebiusLLM

    llm = NebiusLLM("dev", model_override=MODEL)
    out = asyncio.run(llm(agent="ping", schema=Ping, max_tokens=2000, messages=[
        {"role": "user", "content": 'Return JSON {"status": "ok", "disease": "COPD"}'}]))
    print(f"\n[{MODEL}] {_usage(llm)}")
    assert out.status == "ok" and out.disease.upper() == "COPD"
    assert llm.trace[-1]["valid"] is True


def test_a1_live_meets_ps_bars():
    from agents.analysis import a1_disease
    from agents.analysis.context import NebiusLLM, RunContext
    from agents.analysis.retrieval import StubRetrieval
    from agents.analysis.schemas import RunConfig

    as_of = date(2021, 1, 1)
    cfg = RunConfig(disease="COPD", disease_id="EFO_0000341", as_of=as_of,
                    mode="single_shot", profile="dev")
    llm = NebiusLLM("dev", model_override=MODEL)
    ctx = RunContext(config=cfg, retrieval=StubRetrieval(as_of), llm=llm)
    out = asyncio.run(a1_disease.run(cfg, ctx))
    print(f"\n[{MODEL}] {_usage(llm)}")
    for s in out.shifts:
        print(f"  {s.on} {s.type:10} {s.summary[:90]}")
    for g in out.segments:
        print(f"  segment: {g.name} | {'; '.join(g.inclusion_criteria)[:90]}")

    assert a1_disease.check(out) == []
    assert all(s.on < as_of for s in out.shifts)  # leakage
    cited = {sid for s in out.shifts for c in s.claims for sid in c.source_ids}
    assert cited and all(sid.startswith("fx:") for sid in cited)  # only pack ids survive
