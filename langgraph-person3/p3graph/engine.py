"""Link to person 3's tested agents in ../frontier-engine (one source of truth).

Override the location with FRONTIER_ENGINE_PATH. Loads ../frontier-engine/.env (Nebius key).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENGINE = Path(os.getenv("FRONTIER_ENGINE_PATH",
                        Path(__file__).resolve().parents[2])).resolve()
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE / ".env")

from agents.analysis import a1_disease, a2_mechanism, a3_profile, a6_redteam, a7_judge  # noqa: E402,F401
from agents.analysis import tool_loop  # noqa: E402,F401
from agents.analysis._common import EvidencePack, system_prompt  # noqa: E402,F401
from agents.analysis.context import (  # noqa: E402,F401
    EventBus, LLMError, NebiusLLM, RunContext, StubLLM, Usage, complete_raw, fix_messages, parse_output, model_for,
    BACKTEST_AS_OF, resolve_as_of,
)
from agents.analysis.retrieval import HttpRetrieval, StubRetrieval  # noqa: E402,F401
from agents.analysis.retrieval_web import SEARCH_SOURCES, WebSearchError, make_retrieval  # noqa: E402,F401
from agents.analysis.schemas import (  # noqa: E402,F401
    A2Output, HypothesisCard, InactiveAssets, JudgeDecision, Landscape, ProductProfile, RedTeamInput, RunConfig,
    Verdict, WhyNow,
)
from exports.analysis_workbook import AnalysisWorkbook  # noqa: E402,F401
from agents.analysis.retrieval_db import DatabaseError, DatabaseRetrieval, build_select, make_db  # noqa: E402,F401
