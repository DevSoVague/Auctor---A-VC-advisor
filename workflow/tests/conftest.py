"""Keep test runs out of the real runs/ folder: every test writes to its own tmp dir."""

import pytest


@pytest.fixture(autouse=True)
def _tmp_runs(tmp_path, monkeypatch):
    from p3graph import live, narrate, nodes

    for mod in (nodes, live, narrate):
        monkeypatch.setattr(mod, "RUNS", tmp_path / "runs")
    yield
