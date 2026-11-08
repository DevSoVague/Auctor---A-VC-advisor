# How to run · Person 3 analysis agents

The workflow runs all six agents on COPD, in order: A1 why now, A2 mechanisms, then per hypothesis A3 product profile, A4 inactive assets, A5 active landscape, A6 red team. A4 and A5 are person 4's (Adam's), fitted to our context in `../agents/competitor/`; they run one after the other, never in parallel. There are two ways to run it, and both use the same step code:

| Way | Use it for |
|---|---|
| **Langflow canvas** | Clicking through the steps, editing prompts on a node, demos |
| **LangGraph CLI** | Backtests, review pauses (approve / edit / rerun), resuming later |

What the workflow does: [FSD.md](FSD.md). How it works inside: [TSD.md](TSD.md). The team evidence store (Postgres + RAG in Docker) is in [§8](#8-evidence-store-postgres--pgvector-rag-in-docker).

## 1. One-time setup

```bash
conda activate hack                      # engine + LangGraph (tests run here)
pip install -r ../requirements-person3.txt
```

Langflow runs in its own env, `langflow` (Langflow 1.8.4).

Settings go in `../.env`. Copy `.env.example` and fill in only what you need:

| Variable | Needed for |
|---|---|
| `NEBIUS_API_KEY` | `live` runs on Nebius |
| `NEBIUS_BASE_URL` | Another OpenAI-compatible server, such as a local model server. Default: `https://api.tokenfactory.us-central1.nebius.com/v1/` |
| `NEBIUS_MODEL_<AGENT>` | Force a model for one agent, e.g. `NEBIUS_MODEL_A3_PROFILE` |
| `TAVILY_API_KEY` | `--search web` or `both` |
| `DB_BACKEND`, `POSTGRES_URL`, `MILVUS_URI`, `EMBED_MODEL` | `--search database`. `DB_BACKEND=sample` (the default) uses built-in sample rows |
| `DATABASE_URL`, `FRONTIER_STORE_DIR` | The evidence store (§8), read from `evidence-store/.env`. The defaults work with `make db-up` |
| `MAX_CALLS_PER_RUN`, `MAX_OUTPUT_TOKENS_PER_RUN`, `MAX_TOTAL_TOKENS_PER_RUN`, `MAX_TOKENS_PER_CALL` | Spend caps (0 = no cap) |

> Never commit a real key, and never put one in `.env.example`.

## 2. Run in Langflow

```bash
cd langgraph-person3
./run_langflow.sh                        # http://localhost:7860 · PORT=7861 ./run_langflow.sh for another port
```

The script regenerates the components, starts Langflow, loads **Person 3 · Analysis agents (step by step)** and opens it.

1. On **P3 · Setup**, choose:
   - **LLM mode**: `stub` (no tokens) or `live`
   - **Model**
   - **As-of date**: `today` (default), or an explicit `YYYY-MM-DD` cutoff for a backtest. The selected date applies in both live and stub mode.
   - **Search source**
2. Click **Playground** and send `run`.
3. As each node finishes it shows its build time. Its status text says, in plain language, what it did. Hover over the node to read it.
4. The Playground shows the report, followed by the full "what happened, node by node" transcript.

> **Gotcha:** any Playground message other than `run` (or `go`, `start`) is added to **every agent's prompt** as `REVIEWER FEEDBACK (address this explicitly)`. This changes the results. Send `run` for a clean run.

Restart Langflow after you change code, `.env`, or the step list. Stop it with Ctrl+C.

## 3. Run from the CLI (LangGraph)

```bash
cd langgraph-person3
python -m p3graph run                                            # backtest, stub LLM, current-date cutoff, 0 tokens
python -m p3graph run --llm live --model openai/gpt-oss-120b      # live backtest
python -m p3graph run --llm live --as-of today                   # live "today" run
python -m p3graph run --llm live --profile final                 # per-agent model map
python -m p3graph run --search web|both|database|database+web    # where search() looks
python -m p3graph run --mode guided -i                           # pause at each review, answer in the terminal
python -m p3graph resume <thread> --action approve|approve_all|rerun|edit [--comment ...] [--edited file.json]
python -m p3graph status <thread>
python -m p3graph steps <thread>                                 # step table
python -m p3graph chat [run_id] [-f]                             # the run as plain-language chat (-f follows live)
python -m p3graph nodes                                          # every node, including inserted ones
python -m p3graph prompt a3_profile                              # the system prompt in use and where it came from
```

LangGraph Studio: `./run_langgraph.sh`, then pick `person3_analysis` with
`{"config": {"as_of": "backtest", "mode": "single_shot"}, "llm": {"mode": "stub"}}`.

### 3a. Faster backtests (`langgraph-person3-fast`)

Same commands, run from `../langgraph-person3-fast`. Cards run in parallel, and A1/A2 are cached between runs.

```bash
cd ../langgraph-person3-fast
python -m p3graph run --llm live                       # fast profile: gpt-oss-120b (A1-A3) + Qwen3.5-397B (A6)
python -m p3graph run --llm live --parallel 5 --top-k 3 --no-revise
python -m p3graph run --models a6=zai-org/GLM-5.1      # override one agent
python -m p3graph run --no-reuse-upstream              # ignore the A1/A2 cache (or delete runs/cache/)
python -m p3graph run --sequential                     # the original one-card-at-a-time loop
```

Guided mode always runs cards one at a time and never uses the cache.

### 3b. Engine only (no LangGraph)

```bash
cd ..
python scripts/run_analysis.py                                   # stub, current-date cutoff, writes runs/<id>/analysis.xlsx
python scripts/run_analysis.py --as-of today --llm live
python scripts/run_analysis.py --retrieval http://localhost:8000 --llm live --profile final
python scripts/model_compare.py --models openai/gpt-oss-120b zai-org/GLM-5.1   # same prompts, several models
```

### 3c. A local model instead of Nebius

Set this in `../.env` and restart:

```dotenv
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
# LOCAL_LLM_API_KEY=...        # optional, defaults to "local"
```

Langflow runs also stream to a live node monitor at http://127.0.0.1:7862. It starts with the first run; set `P3_MONITOR_PORT` to change the port.

## 4. What a run leaves behind

Everything goes in `runs/<run_id>/`:

| File | What |
|---|---|
| `report.md` | Q1 why now, Q2/Q5 mechanisms and verdicts, Q3 must-haves, kills, decision log |
| `analysis.xlsx` | Live workbook, rewritten after every commit |
| `chat.md` | One plain-language message per node |
| `log.md` | Everything, untruncated: system and user prompts, raw answers, model reasoning, what each step changed, the evidence pack |
| `log.jsonl` | The same as `log.md`, one JSON record per node |
| `steps.md`, `steps/NNN_<node>.json` | Step table and per-step debug files |
| `result.json` | Final state |

Paused CLI runs are kept in `runs/checkpoints.sqlite`. Deleting it means they can no longer be resumed.

## 5. Tests

```bash
conda run -n hack python -m pytest -q    # in langgraph-person3: stubbed, 0 tokens (44 tests)
cd ../langgraph-person3-fast && conda run -n hack python -m pytest -q             # 38 tests
cd .. && LLM_MODE=stub conda run -n hack python -m pytest -q      # 21 tests + 2 live (skipped)
```

## 6. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `No module named 'lf_graph'` in Langflow | The flow was built from old components. Run `./run_langflow.sh` again (it regenerates them), or reload the flow with `conda run -n langflow python langflow/sync_flow.py langflow/flows/person3_steps.json` |
| Engine import errors in Langflow | A stale `FRONTIER_ENGINE_PATH` in your shell. `run_langflow.sh` now always sets it to `..`; restart through the script |
| `finish_reason = length` on an `llm` step | The model hit `max_tokens` (16,000) while reasoning. The step retries once. Use a faster or less verbose model |
| A stub run on `--search database` stops at A1's citation check | Expected: the stub answers cite the old corpus ids. Use a live model |
| Nothing in the evidence pack | The as-of date hides everything dated on or after it. The backtest cutoff is 2025-01-01, so only 2023–2024 evidence is visible. An as-of date before 2023 sees nothing in the team store |
| Stub run fails a citation/quality check at the chosen cutoff | Fixture answers are fixed and may not match the evidence selected at an arbitrary cutoff. Use a live model with the intended evidence source. |
| Port in use | `PORT=7861 ./run_langflow.sh` |

## 7. Live workflow monitor

The monitor runs separately from the agents and shows their saved/live events at **http://127.0.0.1:7862/**. It does not execute a workflow or make model calls. Its behavior and event flow are specified in [FSD.md §9](FSD.md#9-live-workflow-monitor).

### Start the monitor on its own

From the repository root:

```bash
cd langgraph-person3
conda run --no-capture-output -n langflow python -m p3graph.live
```

Leave that terminal running and open **http://127.0.0.1:7862/**. This works even with Langflow stopped: existing runs with `runs/<run_id>/live.jsonl` can still be viewed. If the monitor is already listening on 7862, use the existing page instead of starting a second copy on that port.

### Run a workflow alongside it

In a second terminal, from the repository root:

```bash
cd langgraph-person3
PORT=7861 ./run_langflow.sh
```

The launcher prints and opens the Langflow flow URL. On **P3 · Setup**, choose the disease/date, model and retrieval settings, then open **Playground** and send `run`.

- **stub:** fixture results, no model calls or simulated reasoning stream.
- **live:** real streamed responses from the configured provider. For a local server, configure `LOCAL_LLM_BASE_URL` as described in §3c and enter its exact model ID in Setup.

Alternatively, run the instrumented reference CLI from `langgraph-person3`:

```bash
conda run --no-capture-output -n langflow python -m p3graph run
# Real model run (uses the configured provider):
conda run --no-capture-output -n langflow python -m p3graph run --llm live --model YOUR_MODEL_ID
```

The standalone monitor reads this checkout's `runs/` directory (`1to6merged/langgraph-person3/runs/`); it does not automatically watch the separate `langgraph-person3/` or `langgraph-person3-fast/` checkouts.

### Use the page

1. Keep **Follow newest run** checked for the current run, or uncheck it and select an older run.
2. Expand **Shared preparation** to inspect Setup, A1 and A2.
3. After A2 grades the ideas, each card has its own workflow panel: up to **3 columns**, with further cards below. Queued panels become active when their steps actually start.
4. Click a card header for a focused view; click a step for full prompts, model name, output, exposed reasoning and results. Close focus with **Close focus** or **Escape**.
5. Leave **Auto-scroll** checked to follow updates within each card's panel, or disable it to inspect earlier content. The final report appears in the shared follow-up section.

The layout displays the runner's actual execution. It does not turn sequential execution into parallel execution. Reasoning is visible only if the model supplies it.

### Startup options and shutdown

Without a standalone monitor, the first workflow Setup attempts to start one inside its own process. The page may be unavailable until that first run starts, and the embedded monitor disappears when its host exits. Use the standalone command above to keep recorded runs accessible after stopping Langflow or the CLI.

For a custom monitor port:

```bash
# Monitor terminal, from langgraph-person3:
conda run --no-capture-output -n langflow python -m p3graph.live --port 7863

# Workflow terminal, also from langgraph-person3:
P3_MONITOR_PORT=7863 PORT=7861 ./run_langflow.sh
```

Open **http://127.0.0.1:7863/**. `PORT` controls Langflow; `P3_MONITOR_PORT` controls the monitor's embedded startup and launcher link. The standalone server accepts either `--port` or `P3_MONITOR_PORT` (default 7862).

Press **Ctrl+C in the monitor terminal** to stop the standalone monitor, and **Ctrl+C in the workflow terminal** to stop Langflow or the CLI. Stopping either process leaves recorded events on disk. A separately launched monitor remains running when Langflow stops.

| Symptom | What to check |
|---|---|
| Monitor page cannot connect | Start the standalone command, or trigger the first workflow run for embedded startup. Check the monitor port. |
| `Address already in use` | A server already owns the port. Use its page if it is the monitor, or choose a different monitor port. |
| Run is missing | Check for `langgraph-person3/runs/<run_id>/live.jsonl`. Older runs and runs written by other checkouts are not automatically imported. |
| No new events after stopping Langflow | Expected: the standalone monitor can replay files, but agents must be running to produce new events. |
| No live tokens or reasoning | Stub mode uses fixtures. In live mode the local/provider endpoint must support streaming; separate reasoning appears only when the model emits it. |

## 8. Evidence store (Postgres + pgvector RAG, in Docker)

The evidence store is the team's COPD database, holding data dated **2023–2025**. The years 2020–2022 are left out on purpose because COVID confounds them. It lives in `upmc/evidence-store/`, next to `1to6merged/`, and it has two ways to read it:

| Way | Use it for | Network |
|---|---|---|
| **Lookups** (tables) | Fast tabular answers: drugs, shelved programs, market stats, trials, per-paper findings, label side effects | None |
| **RAG** (`rag`, `search`) | Passages from papers, trial registrations and FDA labels | One Nebius call per query, to embed it |

Every read is bound to an **as-of date**, and nothing dated on or after it is returned. The full table list and design are in [FSD §10](FSD.md#10-evidence-store) and [TSD §12](TSD.md#12-evidence-store-internals). The store's own `README.md` has the column-level detail.

> **It is not wired into the agents yet.** `--search database` still uses the older `retrieval_db.py` backend (Postgres + Milvus, with sample rows by default). Use the store from code or the CLI below until it is plugged in.

### 8.1 Start the database (Docker)

You need Docker Desktop running. From the `upmc/` folder:

```bash
cd evidence-store
make db-up            # pgvector/pgvector:pg16, container evidence-store-db, host port 5433
```

| Setting | Value |
|---|---|
| Container | `evidence-store-db` (compose project `evidence-store`; it restarts on its own unless you stopped it) |
| URL | `postgresql://frontier:frontier@localhost:5433/frontier`, the same as the team repo's `DATABASE_URL` |
| Data | Docker volume `evidence-store_pgdata`. It survives `make db-down` and Docker restarts |

`make db-down` stops the container and `make db-logs` follows its log. Without Docker, `make db-up-local` runs the same database from Postgres.app binaries.

### 8.2 Load it

```bash
conda activate hack                                   # needs psycopg[binary], pgvector, numpy, openai
mkdir -p data && unzip ../frontier-store.zip -d data  # person 1's export -> data/frontier-store/
make cache-import     # reuse the paid embeddings + extractions in data/cache/*.tsv (if you have them)
make all              # schema + load + chunks + tables + embed + extract + status
make test             # 10 integration tests; EVSTORE_LIVE=1 adds a Nebius RAG test
```

| Step | What it does | Cost |
|---|---|---|
| `make build` | Applies the schema (team schema, then `001_rag_and_lookups.sql`), loads the export, cuts 1,644 passages, fills the deterministic tables | Free |
| `make embed` | Embeds passages that have no vector yet, with `Qwen/Qwen3-Embedding-8B` on Nebius | About 530k tokens the first time; 0 after `cache-import` |
| `make extract` | LLM tables `paper_findings` and `label_events`, with `openai/gpt-oss-120b` on Nebius | About 400k tokens the first time; 0 after `cache-import` |

The Nebius key is read from `evidence-store/.env` or `frontier-engine/.env` (`NEBIUS_API_KEY`).

### 8.3 Use it

From the shell:

```bash
make status AS_OF=2025-01-01                          # what that cutoff can see
make ask Q="anti-IL-33 antibody results in COPD" AS_OF=2025-01-01
```

From Python (run from `evidence-store/`, or put it on `PYTHONPATH`):

```python
from datetime import date
from evstore import StoreClient

s = StoreClient(date(2025, 1, 1))
await s.shelved()                                   # failed programs, with reasons
await s.findings(drug="dupilumab")                  # n, endpoint, result, met_primary per paper
await s.label_events(kind="adverse_reaction")       # side effects, with drug % and placebo %
await s.rag("does blocking IL-33 reduce exacerbations", k=8)   # passages; cite hit.doc_id
```

`StoreClient` also has the team retrieval methods (`search`, `doc`, `trials`, `drugs`, `approvals`, …), so it can stand in for `ctx.retrieval`.

### 8.4 Share the paid work

`make cache-export` writes `data/cache/embedding_cache.tsv` (about 83 MB) and `extractions.tsv`. A teammate copies them into their own `evidence-store/data/cache/` and runs `make cache-import && make all`, and gets the identical store with no Nebius calls. The files are git-ignored, so share them through a drive, not the repo.

### 8.5 Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make db-up`: `Cannot connect to the Docker daemon` | Start Docker Desktop and wait for "Engine running" |
| `port is already allocated` on 5433 | Another database holds the port: the team repo's `db` container, or the local cluster (`make db-down-local`). Stop it, or set a different port in `docker-compose.yml` and `DATABASE_URL` |
| `records.json not found` | The export isn't unzipped to `evidence-store/data/frontier-store/`. Unzip it there, or set `FRONTIER_STORE_DIR` |
| `make embed` or `make extract` says `NEBIUS_API_KEY is not set` | Put the key in `evidence-store/.env` or `frontier-engine/.env` |
| A 2025-01-01 lookup returns no on-market drugs, labels or market rows | Expected. Those rows are dated by the 2025 FDA label versions. Use a later cutoff, or have person 1 add older label versions |
| `rag()` raises a connection error to Nebius | The query embedding needs the network. `StoreClient(as_of, use_vectors=False)` falls back to full-text search only |

## Update 2026-11-08: evidence-store tools and the A6b judge

See [HANDOFF.md](../../HANDOFF.md) for the full detail.

- **Ports:** monitor 7872, Studio 2034, Langflow 7870. These replace 7862, 2024 and 7860.
- **New flags:** `--search evstore`, `--tools`, `--no-judge`, `--max-cards N`.
- **Graph:** each chain is now retrieve → prompt → **toolloop** → llm → parse → … → commit. A6's commit is followed by **a6b_judge**, then a6_route; with the judge on, A6b's decision is what gets routed.
- **Tools:** DB read for A1–A6, DB write for A1 only, web search for A1/A4/A5, and nothing for A6b. Every result is dated, within `2023-01-01 <= date < as_of`.
- **New state keys:** `tools`, `judge`, `max_cards`, `judgments`.
- **New workbook sheets:** ToolCalls, NewEvidence, Judge.


### Agent dropdown in Node Monitor

Use the **Agent** dropdown in the header. It defaults to **A3 · Profile**, showing every idea card for A3 side by side. Select **A4 · Inactive assets**, **A5 · Active landscape**, or **A6 · Red team** to switch the entire board to that agent: its own progress, reasoning, output, timers, and step history. Streams continue collecting while a different agent is selected. The focus-mode agent selector stays synchronized with the board dropdown. Full captured streams remain in individual step details; previews show the latest model call. Reasoning appears only when exposed by the model.

These controls filter monitor events; they do not change scheduling, graph dependencies, API credentials, or concurrency. Producers should use stable per-attempt event IDs, the correct `hid`, and step names such as `a4_llm` / `a5_llm`, with `delta` events labeled `reasoning` or `output`.
