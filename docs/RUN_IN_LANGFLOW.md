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

## Agent 8 (the memo) is opened up like every other agent

Agent 8 used to be one node. It is now twelve, in the graph (`p3graph/graph.py`: `report -> a8_read -> ... -> a8_commit`)
and in the Langflow canvas (a row of twelve `P3 · A8.n` nodes after the report), each reporting to the monitor.
The monitor shows a progress strip **A8 · Memo writer** (one box per step, with that step's one-line result) and a
full card per step (open it for the exact inputs, messages and answers).

| # | Step | What it does | What the monitor shows |
|---|---|---|---|
| 1 | Read the run | collects the run's saved outputs; never searches | which agents' outputs exist, hypotheses (survived / killed), claims checked / verified / partly / removed, gaps |
| 2 | Score | code rates upside and risk per surviving hypothesis | each hypothesis: evidence tier, crowding, upside, risk, the reasons, already-approved flag |
| 3 | Pick pursuits | code builds the menu of ways in and picks 2 to 3 | the menu, the chosen pursuits, the Proceed with Caution check and reasons, the 12-month catalysts |
| 4 | Prompt | builds what the memo model may use | the exact messages (rules + the brief), the number of citable claims |
| 5 | Model call | the memo model writes the wording (none in a stub run) | request, streamed answer, response |
| 6 | Rule check | applies the rules to the wording | names, numbers, citations, order; the rule breaks, and whether the model gets its one retry |
| 7 | Merge wording | model wording if it passed, otherwise template wording | who wrote it, the fallback reason, the bottom line and each pursuit as written |
| 8 | Charts | draws the charts | which charts |
| 9 | Word memo | writes `memo.docx` | the headings |
| 10 | PDF | writes `memo.pdf` (needs LibreOffice) | the file, or a skip note |
| 11 | Excel table | writes `programs.xlsx` | the file and its sheets |
| 12 | Commit | the summary | the files, the pursuits, the caution flag |

After step 6 the graph can send the run back to step 5 once with the rule breaks listed (in the canvas this loop
runs inside the Rule check node, because a canvas cannot loop). Agent 8 never searches: it reads only the run's own
saved outputs. A problem in any step is shown as an error card; later steps skip and the run still finishes.
