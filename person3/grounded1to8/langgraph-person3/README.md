# Person 3 · LangGraph workflow

All six agents as a LangGraph state machine: A1 (why now), A2 (mechanism), A3 (TPP), A4 (inactive assets), A5 (active landscape) and A6 (red team). A4 and A5 are Adam's agents, fitted to our context in `../agents/competitor/`, and run one after the other (V1 is sequential).

The nodes **wrap** the tested agents in `../frontier-engine`, so the grading, citation post-check, prompts and workbook are one shared codebase.

```
setup → A1 → [review] → A2 → [review] → next_card ──► report → END
                                          │  ▲
                 one hypothesis at a time ▼  │
             A3 → [review] → A4 → A5 → A6 → [review]
             ▲                ▲         │
             └────────────────┴─────────┘  revise (once per hypothesis)
```

**Every agent is split into its real steps**, one graph node each, so you can see and debug where a run went wrong:

```
retrieve → prompt → llm → parse → postcheck ⇄ validate → commit          (A1: postcheck first)
                     ▲      │ schema error        │ semantic problems
                     └─ fix ◄┴─────────────────────┘                     (A3, A6: validate first)
```

| Step | What it does | What can go wrong |
|---|---|---|
| `retrieve` | Deterministic evidence retrieval: the only documents the model may cite | Empty or thin pack, leakage guard dropping docs |
| `prompt` | System prompt + payload + evidence pack + reviewer/red-team feedback | Feedback missing, prompt too large |
| `llm` | One model call, raw text back with model, `finish_reason`, tokens, latency | `length` (truncated by `max_tokens`), HTTP errors |
| `parse` | JSON-schema validation of the raw text | Invalid or empty JSON, so `fix` then a second `llm` call (once) |
| `postcheck` | Citation check: strips unknown sources, downgrades claims, caps confidence | Many `stripped_sources` means the model is citing ids it was not given |
| `validate` | The agent's own bars (A1 needs 3+ shifts and a negative one, A3 needs measurable must-haves, A6 needs kill criteria) | Problems go back through `fix` and `llm` once, then the run fails loudly |
| `commit` | Emits claims and events, writes the output into graph state | |

A2 has `grade` (evidence tier, validation level, tractability gate, ranking: all computed in code) in place of `validate`. A6 adds `route` after `commit`: revise once, kill, or survive.

A4 and A5 have their own steps (`p3graph/competitor.py`); everything except `llm` is code:

```
A4: gather → triage → establish → prompt → llm → merge → commit
A5: gather → shortlist → establish → prompt → llm → merge → score → commit
```

Full diagram: [docs/graph.md](docs/graph.md). Regenerate it with `python -m p3graph diagram`.

## Retrieval: corpus (RAG) or web search

`search()` is the open-ended search the agents use to find papers and documents. You choose where it looks:

| Search source | Looks in | Needs |
|---|---|---|
| `corpus` (default) | The retrieval corpus: the stub fixtures, or person 1's API at `--retrieval <url>` (this is where the RAG index plugs in) | nothing |
| `web` | Tavily web search only | `TAVILY_API_KEY` in `frontier-engine/.env` |
| `both` | Corpus results first, then web results | `TAVILY_API_KEY` |

```bash
python -m p3graph run --llm live --search web
python -m p3graph run --llm live --retrieval http://localhost:8000 --search both
```

Only `search()` uses the web. The structured lookups (Open Targets targets, drugs, trials, approvals, retractions) always come from the corpus side, because a web search can't return those tables. Web documents show up in the evidence pack with ids like `web:ab12cd34ef` and in the step trace as `(N from the web)`.

**Backtest safety.** Tavily is asked for results before the as-of date, every result is dated from its published date and dropped if it is on or after the cutoff, and results with no date are dropped in a backtest. In a live run (`--as-of today`) undated pages are kept and tagged `undated`. Tavily's date detection is an estimate, so for a strict backtest prefer `corpus`. Set `TAVILY_DOMAINS=pubmed.ncbi.nlm.nih.gov,clinicaltrials.gov,fda.gov` to restrict the search to trusted sites.

The agents' prompts tell the model to use only the evidence pack. A model can still lean on its training knowledge: claims that cite nothing are downgraded to `reasoning` and capped at confidence 0.5, but nothing blocks that knowledge, and a model whose training data runs past the cutoff (every current model does, for 2025-01-01) may know the future. Web or corpus evidence reduces this, it doesn't remove it.

## Editing the workflow

Change prompts, add loops, a safety gate or a tool call without touching `p3graph/`: see [docs/EDITING.md](docs/EDITING.md).
Prompts go in `prompts/`, node placement in `workflow.yaml`, node code in `custom_nodes.py`.

## Watching a run as a chat

Every node says what it did in plain language: the documents it found, what the model answered, what the citation check changed, the red-team verdict. It prints live in the terminal and is saved to `runs/<run_id>/chat.md`.

```bash
python -m p3graph run --llm live          # chat view while it runs (add --compact for the old one-line view)
python -m p3graph chat -f                 # 2nd terminal: follow the newest run live
python -m p3graph chat <run_id>           # replay a finished run
```

The same messages stream as `{"event": "narration", "who", "text", ...}` in `stream_mode="custom"` (LangGraph Studio, any client). Code: `p3graph/narrate.py`.

## Debugging a run

```bash
python -m p3graph steps <thread>        # every node that ran: card, ms, one-line explanation
```

Live runs print the same line per node as they go. Every run also writes, next to `analysis.xlsx`:

| File | What |
|---|---|
| `steps.md` | The step table for the whole run |
| `steps/NNN_<node>[_hid].json` | One file per step with what that step changed. The `*_llm` file also holds the **full prompt** and the model's **reasoning text** |
| `result.json` | Full state, including `steps` |

Intermediate data (evidence pack, messages, raw model text, parsed output, post-check log, A6 checklist) stays in `state["work"]["a3_profile:h2"]` and so on. LangGraph Studio shows it after each node, and `resume`/`status` read it from the checkpoint.

Quick diagnosis: `finish_reason = length` on an `*_llm` row means the model ran out of tokens (reasoning models such as Nemotron do this). A `schema error` on `*_parse` followed by `*_fix` means invalid JSON. A `problem(s)` on `*_validate` means the output failed the agent's own bars.

**`[review]` checkpoints** pause the graph only in guided mode:
- **guided:** the run stops at each checkpoint with LangGraph's `interrupt()`. The reviewer sees a summary, the automatic citation check and the full output, then chooses:

  | Action | Effect |
  |---|---|
  | `approve` | Accept this output and continue |
  | `approve_all` | Accept this and every later output from this agent |
  | `edit` | Replace the output with an edited version (schema-validated) |
  | `rerun` + comment | Rerun the agent with the comment added to its prompt (at most 2 reruns) |

- **single_shot:** checkpoints pass straight through. This is the mode the backtest uses.

Paused runs are saved in `runs/checkpoints.sqlite`, so you can resume them in another terminal, or a day later.

**Red-team routing.** After A6, a `revise` verdict sends the hypothesis back to the agent A6 names (A3, or A4 ∥ A5), once. If A6 asks for a second revise, it is overridden and logged. `kill` is logged with its kill criterion. Every revise, kill, override and reviewer action becomes a row in the decision log.

## Run it

Use the `hack` conda env, from this folder:

```bash
conda activate hack
cd "/Users/sovague/Desktop/ctrl+alt+cure/upmc/langgraph-person3"

python -m p3graph run                                  # backtest, single shot, stub LLM (fixtures at today, 0 tokens)
python -m p3graph run --mode guided -i                 # review each checkpoint in the terminal
python -m p3graph run --mode guided                    # pause at the first checkpoint and exit…
python -m p3graph resume <thread> --action approve     # …resume later
python -m p3graph resume <thread> --action rerun --comment "restrict to former smokers"
python -m p3graph resume <thread> --action edit --edited my_cards.json
python -m p3graph status <thread>

# live on Nebius (costs tokens)
python -m p3graph run --llm live --model openai/gpt-oss-120b                 # backtest
python -m p3graph run --llm live --model openai/gpt-oss-120b --as-of today   # live demo run
python -m p3graph run --llm live --profile final                             # per-agent model map
```

Events stream to the terminal as each agent works. The workbook `runs/<run_id>/analysis.xlsx` is rewritten after every agent node. The run ends by writing three files next to it:
- `report.md`: Q1/Q2/Q3/Q5, the kills and the decision log
- `result.json`: the full state
- the final `analysis.xlsx`

### LangGraph Studio (optional)

```bash
./run_langgraph.sh                      # installs langgraph-cli once, opens Studio on http://localhost:2024
```

Pick `person3_analysis` and use this input (stub, 0 tokens):
`{"config": {"as_of": "backtest", "mode": "single_shot"}, "llm": {"mode": "stub"}}`.
For a live run use `"llm": {"mode": "live", "model": "openai/gpt-oss-120b"}`; for review pauses set `"mode": "guided"`.

## Hooks

| Hook | How |
|---|---|
| Person 4: A4 | `export P3_A4_HOOK=package.module:function`, an `async (card, profile, ctx) -> InactiveAssets` |
| Person 4: A5 | `export P3_A5_HOOK=package.module:function`, an `async (card, profile, ctx) -> Landscape` |
| Person 1: retrieval | `--retrieval http://localhost:8000` (the person 1/2 FastAPI service) |
| Person 2: schemas, router | Picked up automatically through `../frontier-engine/agents/analysis/schemas.py` |
| Person 5: flow and memo | Call `p3graph.graph.build(checkpointer)` as a subgraph. The final state (`cards`, `profiles`, `verdicts`, `killed`, `decisions`, `usage`) feeds A7 and then A8. |

The graph streams every agent event in `stream_mode="custom"`: `agent_done`, `claim_added`, `kill`, `revise`, `card_rejected` and others. Details: `../frontier-engine/docs/PERSON3_HOOKS.md`.

## Files

| Path | What |
|---|---|
| `p3graph/state.py` | Typed state with reducers, so A4 and A5 can write in parallel and logs append |
| `workflow.yaml`, `custom_nodes.py`, `prompts/` | **Yours to edit**: where custom nodes go, what they do, prompt overrides |
| `p3graph/workflow.py`, `p3graph/tools.py` | Splices custom nodes into the graph; the tool registry |
| `p3graph/steps.py` | The per-step agent nodes (retrieve, prompt, llm, parse, fix, postcheck, validate, commit, A2 grade, A6 route) |
| `p3graph/nodes.py` | Shared helpers, setup, the A4/A5 hooks, review checkpoints (`interrupt`), the report |
| `p3graph/graph.py` | Graph wiring (`build(checkpointer)`), plus the `graph` object used by Studio |
| `p3graph/cli.py` | The `run` / `resume` / `status` / `diagram` commands |
| `p3graph/engine.py` | Link to `../frontier-engine` (override with `FRONTIER_ENGINE_PATH`) |
| `tests/test_graph.py` | 28 tests on stubs at 0 tokens: end-to-end backtest, step order, debug files, retry loops (schema and semantic), leakage, guided rerun/edit/approve-all, rerun cap, A5 hook, prompt overrides, custom nodes (tool, loop, safety gate) |

```bash
pytest -q
```

## Database retrieval (RAG + tables), ready for the Postgres + Milvus setup

`--search database` (or `database+web`) reads from the database instead of the corpus. Until it exists the same
code runs on a small SAMPLE (`DB_BACKEND=sample`, the default), so every hook can be tried today.

| need | how the agents get it | backed by |
|---|---|---|
| unstructured text | `rag_search`: small child chunks are matched, the larger parent text is returned | Milvus + Postgres `parent_chunks` |
| original document | `get_document` | Postgres `documents` (`file_path` points at the original file) |
| stats / tables | `query_table` (read-only, allow-listed tables and columns, bound parameters) | Postgres |

- **Tools** (usable in a `run_tools` node in `workflow.yaml`): `rag_search`, `get_document`, `query_table`. They work in any search mode.
- **Safety:** rows/chunks dated on or after the as-of date are hidden (also inside the Milvus query), retracted documents are dropped, the Postgres session is read-only.
- **When the real schema arrives** edit ONE file: `frontier-engine/agents/analysis/db_mapping.yaml` (table, column and Milvus field names). Sample schema for the database owner: `docs/DATABASE_SCHEMA.md`.
- **Go live:** `pip install pymilvus 'psycopg[binary]'`, then in `frontier-engine/.env` set `DB_BACKEND=live`, `POSTGRES_URL`, `MILVUS_URI`, `EMBED_MODEL` (see `.env.example`).
- **Not verified against real Postgres/Milvus yet:** only the sample backend and the SQL builder are tested (11 tests in `tests/test_db.py`). A stub-LLM run on the sample database stops at the citation check, because the stub answers cite the old corpus ids; use a live model.
