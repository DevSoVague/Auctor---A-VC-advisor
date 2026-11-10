"""Web search (Tavily) retrieval option. The HTTP call is mocked: 0 tokens, no key, no network."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auctor import engine as E  # noqa: E402
from auctor import nodes as N  # noqa: E402
from auctor.graph import build  # noqa: E402
from agents.analysis.retrieval_web import TavilySearch, WebSearchError, WithWebSearch  # noqa: E402
from test_graph import cfg, drive, inp, run  # noqa: E402

AS_OF = date(2021, 1, 1)


def tavily(results, seen=None, status=200):
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append({"headers": dict(req.headers), "body": json.loads(req.content)})
        return httpx.Response(status, json={"results": results})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def hit(url, published, content="snippet"):
    return {"title": "T " + url, "url": url, "content": content, "published_date": published, "score": 0.9}


def test_request_has_bearer_key_and_day_before_end_date():
    seen = []
    web = TavilySearch(AS_OF, api_key="tvly-test", client=tavily([], seen))
    run(web.search("COPD IL-4R", k=5))
    assert seen[0]["headers"]["authorization"] == "Bearer tvly-test"
    b = seen[0]["body"]
    assert b["end_date"] == "2020-12-31" and b["include_published_date"] is True and b["max_results"] == 5


def test_backtest_drops_future_and_undated_results():
    res = [hit("https://a.org/old", "Tue, 10 Mar 2020 00:00:00 GMT"),
           hit("https://a.org/future", "2023-05-01"),
           hit("https://a.org/on-cutoff", "2021-01-01"),
           hit("https://a.org/undated", None)]
    docs = run(TavilySearch(AS_OF, api_key="k", client=tavily(res)).search("x"))
    assert [d["url"] for d in docs] == ["https://a.org/old"]
    assert docs[0]["id"].startswith("web:") and docs[0]["source"] == "web" and docs[0]["source_date"] == "2020-03-10"


def test_live_run_keeps_undated_but_labels_them():
    today = date.today() + timedelta(days=1)
    docs = run(TavilySearch(today, api_key="k", client=tavily([hit("https://a.org/u", None)])).search("x"))
    assert docs[0]["tags"] == ["web", "undated"] and docs[0]["source_date"] == date.today().isoformat()


def test_missing_key_and_bad_key_give_clear_errors(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(WebSearchError, match="TAVILY_API_KEY is not set"):
        run(TavilySearch(AS_OF, api_key="").search("x"))
    with pytest.raises(WebSearchError, match="401"):
        run(TavilySearch(AS_OF, api_key="bad", client=tavily([], status=401)).search("x"))


def test_modes_corpus_web_both(monkeypatch):
    web = TavilySearch(AS_OF, api_key="k", client=tavily([hit("https://a.org/w", "2020-01-01")]))
    base = E.StubRetrieval(AS_OF)
    corpus_ids = {d["id"] for d in run(base.search("COPD eosinophil", k=6))}
    assert corpus_ids  # the stub corpus has results for this query
    only_web = run(WithWebSearch(base, web, "web").search("COPD eosinophil", k=6))
    assert [d["source"] for d in only_web] == ["web"]
    both = run(WithWebSearch(base, web, "both").search("COPD eosinophil", k=6))
    assert {d["id"] for d in both} >= corpus_ids and any(d["source"] == "web" for d in both)
    # structured lookups always come from the corpus side
    assert run(WithWebSearch(base, web, "web").trials("COPD"))
    # a cited web doc can be re-read by id (A6 does this)
    wid = only_web[0]["id"]
    assert run(WithWebSearch(base, web, "web").doc(wid))["url"] == "https://a.org/w"


def test_make_retrieval_validates_the_option():
    assert isinstance(E.make_retrieval(AS_OF, "stub", "corpus"), E.StubRetrieval)
    with pytest.raises(ValueError, match="corpus, web, both|search source"):
        E.make_retrieval(AS_OF, "stub", "bing")


def test_graph_with_web_search_puts_web_docs_in_the_prompt_and_trace(monkeypatch):
    web = TavilySearch(AS_OF, api_key="k", client=tavily([hit("https://pubmed.org/1", "2020-06-01",
                                                               "WEBSNIPPET about IL-4R")]))
    monkeypatch.setattr(E, "make_retrieval", lambda as_of, r, s: WithWebSearch(E.StubRetrieval(as_of), web, s))
    llm = E.StubLLM()
    N.LLM_FACTORY = lambda state: llm
    try:
        x = inp()
        x["search"] = "both"
        s = run(drive(build(InMemorySaver()), x, cfg())).values
    finally:
        N.LLM_FACTORY = None
    prompts = " ".join(m["content"] for c in llm.calls for m in c["messages"])
    assert "WEBSNIPPET about IL-4R" in prompts
    assert s["search"] == "both"
    assert any("from the web" in r["detail"] for r in s["steps"] if r["node"].endswith("_retrieve"))
