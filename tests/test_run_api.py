"""Run API over Person 3's pipeline, end to end in stub mode (0 tokens)."""

import time
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontier.api import runs as R

STUB = {"llm": "stub", "as_of": "2021-01-01", "max_cards": 1}


@pytest.fixture(scope="module")
def client():
    from langgraph.checkpoint.memory import InMemorySaver

    @asynccontextmanager
    async def lifespan(app):
        await R.runner.start(checkpointer=InMemorySaver())
        yield
        await R.runner.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(R.router)
    with TestClient(app) as c:
        yield c


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
    assert client.get("/runs").status_code == 401
    assert client.get("/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/runs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
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
