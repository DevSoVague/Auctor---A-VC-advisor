# Functional Specification (FSD) · Person 3 analysis agents

**Project:** Frontier Hypothesis Engine, UPMC Enterprises MedTech PS #1 (team ctrl+alt+cure)
**Scope of this document:** person 3's part: agents A1, A2, A3 and A6, plus the workflow that runs them
**Disease:** COPD (`EFO_0000341`) · **Backtest cutoff:** 2025-01-01 (data window 2023–2025; 2020–2022 left out because COVID confounds it)
**Evidence store data window:** 2023–2025 (see §10)
**Related:** [HOW_TO_RUN.md](HOW_TO_RUN.md) (how to run it) · [TSD.md](TSD.md) (how it works inside) · [../../frontier-engine/docs/PERSON3_HOOKS.md](../../frontier-engine/docs/PERSON3_HOOKS.md) (integration contract)

---

## 1. Purpose

A biopharma VC wants therapeutic hypotheses it can act on in a disease area. For each hypothesis the full team system answers five questions from the problem statement:

| # | Question | Answered by | Owner |
|---|---|---|---|
| Q1 | Why is now the time to work on this disease? | A1 Disease Analysis | **Person 3** |
| Q2 | Which mechanism(s) should be targeted? | A2 Mechanistic Analysis | **Person 3** |
| Q3 | What would a winning product look like? | A3 Optimal Solution (TPP) | **Person 3** |
| Q4 | Which existing assets or companies could get there? | A4 Inactive / A5 Active asset finders | Person 4 (hooks here) |
| Q5 | What could kill the hypothesis? | A6 Red Teamer | **Person 3** |

Person 3's part turns a disease and a date into a ranked set of hypothesis cards, each with a product profile and a red-team verdict. Every claim carries a citation, and every confidence value is calibrated. The citation checker (A7) and the memo (A8) consume this output downstream.

## 2. Users

| User | What they do with it |
|---|---|
| **VC analyst / reviewer** | Runs the workflow and reviews each agent's output at checkpoints (approve, edit, rerun). Reads the report and workbook. |
| **Demo audience / judges** | Watch a run live (Langflow canvas, chat view, live monitor) and read the final report. |
| **Teammates (persons 1, 2, 4, 5)** | Call the agents through documented hooks and consume their events, signals and outputs. |
| **Developer (person 3)** | Edits prompts, inserts nodes, compares models and debugs runs step by step. |

## 3. Scope

**In scope**
- A1, A2, A3 and A6: retrieval, prompting, model call, schema validation, citation post-check, agent-specific quality bars, events.
- Workflow orchestration: one hypothesis at a time, A4 ∥ A5 hooks, one red-team revise round, review checkpoints.
- Two run modes: `single_shot` (backtest) and `guided` (human review).
- Two time modes: current-date analysis (`as_of = today`, the default) and an explicit `YYYY-MM-DD` cutoff for a backtest.
- Outputs: report, live Excel workbook, chat transcript, full logs, step traces.
- Three front ends over the same code: CLI (LangGraph), LangGraph Studio, Langflow canvas.

**Out of scope (owned by teammates)**
- A4 / A5 asset finders (person 4). Here they are hooks that return nothing until person 4 plugs them in.
- A7 Citation Checker and A8 Orchestrator/memo (person 5).
- The retrieval corpus, RAG index and ingestion (person 1). Here: stub fixtures, an HTTP client, and a database adapter.
- Canonical schemas, run context and LLM router (person 2). Here: a PRD-verbatim draft, used until person 2's version exists.

## 4. Functional requirements

### 4.1 Run setup

| ID | Requirement |
|---|---|
| FR-RUN-1 | A run takes: disease, disease id, `as_of` date (`backtest`, `YYYY-MM-DD` or `today`), mode (`single_shot` / `guided`), LLM mode (`stub` / `live`), model or model profile, retrieval source, search source and audience. Defaults: COPD, `EFO_0000341`, `today`, `single_shot`, `stub`, profile `dev`, retrieval `stub`, search `corpus`, audience `generalist`. |
| FR-RUN-2 | `stub` LLM mode costs 0 tokens and gives deterministic fixture answers. `live` calls an OpenAI-compatible endpoint (Nebius Token Factory by default, or a local server). |
| FR-RUN-3 | Each run gets a `run_id` and writes all its artefacts to `runs/<run_id>/`. |
| FR-RUN-4 | Optional spend caps (calls, output tokens, total tokens, tokens per call) stop a live run when they are reached. Every cap defaults to 0, which means no cap. |

### 4.2 A1 · Disease Analysis (Q1 "why now")

| ID | Requirement |
|---|---|
| FR-A1-1 | Look back 5 years before `as_of` and explain what changed to make the disease workable now. |
| FR-A1-2 | Output **at least 3 shifts**. Each shift has a type (`endpoint`, `segment`, `trial_tool`, `negative`), a date, `effect_on` (one or more of time, cost, selection) and claims. |
| FR-A1-3 | At least one shift must be **`negative`**, meaning something that raises the bar. |
| FR-A1-4 | Every shift needs at least one claim citing the evidence pack. |
| FR-A1-5 | Define at least one patient segment. Each segment needs **at least 2 concrete inclusion criteria**; a label alone does not count. |
| FR-A1-6 | Drop shifts dated on or after `as_of`. |

### 4.3 A2 · Mechanistic Analysis (Q2 "which mechanism")

| ID | Requirement |
|---|---|
| FR-A2-1 | Produce **3 to 5 hypothesis cards**, ranked best first, each built on existing modalities. |
| FR-A2-2 | Cards may only use target ids from the Open Targets association table. |
| FR-A2-3 | **Code, not the model, sets the evidence tier (1–4) and validation level (gold / silver / bronze)**, from genetics, in-disease clinical phase and human literature. |
| FR-A2-4 | A failed precedent in the disease never earns tier 1. |
| FR-A2-5 | Reject a card when its targets are unknown, it has no tractable modality, or it has weak genetics with fewer than 2 converging human evidence lines. If fewer than 3 cards survive, pad the set with rejected cards, marked bronze with the gate they failed. |
| FR-A2-6 | Rank by tier, then by number of converging human lines. |
| FR-A2-7 | Publish publication counts per target (`pub_counts`) for person 4's crowding score and the "most-published" baseline. |

### 4.4 A3 · Optimal Solution / Target Product Profile (Q3)

| ID | Requirement |
|---|---|
| FR-A3-1 | For each card, write the standard-of-care gap **expected at launch**, minimum specs, holy-grail specs, 1–2 must-haves and the expansion opportunity. |
| FR-A3-2 | Every must-have must be **measurable**: a number with a unit, a threshold (≥, ≤), a dosing frequency or a non-inferiority margin. |
| FR-A3-3 | Minimum and holy-grail specs and the SoC gap must be non-empty. |

### 4.5 A6 · Red Teamer (Q5)

| ID | Requirement |
|---|---|
| FR-A6-1 | Build a deterministic risk checklist before the model runs: retractions, prior failures (including terminated, withdrawn or negative trials with `why_stopped`), single-lab or unreplicated findings, safety signals, and contradictions. |
| FR-A6-2 | The model must address every non-empty checklist item, then give a verdict: `survives`, `revise` or `kill`. |
| FR-A6-3 | Each risk (2–5, most severe first) needs a kill criterion, a cheapest test and a claim. |
| FR-A6-4 | Include a premortem. |
| FR-A6-5 | A `revise` verdict names exactly one upstream agent to redo the work. |

### 4.6 Workflow behaviour

| ID | Requirement |
|---|---|
| FR-WF-1 | Order: A1 → A2 → for each card in rank order: A3 → (A4 ∥ A5) → A6 → next card → report. |
| FR-WF-2 | **One revise round per hypothesis.** A revise sends the card back once, to A3 or to A4 ∥ A5, with the red team's reason as feedback. A second revise request is overridden to `survives`, flagged and logged. |
| FR-WF-3 | `kill` removes the card from the survivors and logs its kill criterion. |
| FR-WF-4 | **Guided mode:** pause after A1, after A2, after A3 (each card) and after A6 (each card). The reviewer chooses one of: |

| Reviewer action | Effect |
|---|---|
| `approve` | Accept the output and continue |
| `approve_all` | Accept this output and every later output from the same agent |
| `edit` | Replace the output with a schema-validated edited version |
| `rerun` + comment | Rerun the agent with the comment added to its prompt, at most 2 times per agent per card |

| ID | Requirement |
|---|---|
| FR-WF-5 | **Single-shot mode:** checkpoints pass straight through. This is the backtest mode. |
| FR-WF-6 | Paused runs survive a process restart and can be resumed later by thread id. |
| FR-WF-7 | Every revise, kill, override and reviewer action becomes a row in the decision log. |
| FR-WF-8 | Agents that return invalid JSON get one schema retry. Agents whose output fails their quality bar get one semantic retry. After that the run fails loudly; it never continues silently. |

### 4.6a Per-hypothesis processing and isolation

After A2, the workflow runs for each idea card (`h1`–`h5`) separately. A label such as `a3_profile / h3` means agent A3 working on card h3. A3 and A6 run once per card: one product profile and one verdict per mechanism. They are not alternative plans for the same idea.

| ID | Requirement |
|---|---|
| FR-HYP-1 | Each card passes through its own A3 → (A4 ∥ A5) → A6 → route sequence. |
| FR-HYP-2 | **Spreading out (fast copy, single-shot mode):** after A2, a `fan_out` step starts one branch per card, with up to `--parallel N` branches at once (default 3). Each branch works on its own copy of the state. A `cards_join` step merges the branches in rank order, so the output is deterministic. |
| FR-HYP-3 | **One card at a time** in the reference workflow (`langgraph-person3`), and in the fast copy in `guided` mode or with `--sequential`. A `next_card` step takes the next card from a queue. Guided mode stays sequential so that each card gets its own review pause. |
| FR-HYP-4 | **No judgement spills between cards.** These are kept per card: |

| Kept per card | How |
|---|---|
| Model input | Each model call is stateless. A3 and A6 for card h3 see only h3's card, h3's profile, and an evidence pack built fresh for h3. They never see another card's profile or verdict. |
| Feedback | Stored under `agent:hid` (e.g. `a3_profile:h3`) and cleared once used. A revise on h3 changes only h3's prompt. |
| Limits | The single revise round is counted per card. The 2-rerun cap is counted per agent per card. |
| Working data | The evidence pack, messages, raw answer and problems are stored under `work["agent:hid"]`. |

| ID | Requirement |
|---|---|
| FR-HYP-5 | **Shared on purpose:** A1's why-now (shifts and segments) is the common background for every card. A2's grading and ranking compare the cards with each other, which sets their order and decides which pass the gates. The audit logs (decisions, kills, events) are one list, and every row is tagged with its card id. |
| FR-HYP-6 | **Known exceptions that do apply across cards:** (a) the guided `approve_all` action applies to an agent on **all** remaining cards; (b) a Langflow Playground message other than `run` is added to every agent's prompt on every card; (c) a red-team revise aimed at A1 or A2 cannot be applied to one card alone, because those agents run once for all cards, so it is overridden to `survives` and logged. |

**Verified (stub run of the fast copy, 2026-11-08, `runs/152684f8…`):**
- `fan_out` started 5 card branches (cap 3), and `cards_join` merged them in the order `h1`–`h5`.
- All five cards reached `a3_retrieve` at step 016 and `a6_route` at step 030.
- Only h3 was revised: it went back to A3 once, and its second revise was overridden to `survives`.
- h5 was killed.
- h1, h2 and h4 survived and were not affected by either.

### 4.7 Evidence and retrieval

| ID | Requirement |
|---|---|
| FR-EV-1 | Agents may cite only documents in their evidence pack, and the pack contains only documents dated **before** `as_of`. |
| FR-EV-2 | Search sources, selectable per run: |

| Source | Searches | Needs |
|---|---|---|
| `corpus` | Stub fixtures, or person 1's API | Nothing |
| `web` | Tavily web search | `TAVILY_API_KEY` |
| `both` | Corpus, then web | `TAVILY_API_KEY` |
| `database` | Postgres + Milvus RAG | Database settings (sample rows by default) |
| `database+web` | Database, then web | Database settings and `TAVILY_API_KEY` |

| ID | Requirement |
|---|---|
| FR-EV-3 | Structured lookups (targets, drugs, trials, approvals, retractions) always come from the corpus or database side, never from the web. |
| FR-EV-4 | In a backtest, web results without a date are dropped. In a live run they are kept and tagged `undated`. |
| FR-EV-5 | The database backend hides rows dated on or after `as_of`, drops retracted documents and uses a read-only session. |

The team's pre-loaded evidence store (Postgres + pgvector RAG, data 2023–2025) is specified in [§10](#10-evidence-store). It is not yet behind `--search database`.

### 4.8 Outputs

| ID | Output | Contents |
|---|---|---|
| FR-OUT-1 | `report.md` | Q1 why now, Q2/Q5 mechanisms with verdicts, Q3 must-haves, kills, decision log |
| FR-OUT-2 | `analysis.xlsx` | Sheets: WhyNow, Segments, Hypotheses, TPP, RedTeam, Claims, Events, Meta (config, tokens, USD per agent). It is rewritten during the run, so it fills in live. |
| FR-OUT-3 | `chat.md` | One plain-language message per node |
| FR-OUT-4 | `log.md` / `log.jsonl` | Full prompts, raw answers, model reasoning and the evidence pack, untruncated |
| FR-OUT-5 | `steps.md`, `steps/NNN_<node>.json` | Step table and per-step diffs |
| FR-OUT-6 | `result.json` | Final state: `cards`, `profiles`, `verdicts`, `killed`, `decisions`, `usage`. A7 and A8 read this. |
| FR-OUT-7 | Event stream | `agent_started`, `agent_done`, `claim_added`, `card_rejected`, `revise`, `kill`, `signal`, `warning`, `narration` |

## 5. Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-1 | **No look-ahead leakage** | The as-of filter is applied at retrieval, in the evidence pack and in the post-check. Citations to documents not in the pack, or dated on or after `as_of`, are stripped. |
| NFR-2 | **Calibration** | Evidence-claim confidence is capped by the card's tier: 1 ≤ 0.9, 2 ≤ 0.75, 3 ≤ 0.6, 4 ≤ 0.4. Claims that cite nothing are downgraded to `reasoning` and capped at 0.5. |
| NFR-3 | **Traceability** | Every claim carries the agent that made it, a stable id and its source ids. Every row in the workbook carries `hypothesis_id` and `source_ids`. |
| NFR-4 | **Determinism** | Retrieval, grading, ranking, gates and the checklist are computed in code. Stub runs are fully reproducible. |
| NFR-5 | **Cost** | Stub mode spends 0 tokens. Live mode reports tokens and USD per agent and enforces the optional caps. |
| NFR-6 | **Integration** | Hooks only: teammates plug in without editing person 3's code (env-var hooks, duck-typed `ctx`, automatic schema pick-up). |
| NFR-7 | **Debuggability** | Every agent is split into visible steps. Every step logs what it changed. |
| NFR-8 | **Security** | Keys live only in `.env`, never in `.env.example` or git. The database user is read-only. The live monitor listens only on loopback. |

## 6. Variants

| Folder | Use for |
|---|---|
| `langgraph-person3/` | Reference workflow: CLI, Studio, Langflow, guided review, database retrieval |
| `langgraph-person3-fast/` | Faster backtests. Cards run in parallel (default cap 3), with a per-agent model map, an A1/A2 cache and the options `--top-k` and `--no-revise`. With the stub LLM it produces the same output as the reference. Guided mode falls back to one card at a time. |
| `frontier-engine/` | The agents themselves, plus `scripts/run_analysis.py` (plain async chain) and `scripts/model_compare.py` |

## 7. Acceptance criteria

| # | Criterion | How it is verified | Status (2026-11-08) |
|---|---|---|---|
| AC-1 | A stub backtest runs end to end at 0 tokens and writes every output | `pytest` in `langgraph-person3` | ✅ 44 passed |
| AC-2 | The agents meet their bars, the citation post-check and calibration caps work, and the leakage guard holds | `pytest` in `frontier-engine` | ✅ 21 passed, 2 live tests skipped |
| AC-3 | The fast copy produces the same output as the reference | `pytest` in `langgraph-person3-fast` | ✅ 38 passed |
| AC-4 | Guided approve, edit, rerun and approve-all work, and the rerun cap holds | `tests/test_graph.py` | ✅ |
| AC-5 | Live models return schema-valid output for all 4 agents | `scripts/model_compare.py` (2026-11-07) | ✅ gpt-oss-120b, Qwen3.5-397B and DeepSeek-V4-Flash 4/4 · GLM-5.1 3/4 |
| AC-6 | The 2025-01-01 backtest ranks later-successful mechanisms high and flags the 2025 failures (e.g. astegolimab, itepekimab) | Live backtest on the real corpus | ⏳ Not run yet |
| AC-7 | Database retrieval works against real Postgres + Milvus | Live database | ⏳ Only the sample backend is tested |
| AC-8 | The evidence store holds the 2023–2025 data with lookups and RAG, in Docker | `evidence-store` tests ([§10.4](#104-acceptance)) | ✅ 10 passed. Not yet wired to the agents |

## 8. Open items and dependencies

- Person 1: the real retrieval API and corpus. Until then the stub fixtures, whose ids start with `fx:`, are fake.
- Person 2: merge `A2Output` and `RedTeamInput` into `agents/schemas.py`. The model IDs in the PRD don't exist on Nebius, so person 2 needs the real ones.
- Person 4: A4 and A5 implementations, through `P3_A4_HOOK` and `P3_A5_HOOK`.
- Person 5: use the graph as a subgraph, and feed A7 and A8 from the final state.
- The final model map (`--profile final`) is awaiting sign-off.
- The final live run and the backtest run are not done yet.
- Known limit: a model whose training data runs past the cutoff (every current model does, for 2025-01-01) may still "know the future". Prompts forbid it and uncited claims are capped, but nothing can block it outright.

## 9. Live workflow monitor

The monitor at **http://127.0.0.1:7862/** is a local view of workflow activity. It reads recorded events; it does not start agents, call models, or change the number of cards running concurrently. Langflow or the LangGraph runner executes the workflow separately.

### 9.1 What the user sees

| ID | Requirement |
|---|---|
| FR-MON-1 | Show shared preparation (Setup, A1 and A2) separately from the per-card workflows and the final report. |
| FR-MON-2 | Give every idea card its own panel, labeled with its hypothesis ID and mechanism. Show at most **3 columns**, with additional cards wrapping into more rows. Smaller screens use 2 or 1 column. |
| FR-MON-3 | Clicking a card header opens a focused view of that card. Clicking a step expands its details. Each card has its own scrolling step history. |
| FR-MON-4 | Show the step name, agent, card ID, model name for model calls, full system/user messages, streamed output and any reasoning text exposed by the model. Completed JSON answers appear as labeled sections. |
| FR-MON-5 | Keep parallel cards and retry attempts separate. Display queued, running, waiting, complete and failed states based on recorded events. A panel does not imply its card is currently executing. |
| FR-MON-6 | Follow the newest run by default. Allow selecting an older recorded run and disabling auto-scroll. Refresh replays the selected run's events. |
| FR-MON-7 | Stub runs show fixture output without fabricated token streaming or reasoning. A model that provides no separate reasoning stream is labeled accordingly; hidden internal reasoning is unavailable. |

### 9.2 How it runs

1. Instrumented steps write start, request, delta, response, completion and error events to `langgraph-person3/runs/<run_id>/live.jsonl`. A unique step-attempt ID and hypothesis ID associate events with the correct panel.
2. For observed live calls, the model client consumes OpenAI-compatible streaming responses and records output and exposed reasoning chunks as they arrive. Actual request messages include schema-fallback instructions when used.
3. `p3graph/live.py` serves the monitor's HTML and read-only run/event endpoints using Python's standard-library HTTP server, bound to `127.0.0.1`.
4. The browser polls for new events every **250 ms**, using a byte offset to fetch only unread records, up to 2,000 events per response. `p3graph/live.html` groups them by card and updates the appropriate step.

The server reads this checkout's `runs/` directory. Runs without `live.jsonl`, including runs from uninstrumented copies, do not appear. Viewing an old run replays its saved transcript and makes no model calls.

Setup attempts to start the monitor in a background thread on the first run. That embedded monitor ends with its host process. A **standalone monitor** runs independently, so it can remain available after the workflow server stops and can display completed runs. If a standalone monitor is already listening on the configured port, Setup leaves it running.

### 9.3 How to start and stop

From the repository root, start the standalone monitor in one terminal:

```bash
cd langgraph-person3
conda run --no-capture-output -n langflow python -m p3graph.live
```

Open **http://127.0.0.1:7862/**. In another terminal, from the repository root:

```bash
cd langgraph-person3
PORT=7861 ./run_langflow.sh
```

Use the Langflow URL printed by the launcher, set **P3 · Setup**, open **Playground**, and send `run`. The monitor fills as steps execute. Use `stub` for a fixture demonstration or `live` for real model streaming.

Stop each foreground process with **Ctrl+C in its own terminal**. Stopping Langflow does not stop a separately launched monitor. Starting only the monitor displays saved activity and does not restart the workflow server.

For custom ports, local-model configuration, CLI runs and troubleshooting, see [HOW_TO_RUN.md §7](HOW_TO_RUN.md#7-live-workflow-monitor).

## 10. Evidence store

The evidence store is the team's pre-loaded COPD database. It answers routine questions locally and quickly, so agents don't search the web every time. It lives in `upmc/evidence-store/`, runs in Docker, and is built from person 1's export (`frontier-store.zip`) and the team schema. How to run it: [HOW_TO_RUN §8](HOW_TO_RUN.md#8-evidence-store-postgres--pgvector-rag-in-docker). How it works inside: [TSD §12](TSD.md#12-evidence-store-internals).

### 10.1 Scope

| Item | Value |
|---|---|
| Data window | **2023-01-01 to 2025-12-31.** The years 2020–2022 are excluded because COVID confounds the COPD evidence. |
| Contents | 50 COPD drug-trial papers (33 with full text), 41 trials (48 versions), 6 FDA labels with Medicare Part D 2023, 6 shelved programs, and market context |
| Role | Store of record for what was known up to a cutoff. A run's "is there something new?" check (2025–2026) looks outside it, then adds what it finds. |

### 10.2 Requirements

| ID | Requirement |
|---|---|
| FR-ES-1 | Every read is bound to one as-of date, given when the client is created. No document, passage or table row dated on or after it is returned. |
| FR-ES-2 | For a record with several versions (a ClinicalTrials.gov registration and its later update), a read returns the latest version dated before the cutoff, and returns only that one. |
| FR-ES-3 | **Lookups:** tabular answers without RAG or network. See the tables below. |
| FR-ES-4 | **RAG:** passages from papers, trial registrations and FDA labels, ranked by meaning (vectors) and by exact words (full text). No document takes more than 2 of the returned slots. |
| FR-ES-5 | Every passage and table row carries its document id (`pmid:…`, `nct:…`, `fda:…`, `curated:…`), unchanged from person 1's ids, so citations and the citation checker keep working. |
| FR-ES-6 | Everything a model produced is checked against its source text, and the result is stored with the row. A patient-group caution the label never names is dropped. |
| FR-ES-7 | Paid model work (embeddings and extractions) is cached and never recomputed. It can be exported and imported, so a teammate can rebuild the store with no model calls. |
| FR-ES-8 | It has the same retrieval methods as the team client (`search`, `doc`, `trials`, `targets`, `drugs`, `approvals`, `filings`, `retractions`), so it can stand in for `ctx.retrieval`. |
| FR-ES-9 | The database runs in Docker (`pgvector/pgvector:pg16`, port 5433, the team's credentials), and its data survives restarts. |

### 10.3 What you can look up

| Table | Rows | Answers |
|---|---|---|
| `drugs` | 12 | Which COPD drugs are on the market or shelved; class and pathway |
| `shelved_programs` | 6 | Why a program failed (efficacy, safety, …), the phase it reached, side effects |
| `market_stats` | 6 | Medicare Part D 2023 claims, beneficiaries, spend; label usage and warnings |
| `trials` | 48 | Phase, status, dates, enrollment, primary outcome, why it stopped (per version) |
| `paper_findings` | 50 | Per paper: design, phase, population, n, duration, drugs, primary endpoint and result, whether it was met, safety, conclusion, NCT ids |
| `label_events` | 188 | Per label: side effects (drug % vs placebo %), contraindications, warnings, patient-group cautions |
| `labels` | 6 | Indication, dosing, mechanism (verbatim and summarised), Part D numbers |
| `doc_drugs`, `trial_links`, `publications` | 246 / 56 / 50 | Which papers and trials test a drug; paper ↔ trial; journal, type, MeSH terms, whether it is an RCT |
| `chunks` | 1,644 | RAG passages, each with an embedding |

### 10.4 Acceptance

| # | Criterion | How it is verified | Status (2026-11-08) |
|---|---|---|---|
| AC-ES-1 | Nothing dated on or after the cutoff is returned, by any method | `evidence-store/tests/test_store.py` | ✅ |
| AC-ES-2 | A versioned trial returns its registration before the update date and the update after it | same | ✅ |
| AC-ES-3 | Every passage is embedded and points at an existing document version | same | ✅ |
| AC-ES-4 | Live RAG ranks the right paper first for a known question | same, with `EVSTORE_LIVE=1` | ✅ 10/10 passed |
| AC-ES-5 | It runs in Docker, survives a restart, and rebuilds from the cache with zero model calls | Manual, 2026-11-08 | ✅ |
| AC-ES-6 | Agents read from it during a run | Not wired yet | ⏳ |

### 10.5 Known limits

- A backtest at 2025-01-01 sees no on-market drug data, because the labels and the market rows built from them are dated 2025. Person 1 would need to add older label versions.
- `met_primary` is conservative: "not_reported" for 31 of 50 papers. The numbers are always in `primary_result`.
- 8 of 50 supporting quotes don't match the paper closely enough to verify (`quote_verified = false`).
- There is no Open Targets or EDGAR data, so `targets()` and `filings()` return nothing. `pmid:38724168` has no abstract.
- A few `source_ids` in the shelved and market rows point at URLs or news ids that have no document in the store.

## Update 2026-11-08: evidence-store tools and the A7 judge

See [HANDOFF.md](../../HANDOFF.md) for the full detail.

- **Ports:** monitor 7872, Studio 2034, Langflow 7870. These replace 7862, 2024 and 7860.
- **New flags:** `--search evstore`, `--tools`, `--no-judge`, `--max-cards N`.
- **Graph:** each chain is now retrieve → prompt → **toolloop** → llm → parse → … → commit. A6's commit is followed by **a7_judge**, then a6_route; with the judge on, A7's decision is what gets routed.
- **Tools:** DB read for A1–A6, DB write for A1 only, web search for A1/A4/A5, and nothing for A7. Every result is dated, within `2023-01-01 <= date < as_of`.
- **New state keys:** `tools`, `judge`, `max_cards`, `judgments`.
- **New workbook sheets:** ToolCalls, NewEvidence, Judge.
