# Person 3 · Langflow workflow

This is a Langflow version of person 3's analysis agents: A1 (why now), A2 (mechanism), A3 (TPP) and A6 (red team).

```
P3 Run Setup → A1 Disease → A2 Mechanism → A3 TPP → A6 Red Team → Report + Workbook → Chat Output
```

The nodes **wrap** the tested agents in `../../frontier-engine`; they don't reimplement them. So the evidence grading, citation post-check, revise and kill logic, and Excel workbook are the same code that `pytest` covers there.

## Run it

### Live node monitor

Open **http://127.0.0.1:7862** beside Langflow. The monitor starts automatically with the
first run after restarting Langflow. It shows each step as it starts, with model name,
hypothesis ID, full system/user messages, live response text, and any reasoning text
the model exposes (`reasoning_content` or `reasoning`). Completed JSON answers are
displayed as labeled sections. Deterministic nodes show their results; retries and
parallel cards have separate entries. Provider errors appear on the failed step.

The monitor follows the newest run; uncheck **Follow newest run** to replay another.
Events are saved to `../runs/<run_id>/live.jsonl`, so refresh resumes the transcript.
Runs created before this feature do not have a live transcript.

To start the monitor separately, from `workflow`:

```bash
conda run --no-capture-output -n langflow python -m p3graph.live
```

Set `P3_MONITOR_PORT` to change the port. It listens only on loopback.
Stub runs show fixture output, with no simulated reasoning or token stream.

For an OpenAI-compatible local server, set these in `frontier-engine/.env` and restart:

```dotenv
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
# LOCAL_LLM_API_KEY=...   # optional; defaults to "local"
```

Use **live** mode and enter the server's exact model ID in **Model (all agents)**,
or **Model for this step**. The regenerated step components accept free text.
The server must support chat-completion streaming and a structured response format
(`json_schema`, or the existing `json_object` fallback). Full messages for each
actual request, including fallback schema instructions, are visible in the monitor.
Hidden internal reasoning cannot be retrieved; models that expose no reasoning
stream are labeled accordingly.

```bash
./run_langflow.sh                     # http://localhost:7860 (PORT=7861 ./run_langflow.sh for another port)
```

1. Open Langflow and import `flows/person3_analysis.json`.
2. The six `P3 ·` components also appear in the sidebar under **frontier_person3**.
3. In **P3 · Run Setup**, choose:

| Field | Options |
|---|---|
| As-of date | `today` (default) or an explicit `YYYY-MM-DD` cutoff |
| LLM mode | `stub` uses fixture outputs and spends no tokens; `live` calls Nebius |
| Model (all agents) | `profile default`, gpt-oss-120b, DeepSeek-V4-Flash, Qwen3.5-397B or GLM-5.1 |
| Retrieval | `stub` for the fixture corpus, or person 1's retrieval API URL |

Then run the flow. Chat Output shows the Q1/Q2/Q3/Q5 summary. The full workbook is written to `../../frontier-engine/runs/<run_id>/analysis.xlsx`; each node rewrites it after it runs, so it fills in node by node.

**Reviewer feedback:** every agent node has an advanced **Reviewer feedback** field. Text there is appended to that agent's prompt, so you can rerun one node with a comment.

## Step-by-step flow (every LangGraph step is a node)

`flows/person3_steps.json` has 33 nodes: a Chat Input, then one per step of A1, A2, A3 and A6, one row per agent.

```
Setup → A1: Retrieve · Prompt · LLM · Parse · Postcheck · Validate · Commit
      → A2: Retrieve · Prompt · LLM · Parse · Grade · Postcheck · Commit
      → A3: Retrieve · Prompt · LLM · Parse · Validate · Postcheck · Commit
      → A6: Retrieve · Prompt · LLM · Parse · Validate · Postcheck · Commit · Route → Report → Chat Output
```

Each node runs the **same code as the LangGraph graph** in `..` (`lf_graph.py` calls its step functions), so results are identical. Each node's status line shows what it did, and `runs/<run_id>/steps/` has one debug file per step.

```bash
./run_langflow.sh                                                    # http://localhost:7860
conda run -n langflow python sync_flow.py flows/person3_steps.json   # loads it into the running Langflow, prints the URL
```

**Search source (Setup node):** `corpus` (default: the stub corpus, or person 1's API in **Retrieval**), `web` (Tavily, needs `TAVILY_API_KEY` in `frontier-engine/.env`) or `both`. Details: `../README.md`. Restart Langflow after adding the key.

**Run it:** open the flow, click **Playground** (top right), type a message and press Enter. `run` runs the default analysis; anything else is sent to every agent as an extra instruction (for example `focus on former smokers`). The report appears in the chat, and the Chat Output node shows it on the canvas. Settings (as-of date, stub/live, model) are on the **P3 · Setup** node: set **LLM mode** to `live` to spend tokens.

Restart Langflow once after pulling, so the new nodes appear in the sidebar under **frontier_person3**.

**Edit it on the canvas**

| To | Do |
|---|---|
| Change a prompt | Edit **System prompt** on a `Prompt` node. Left unchanged, it uses the engine prompt (or `prompts/` files in workflow/) |
| Use another model for one step | **Model for this step** (advanced) on `LLM`, `Parse` or `Validate` |
| Add a loop, safety gate or tool call | Drag in **P3 · Custom Node**, wire it after a step, set **Sits after step**, **Function** (`custom_nodes:safety_gate`, `refine_loop`, `run_tools`) and **Params** (JSON). Functions live in `../custom_nodes.py` |
| Skip or reorder steps | Rewire. Steps need what earlier steps wrote (for example `Parse` needs `LLM`) |

**Speed:** A3 and A6 nodes run their cards at the same time (3 at once by default; `P3_MAX_PARALLEL=5 ./run_langflow.sh` for more, `1` for one at a time). For the biggest saving, pick a fast model in **Model for this step** on the LLM nodes (gpt-oss-120b is about 5 times faster than Qwen or GLM). Restart Langflow after changing the setting.

Loops run inside the node that needs them (Langflow has no loops): `Parse` and `Validate` retry once, `Route` reruns A3 + A6 once on `revise`, and a Custom Node that jumps back reruns the steps between and then itself (capped). The pause-for-review checkpoints only exist in the LangGraph CLI.

Regenerate after changing the step list: `conda run -n langflow python gen_components.py && conda run -n langflow python build_steps_flow.py`.
Headless: `conda run -n langflow python build_steps_flow.py --run [--llm live --model openai/gpt-oss-120b]`.

## Headless

```bash
conda run -n langflow python build_flow.py           # re-export flows/person3_analysis.json
conda run -n langflow python build_flow.py --run     # run in-process (stub: 0 tokens)
conda run -n langflow python build_flow.py --run --llm live --model openai/gpt-oss-120b
```

## Files

| Path | What |
|---|---|
| `components/frontier_person3/*.py` | The six custom components. Langflow loads them via `LANGFLOW_COMPONENTS_PATH`. |
| `lf_graph.py`, `gen_components.py`, `build_steps_flow.py`, `flows/person3_steps.json` | The step-by-step flow (see above) |
| `lf_bridge.py` | Holds the state passed between nodes, rebuilds a `RunContext` per node, merges usage and rewrites the workbook |
| `build_flow.py` | Builds the graph in code and exports the importable flow JSON |
| `flows/person3_analysis.json` | The exported flow. It defaults to stub mode, so it is safe to run. |
| `run_langflow.sh` | Starts Langflow with the right env vars |

## Environment

The workflow uses the `langflow` conda env (Langflow 1.8.4, Python 3.11). The Nebius key is read from `../../frontier-engine/.env`.

Env vars:

| Variable | Use |
|---|---|
| `P3_LANGFLOW_HOME` | Override this folder's path |
| `FRONTIER_ENGINE_PATH` | Override the engine's path |
| `MAX_*_PER_RUN` | Optional spend caps |

Setup fix applied on 2026-11-07: `opentelemetry-exporter-otlp` and `opentelemetry-exporter-otlp-proto-grpc` were pinned to 1.37.0 to match the SDK. At 1.40.0, Langflow would not start.

## State passed between nodes

Each node receives one `Data` object holding the run state:

- run settings: `config`, `llm`, `retrieval`, `run_id`
- agent outputs: `why_now`, `cards`, `grades`, `profiles`, `verdicts`, `revised`, `killed`
- bookkeeping: `events`, `signals`, `usage`, `workbook_path`

The `events` list is the same event hook the rest of the team consumes (`agent_done`, `claim_added`, `kill`, `revise`, …). See `../../frontier-engine/docs/PERSON3_HOOKS.md`.
