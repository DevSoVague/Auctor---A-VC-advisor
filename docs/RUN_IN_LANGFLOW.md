# Run the whole pipeline in Langflow, with the live monitor

You get two pages in your browser:

| What | Where | Shows |
|---|---|---|
| **Langflow canvas** | http://localhost:7870 | every agent step as a node (agents 1 to 6, the join, the report, and **agent 8, the memo**) that lights up as it runs |
| **Live monitor** | http://127.0.0.1:7872 | every step's inputs, exact model messages, streamed model output, timings, and agent 8 (what it read, what the code picked, the model call, the files it wrote) |

## One-time setup (about 20 minutes, mostly downloads)

```bash
brew install --cask miniforge        # conda, if you do not have it; then open a new terminal
cd Ctrl-Alt-Cure
bash scripts/setup_hack_env.sh       # creates two conda envs, below
```

Two environments, because Person 3's pinned versions for Langflow conflict with the engine's:

| Env | Python | For | Versions from |
|---|---|---|---|
| `hack` | 3.12 | the CLI, the tests, a plain pipeline run | `person3/grounded1to8/requirements.txt` |
| `langflow` | 3.11 | the Langflow canvas | `person3/grounded1to8/requirements-langflow.txt` |

Both get this repo installed (`pip install -e --no-deps .`) so agent 8 (`frontier.memo`) is importable.
`ONLY=hack bash scripts/setup_hack_env.sh` builds just the fast one.

Also needed, which pip does not install:

- a `.env` with your `NEBIUS_API_KEY` (copy `.env.example`); stub runs work without it;
- for a live run on the real evidence store: Docker running, then `make up` (Postgres on port 5433);
- optional: `brew install --cask libreoffice`, so the memo also comes out as a PDF.

## Every time

```bash
bash scripts/run_langflow_monitor.sh
```

It starts the monitor (7872) and Langflow (7870), loads the flow **Person 3 · Analysis agents (step by step)**
and opens both pages. In the canvas:

1. Click **P3 · Setup**. Set *LLM mode* (`stub` is free and fast, `live` spends tokens), *Search source*
   (`evstore+web` = our store plus Tavily; `evstore` = store only), *As of* (`today`).
2. Open **Playground**, type `run`, press send.
3. Watch the nodes light up in the canvas and the steps stream in the monitor.
   The last node, **P3 · A8 Memo**, writes `memo.docx`, `memo.pdf` and `programs.xlsx` into `runs/<run id>/`.

Stop with Ctrl+C. Ports: `PORT=7871` for Langflow, `P3_MONITOR_PORT=7873` for the monitor.

> For a stub run, use *As of* `2021-01-01`: the stub fixtures are dated before 2023.

## No Langflow, just the pipeline

```bash
conda activate hack
cd person3/grounded1to8/langgraph-person3
python -m p3graph run --llm stub --as-of 2021-01-01 --max-cards 2
# live, our store plus web:
python -m p3graph run --llm live --search evstore+web --max-cards 3
```

Open http://127.0.0.1:7872 while it runs. The monitor server starts with the first run.

## Is agent 8 in the monitor?

Yes. The graph now ends `… → report → a8_memo → END` (`p3graph/graph.py`), and the Langflow flow ends
`… → P3 · Report + Workbook → P3 · A8 Memo → Chat Output`. The `a8_memo` node emits the same events as the
other agents, to `runs/<run id>/live.jsonl`, which the monitor reads:

| Monitor card | What it shows |
|---|---|
| Step inputs (1) | what agent 8 read: hypotheses, survivors, killed, claims checked / verified / partly / removed, the evidence window, gaps, the pursuits the **code** picked, the caution flag |
| Request / model output | in a live run, the exact messages sent to the memo model and its streamed answer |
| Step inputs (2) | who wrote the wording (model or template), the fallback reason if the model's text was rejected, the rules the code applied |
| Step inputs (3) | the files it wrote and the pursuits recommended |
| End | `memo written: memo.docx, memo.pdf, programs.xlsx` (or why it was skipped) |

Agent 8 never searches: it reads only the run's own saved outputs. A memo problem never fails the run; it
shows as an error card and the run still finishes.
