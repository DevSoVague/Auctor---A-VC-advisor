"""Single import point for schemas inside agents/analysis.

Uses person 2's agents/schemas.py when it exists, else our PRD-verbatim draft.
Person-3 additions (A2Output, RedTeamInput) always come from the draft until
person 2 merges them.
"""

from __future__ import annotations

try:  # HOOK IN (person 2)
    from agents.schemas import (  # type: ignore[import-not-found]
        Asset, Catalyst, Claim, HypothesisCard, InactiveAssets, Landscape,
        ProductProfile, Program, Risk, RunConfig, Segment, Shift, Verdict, WhyNow,
    )
    SCHEMA_SOURCE = "agents.schemas"
except ImportError:
    from agents.analysis._schemas_draft import (  # noqa: F401
        Asset, Catalyst, Claim, HypothesisCard, InactiveAssets, Landscape,
        ProductProfile, Program, Risk, RunConfig, Segment, Shift, Verdict, WhyNow,
    )
    SCHEMA_SOURCE = "draft"

try:
    from agents.schemas import A2Output, RedTeamInput  # type: ignore[import-not-found]
except ImportError:
    from agents.analysis._schemas_draft import A2Output, RedTeamInput  # noqa: F401
try:
    from agents.schemas import A4Input, A5Input  # type: ignore[import-not-found]
except ImportError:
    from agents.analysis._schemas_draft import A4Input, A5Input  # noqa: F401
try:
    from agents.schemas import JudgeDecision  # type: ignore[import-not-found]
except ImportError:
    from agents.analysis._schemas_draft import AttackRuling, JudgeDecision  # noqa: F401
