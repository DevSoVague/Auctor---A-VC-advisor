"""Run API over Person 3's pipeline, end to end in stub mode (0 tokens)."""

import io
import os
import time
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontier.api import runs as R

# Offline and 0 tokens: the fixture corpus, as the app's test mode sends. Real runs default to
# the evidence store (evstore+web), which needs a database CI does not have.
STUB = {"llm": "stub", "as_of": "2021-01-01", "max_cards": 1, "search": "corpus"}


TOKEN = "test-token"


@pytest.fixture(scope="module")
def client():
    """A test token of our own: Person 3's engine loads the repo's .env (never overriding), so a
    developer's real APP_API_TOKEN would otherwise switch the token check on mid-test."""
    from langgraph.checkpoint.memory import InMemorySaver

    @asynccontextmanager
    async def lifespan(app):
        await R.runner.start(checkpointer=InMemorySaver())
        yield
        await R.runner.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(R.router)
    old = os.environ.get("APP_API_TOKEN")
    os.environ["APP_API_TOKEN"] = TOKEN
    try:
        with TestClient(app, headers={"X-API-Key": TOKEN}) as c:
            yield c
    finally:
        if old is None:
            os.environ.pop("APP_API_TOKEN", None)
        else:
            os.environ["APP_API_TOKEN"] = old


def wait(client, run_id, timeout=90):
    """Poll until the run stops running (paused, done or error)."""
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} still running after {timeout}s")


def test_guided_run_pauses_at_each_review_and_finishes(client):
    run_id = client.post("/runs", json={**STUB, "mode": "guided"}).json()["run_id"]
    body = wait(client, run_id)
    assert body["status"] == "paused", body.get("error")

    first = client.get(f"/runs/{run_id}/checkpoint").json()
    assert first["checkpoint"] == "a1_disease"
    assert first["summary"] and first["output"]
    assert "a1_disease" in first["citations"]  # the A7 citation check for this step

    seen = [first["checkpoint"]]
    for _ in range(20):  # approve every review until the run ends
        r = client.post(f"/runs/{run_id}/checkpoint", json={"action": "approve"})
        assert r.status_code == 200
        body = wait(client, run_id)
        if body["status"] != "paused":
            break
        seen.append(client.get(f"/runs/{run_id}/checkpoint").json()["checkpoint"])
    assert body["status"] == "done", body.get("error")
    assert seen[:2] == ["a1_disease", "a2_mechanism"] and "a3_profile" in seen

    run = client.get(f"/runs/{run_id}").json()
    assert run["step_count"] > 20 and run["citations"]
    assert "total_usd" in run["cost"]
    assert client.get(f"/runs/{run_id}/report").text.strip()

    names = [f["name"] for f in client.get(f"/runs/{run_id}/files").json()]
    assert "report.md" in names
    assert client.get(f"/runs/{run_id}/files/report.md").status_code == 200
    assert client.get(f"/runs/{run_id}/files/..%2F..%2Fworkflow.yaml").status_code == 404


def test_single_shot_run_goes_straight_to_done(client):
    run_id = client.post("/runs", json={**STUB, "mode": "single_shot"}).json()["run_id"]
    assert wait(client, run_id)["status"] == "done"
    assert client.get(f"/runs/{run_id}/citations").json()
    assert any(r["run_id"] == run_id for r in client.get("/runs").json())


def test_token_is_required_once_set(client, monkeypatch):
    monkeypatch.setenv("APP_API_TOKEN", "s3cret")
    assert client.get("/runs").status_code == 401  # the client's default test key is now wrong
    bearer = lambda t: {"X-API-Key": "", "Authorization": f"Bearer {t}"}  # noqa: E731
    assert client.get("/runs", headers=bearer("wrong")).status_code == 401
    assert client.get("/runs", headers=bearer("s3cret")).status_code == 200
    assert client.get("/runs", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_resume_rules(client):
    run_id = client.post("/runs", json={**STUB, "mode": "single_shot"}).json()["run_id"]
    wait(client, run_id)
    assert client.post(f"/runs/{run_id}/checkpoint", json={"action": "approve"}).status_code == 409
    assert client.get(f"/runs/{run_id}/checkpoint").status_code == 409
    assert client.get("/runs/nope").status_code == 404

    guided = client.post("/runs", json={**STUB, "mode": "guided"}).json()["run_id"]
    wait(client, guided)
    r = client.post(f"/runs/{guided}/checkpoint", json={"action": "edit"})
    assert r.status_code == 422  # edit needs edited_output


def test_result_json_carries_everything_the_memo_reads(client):
    """A8 (person 5) can rebuild a finished run from result.json alone, including A4/A5."""
    run_id = client.post("/runs", json={**STUB, "mode": "single_shot"}).json()["run_id"]
    assert wait(client, run_id)["status"] == "done"
    result = client.get(f"/runs/{run_id}/files/result.json").json()
    assert {"assets", "judgments", "revise_used", "verdicts", "killed", "signals"} <= result.keys()
    assert result["assets"]  # A4/A5 outputs, keyed '<hid>:inactive' and '<hid>:landscape'


def test_a_finished_run_gets_the_memo_files(client):
    """Step 5: when a run finishes, agent 8's memo is written next to the report and workbook."""
    from docx import Document

    run_id = client.post("/runs", json={**STUB, "mode": "single_shot"}).json()["run_id"]
    body = wait(client, run_id)
    assert body["status"] == "done" and body["memo_error"] is None
    assert body["memo"]["docx"] == "memo.docx" and body["memo"]["xlsx"] == "programs.xlsx"
    names = {f["name"] for f in client.get(f"/runs/{run_id}/files").json()}
    assert {"memo.docx", "programs.xlsx", "result.json", "report.md"} <= names
    memo = client.get(f"/runs/{run_id}/files/memo.docx")
    assert memo.status_code == 200 and memo.content[:2] == b"PK"  # a real .docx (zip)
    assert Document(io.BytesIO(memo.content)).paragraphs


def test_a_memo_failure_never_fails_the_run(client, monkeypatch):
    import frontier.memo.run_memo as M

    async def boom(values, run_dir, pdf=True):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(M, "memo_for_run", boom)
    run_id = client.post("/runs", json={**STUB, "mode": "single_shot"}).json()["run_id"]
    body = wait(client, run_id)
    assert body["status"] == "done" and body["error"] is None
    assert "renderer exploded" in body["memo_error"] and body["memo"] is None


def test_no_memo_while_a_guided_run_is_paused(client):
    run_id = client.post("/runs", json={**STUB, "mode": "guided"}).json()["run_id"]
    body = wait(client, run_id)
    assert body["status"] == "paused" and body["memo"] is None
    assert "memo.docx" not in {f["name"] for f in client.get(f"/runs/{run_id}/files").json()}
