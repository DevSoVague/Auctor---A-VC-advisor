# Handoff: db1to6merged

The COPD agents 1–7 with the evidence store wired in as tool calls. This folder is a copy of `1to6merged`, which itself was not changed.

## What's new compared with 1to6merged

| Change | Where | What it does |
|---|---|---|
| DB tools | `agents/analysis/db_tools.py` | Every agent can query the evidence store through tool calls. Every result carries its publication date (`published`, `date_kind`, `age_days`). |
| Tool loop | `agents/analysis/tool_loop.py` | Two phases: the model calls tools, then the usual structured JSON call. Nebius models skip tools when both are sent together. |
| A1 ingest | `agents/analysis/db_ingest.py` | A1 searches PubMed and ClinicalTrials.gov, checks for duplicates, downloads, and stores records verbatim with their official date. Undated, out-of-window and duplicate items are rejected. |
| A6 checkers | `tool_loop.with_checkers` | Four parallel checkers (safety, already-exists, market, evidence) feed the red team. |
| A7 judge | `agents/analysis/a7_judge.py`, `agents/prompts/a7_judge.md` | Sees only A3's and A6's outputs, with no tools. Decides advance, revise or kill, and that decision drives the routing. |
| LangGraph | `langgraph-person3/p3graph/` | A `toolloop` step in each chain and an `a7_judge` node. CLI flags `--tools`, `--no-judge`, `--max-cards` and `--search evstore`. |
| Workbook | `exports/analysis_workbook.py` | New sheets: ToolCalls, NewEvidence, Judge. |
| Ports | `live.py`, `run_*.sh` | Monitor 7872, Studio 2034, Langflow 7870, so this copy doesn't collide with 1to6merged. |
| Tests | `tests/test_db_tools.py`, `langgraph-person3/tests/conftest.py` | 150 engine and 50 LangGraph tests pass. Tests no longer write to `runs/`. |

## Rules

- **Data window:** `2023-01-01 <= published < as_of`. COVID-era data (2020–22) is excluded. Override with `DATA_FLOOR`.
- **Open Targets** is a reference table, so any release before `REFERENCE_CUTOFF` (2025-01-01) is allowed.
- **Tool access:**

  | Agent | DB read | DB write | Web search |
  |---|---|---|---|
  | A1 | ✓ | ✓ | ✓ |
  | A2, A3, A6 | ✓ | | |
  | A4, A5 | ✓ | | ✓ |
  | A7 | | | |

  It is enforced in `db_tools.ACCESS`.
- **Linear V1:** 1 → 2 → 3 → 4 → 5 → 6 → 7 per card, with one revise round per card.

## Required

1. **Conda env `hack`:** `pip install -r requirements-person3.txt psycopg[binary] pgvector numpy langgraph`.
2. **The evidence store:** the folder `../evidence-store` (or set `EVSTORE_PATH`) and its Postgres running on `localhost:5433`. Start it with `cd ../evidence-store && make db-up`. To rebuild it: `make cache-import && make all`.
3. **`.env`** (not in git; copy `.env.example`):
   - `NEBIUS_API_KEY`: required.
   - `TAVILY_API_KEY`: optional, but `web_search` for A1, A4 and A5 needs it.
   - `NCBI_API_KEY`: optional, for faster PubMed.
4. **Optional limits:**

   | Variable | Default |
   |---|---|
   | `A1_MAX_INGESTS` | 5 |
   | `A1_INGEST_DRY_RUN` | 0 |
   | `A6_CHECKERS` | all four |
   | `TOOL_MAX_CALLS` | 12 |
   | `TOOL_MAX_ROUNDS` | 4 |

## How to run

```bash
cd langgraph-person3
# offline stub run (0 tokens)
conda run -n hack python -m p3graph run
# live, one card, tools + judge
conda run -n hack python -m p3graph run --llm live --as-of 2026-01-01 --search evstore --tools --max-cards 1
# tests
conda run -n hack python -m pytest -q          # also in the repo root
```

**Output:** `langgraph-person3/runs/<run_id>/`, containing `analysis.xlsx`, `report.md`, `chat.md` and `steps.md`.

**Live monitor:** http://127.0.0.1:7872

## Last live run (2026-11-08, one card)

- **A1** ingested 5 new papers dated Sep–Dec 2025.
- **A2** kept 4 cards; the top card, h2, was tier 1 / gold.
- **A6** said revise. **A7** ruled the red team right and sent h2 back to A3 once. In the second round A7 ruled the red team partly right, and the card passed on the one-revise limit.
- **Tool calls:** 105 in total, every result dated.
- **Cost:** about 610k input and 85k output tokens on DeepSeek V4 Flash.

## Open items

- **A4 and A5 placed 0 assets** on the store data. Their gather logic relies on ChEMBL naming, which has nothing to match. This is teammate code and was not changed.
- **Ask person 1 to ingest:**
  - Open Targets into the store's `targets` table. A2 is using the fixture's 2020-11 release meanwhile.
  - Google News records.
  - Market data dated before 2025, so backtests can see it.
- **No Tavily key yet:** `web_search` returns an error and the agents carry on without it.
- **`--profile final`:** Qwen3.5 returned truncated JSON for A3. Use `dev` (DeepSeek) for now.
- **Not done yet:** running 3, 4 and 5 in parallel per card with card-only input for A4 and A5. That needs changes to A4/A5's input schemas.
- **Database backups:** in `backups/`, kept locally and not in git. Restore with `pg_restore -c -d postgresql://frontier:frontier@localhost:5433/frontier <file>`.
