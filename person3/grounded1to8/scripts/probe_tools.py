"""Probe which Nebius models return tool_calls, and whether tools + json_schema work in one call.

Run:  conda run -n hack python scripts/probe_tools.py [model ...]
Costs a few hundred tokens per model.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MODELS = sys.argv[1:] or [
    "openai/gpt-oss-120b",
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "Qwen/Qwen3.5-397B-A17B",
    "zai-org/GLM-5.1",
]

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_evidence",
        "description": "Search the COPD evidence store. Returns passages with publication dates.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
            "required": ["query"],
        },
    },
}]
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
          "additionalProperties": False}
MSGS = [{"role": "user", "content": "Find evidence on anti-IL-33 antibodies in COPD. Use the tool."}]


async def probe(api: AsyncOpenAI, model: str) -> dict:
    out: dict = {"model": model}
    # 1) tools only
    try:
        r = await api.chat.completions.create(model=model, messages=MSGS, tools=TOOLS, tool_choice="auto",
                                              max_tokens=800)
        m = r.choices[0].message
        out["tools"] = "tool_calls" if m.tool_calls else "no_call"
        if m.tool_calls:
            tc = m.tool_calls[0]
            out["args"] = tc.function.arguments
            # 2) round trip: feed a result back, expect a final answer
            msgs = MSGS + [m.model_dump(exclude_none=True),
                           {"role": "tool", "tool_call_id": tc.id,
                            "content": json.dumps([{"id": "pmid:1", "published": "2024-05-01",
                                                    "text": "Itepekimab reduced exacerbations in former smokers."}])}]
            r2 = await api.chat.completions.create(model=model, messages=msgs, tools=TOOLS, max_tokens=800)
            out["round_trip"] = "answer" if r2.choices[0].message.content else (
                "called_again" if r2.choices[0].message.tool_calls else "empty")
    except Exception as e:  # noqa: BLE001
        out["tools"] = f"error: {str(e)[:120]}"
    # 3) tools + json_schema response_format together
    try:
        r = await api.chat.completions.create(
            model=model, messages=MSGS, tools=TOOLS, tool_choice="auto", max_tokens=800,
            response_format={"type": "json_schema", "json_schema": {"name": "out", "schema": SCHEMA, "strict": True}})
        m = r.choices[0].message
        out["tools+schema"] = "tool_calls" if m.tool_calls else ("json" if m.content else "empty")
    except Exception as e:  # noqa: BLE001
        out["tools+schema"] = f"error: {str(e)[:120]}"
    return out


async def main():
    api = AsyncOpenAI(api_key=os.environ["NEBIUS_API_KEY"].strip(),
                      base_url=os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.us-central1.nebius.com/v1/"),
                      timeout=120)
    for res in await asyncio.gather(*(probe(api, m) for m in MODELS)):
        print(json.dumps(res))


if __name__ == "__main__":
    asyncio.run(main())
