"""The chat narration: one message per node, written to runs/<run_id>/chat.md and streamed."""
import asyncio

from auctor import narrate as NR


def test_narrate_step_and_files(tmp_path, monkeypatch):
    monkeypatch.setattr(NR, "RUNS", tmp_path)
    got = []
    NR.say(got.append, "r1", "A3 · h1 · llm", "🤖", "a3_llm", "The model answered.", 12, "h1")
    assert got[0]["event"] == "narration" and got[0]["text"] == "The model answered."
    assert "The model answered." in (tmp_path / "r1" / "chat.md").read_text()


def test_postcheck_sentence():
    w = {"postcheck_log": [{"claim_id": "c1", "action": "stripped_sources", "ids": ["x:1"]}]}
    t = NR.narrate_step("a3_profile", "postcheck", "h1", {}, {}, {}, w)
    assert "removed unknown source(s) x:1" in t and "{" not in t
