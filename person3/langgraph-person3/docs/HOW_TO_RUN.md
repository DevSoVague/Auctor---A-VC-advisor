# How to run · Person 3 analysis agents

The workflow runs four agents (A1 why now, A2 mechanisms, A3 product profile, A6 red team) on COPD. A4 and A5 are hooks owned by person 4. There are two ways to run it, and both use the same step code:

| Way | Use it for |
|---|---|
| **Langflow canvas** | Clicking through the steps, editing prompts on a node, demos |
| **LangGraph CLI** | Backtests, review pauses (approve / edit / rerun), resuming later |

What the workflow does: [FSD.md](FSD.md). How it works inside: [TSD.md](TSD.md).

## 1. One-time setup

```bash
conda activate hack                      # engine + LangGraph (tests run here)
pip install -r ../frontier-engine/requirements-person3.txt
```

Langflow runs in its own env, `langflow` (Langflow 1.8.4).

Settings go in `../frontier-engine/.env`. Copy `.env.example` and fill in only what you need:

| Variable | Needed for |
|---|---|
| `NEBIUS_API_KEY` | `live` runs on Nebius |
| `NEBIUS_BASE_URL` | Another OpenAI-compatible server, such as a local model server. Default: `https://api.tokenfactory.us-central1.nebius.com/v1/` |
| `NEBIUS_MODEL_<AGENT>` | Force a model for one agent, e.g. `NEBIUS_MODEL_A3_PROFILE` |
| `TAVILY_API_KEY` | `--search web` or `both` |
| `DB_BACKEND`, `POSTGRES_URL`, `MILVUS_URI`, `EMBED_MODEL` | `--search database`. `DB_BACKEND=sample` (the default) uses built-in sample rows |
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
   - **As-of date**: `2021-01-01` for the backtest, `today` for a live run
   - **Search source**
2. Click **Playground** and send `run`.
3. As each node finishes it shows its build time. Its status text says, in plain language, what it did. Hover over the node to read it.
4. The Playground shows the report, followed by the full "what happened, node by node" transcript.

> **Gotcha:** any Playground message other than `run` (or `go`, `start`) is added to **every agent's prompt** as `REVIEWER FEEDBACK (address this explicitly)`. This changes the results. Send `run` for a clean run.

Restart Langflow after you change code, `.env`, or the step list. Stop it with Ctrl+C.

## 3. Run from the CLI (LangGraph)

```bash
cd langgraph-person3
python -m p3graph run                                            # backtest 2021-01-01, stub LLM, 0 tokens
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
`{"config": {"as_of": "2021-01-01", "mode": "single_shot"}, "llm": {"mode": "stub"}}`.

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
cd ../frontier-engine
python scripts/run_analysis.py                                   # stub, 2021-01-01, writes runs/<id>/analysis.xlsx
python scripts/run_analysis.py --as-of today --llm live
python scripts/run_analysis.py --retrieval http://localhost:8000 --llm live --profile final
python scripts/model_compare.py --models openai/gpt-oss-120b zai-org/GLM-5.1   # same prompts, several models
```

### 3c. A local model instead of Nebius

Set this in `../frontier-engine/.env` and restart:

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
cd ../frontier-engine && LLM_MODE=stub conda run -n hack python -m pytest -q      # 21 tests + 2 live (skipped)
```

## 6. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `No module named 'lf_graph'` in Langflow | The flow was built from old components. Run `./run_langflow.sh` again (it regenerates them), or reload the flow with `conda run -n langflow python langflow/sync_flow.py langflow/flows/person3_steps.json` |
| Engine import errors in Langflow | A stale `FRONTIER_ENGINE_PATH` in your shell. `run_langflow.sh` now always sets it to `../frontier-engine`; restart through the script |
| `finish_reason = length` on an `llm` step | The model hit `max_tokens` (16,000) while reasoning. The step retries once. Use a faster or less verbose model |
| A stub run on `--search database` stops at A1's citation check | Expected: the stub answers cite the old corpus ids. Use a live model |
| Nothing in the evidence pack | The as-of date hides everything dated on or after it. The team's 2023–2025 papers are invisible in the 2021 backtest |
| Port in use | `PORT=7861 ./run_langflow.sh` |
