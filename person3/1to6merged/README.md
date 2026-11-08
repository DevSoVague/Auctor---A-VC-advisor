# 1to6merged: updated merged build (agents A1 to A7)

The newer, self-contained version of Person 3's work. Compared with `../frontier-engine` + `../langgraph-person3`:

- The evidence store is wired to every agent as LLM tool calls (`agents/analysis/db_tools.py`, `tool_loop.py`, `db_ingest.py`).
- New judge agent A7 (`agents/analysis/a7_judge.py`, prompt `agents/prompts/a7_judge.md`). It sees only A3's and A6's outputs.
- LangGraph gets a `toolloop` step in every chain, an `a7_judge` node and the flags `--tools`, `--no-judge`, `--max-cards`, `--search evstore`.
- The workbook export gains `ToolCalls`, `NewEvidence` and `Judge` sheets.
- Competitor agents A4 and A5 are included.

Run it from this folder:

```bash
cp .env.example .env          # add NEBIUS_API_KEY (and TAVILY_API_KEY for web search); never commit .env
pip install -r requirements-person3.txt
LLM_MODE=stub python -m pytest -q
cat langgraph-person3/docs/HOW_TO_RUN.md
```

Not committed on purpose: `.env`, `runs/`, backups and caches.
