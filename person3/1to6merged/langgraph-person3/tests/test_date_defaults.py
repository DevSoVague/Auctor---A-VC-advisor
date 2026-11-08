"""Current-date defaults must not depend on model mode or fixture data."""
import asyncio
from datetime import date
import pytest
from p3graph import nodes as N, live, engine as E

@pytest.mark.parametrize("mode", ["stub", "live"])
def test_setup_defaults_to_today(mode, tmp_path, monkeypatch):
    monkeypatch.setattr(live, "RUNS", tmp_path)
    monkeypatch.setattr(N, "RUNS", tmp_path)
    monkeypatch.setattr(live, "ensure_server", lambda: None)
    result = asyncio.run(N.setup({"llm": {"mode": mode}}))
    assert result["config"]["as_of"] == date.today().isoformat()
    if hasattr(E, "resolve_as_of"):
        assert E.resolve_as_of(None, mode) == date.today()
        assert E.resolve_as_of("today", mode) == date.today()
        assert E.resolve_as_of("2024-06-01", mode) == date(2024, 6, 1)
        assert E.resolve_as_of("backtest", mode) == E.BACKTEST_AS_OF
