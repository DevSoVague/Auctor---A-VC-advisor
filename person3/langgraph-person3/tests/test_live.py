"""Streaming without network: real OpenAI SDK parsing mocked SSE responses."""
import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from p3graph import engine as E
from p3graph import live
from agents.analysis.context import NebiusLLM


def client(monkeypatch, handler):
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    llm = NebiusLLM(model_override="local/test")
    llm.client = AsyncOpenAI(api_key="local", base_url="http://local/v1", max_retries=0,
                            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return llm


def sse(*chunks):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                         text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                         + "data: [DONE]\n\n")


def chunk(delta=None, finish=None, usage=None):
    return {"id": "call1", "object": "chat.completion.chunk", "created": 0, "model": "local/test",
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if not usage else [],
            "usage": usage}


def test_stream_reasoning_output_usage_and_exact_fallback_prompt(monkeypatch):
    events, requests = [], []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["stream"] is True
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "json_schema response_format unsupported"}})
        return sse(chunk({"reasoning_content": "Checking evidence…"}),
                   chunk({"content": '{"shifts":'}), chunk({"content": ' []}'}), chunk(finish="stop"),
                   chunk(usage={"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}))

    async def go():
        llm = client(monkeypatch, handler)
        llm.on_stream = lambda event, data: events.append((event, data))
        out = await E.complete_raw(llm, agent="a1_disease", messages=[{"role": "user", "content": "test"}],
                                   schema=E.WhyNow)
        assert out["text"] == '{"shifts": []}'
        assert out["reasoning"] == "Checking evidence…"
        assert out["input_tokens"] == 10 and llm.usage["a1_disease"].output_tokens == 8
        assert out["mode"] == "json_object"
        assert events[1][0] == "request"  # both actual requests are visible
        assert events[1][1]["messages"] == requests[1]["messages"]
        assert [d["channel"] for e, d in events if e == "delta"] == ["reasoning", "output", "output"]
        assert events[-1][0] == "response"
        await llm.client.close()

    asyncio.run(go())


def test_truncated_stream_fails_after_emitting_partial_output(monkeypatch):
    async def go():
        llm = client(monkeypatch, lambda request: sse(chunk({"content": "partial"})))
        events = []
        llm.on_stream = lambda e, d: events.append((e, d))
        with pytest.raises(E.LLMError, match="without a finish reason"):
            await E.complete_raw(llm, agent="a1_disease", messages=[], schema=E.WhyNow)
        assert events[-1] == ("delta", {"channel": "output", "text": "partial"})
        assert llm.trace[-1]["error"]
        await llm.client.close()

    asyncio.run(go())


def test_step_monitor_captures_prompts_and_errors(tmp_path, monkeypatch):
    from p3graph import nodes as N, steps as S
    monkeypatch.setattr(live, "RUNS", tmp_path)
    monkeypatch.setattr(N, "LLM_FACTORY", lambda state: E.StubLLM())

    async def go():
        state = {"run_id": "test", "config": {"as_of": "2021-01-01", "disease": "COPD",
                 "disease_id": "EFO_0000341", "mode": "single_shot", "profile": "dev"},
                 "llm": {"mode": "stub"}}
        result = await S.a1_retrieve(state)
        state["work"] = result["work"]
        result = await S.a1_prompt(state)
        state["work"] = result["work"]
        await S.llm_step(S.A1)(state)
        state["work"] = {}
        with pytest.raises(KeyError):
            await S.llm_step(S.A1)(state)

    asyncio.run(go())
    events = [json.loads(line) for line in (tmp_path / "test/live.jsonl").read_text().splitlines()]
    starts = [e for e in events if e["event"] == "start" and e["node"] == "a1_llm"]
    assert starts[0]["model"] == "stub"
    assert {m["role"] for m in starts[0]["messages"]} == {"system", "user"}
    assert events[-1]["event"] == "error"
    assert events[-1]["id"] != starts[0]["id"]  # repeated nodes stay distinct
