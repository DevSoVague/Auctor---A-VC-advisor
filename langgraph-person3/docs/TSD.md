# Technical Specification (TSD) · Person 3 analysis agents

How one run works inside, in the order it happens. Requirements are in [FSD.md](FSD.md), commands are in [HOW_TO_RUN.md](HOW_TO_RUN.md), and the full node diagram is in [graph.md](graph.md).

---

## 1. System at a glance

Three folders share one codebase. The agent logic exists exactly once, in `frontier-engine/`. The two LangGraph folders and the Langflow canvas **wrap** it; none of them reimplements it.

```mermaid
flowchart LR
    subgraph FE["frontier-engine/  (the agents)"]
        AG["agents/analysis/<br/>a1 · a2 · a3 · a6<br/>_common · context · retrieval*"]
        PR["agents/prompts/*.md"]
        WB["exports/analysis_workbook.py"]
    end
    LG["langgraph-person3/<br/>p3graph (one node per step)"] --> AG
    FAST["langgraph-person3-fast/<br/>parallel cards + cache"] --> AG
    LF["Langflow canvas<br/>langgraph-person3/langflow"] --> LG
    CLI["scripts/run_analysis.py<br/>(plain async chain)"] --> AG
    AG --> LLM[("Nebius Token Factory<br/>or local OpenAI-compatible")]
    AG --> RET[("Retrieval: stub fixtures ·<br/>person 1 API · Tavily · Postgres+Milvus")]
```

| Layer | Tech |
|---|---|
| Agents, schemas | Python 3, Pydantic v2, `openai` client (OpenAI-compatible), `httpx` |
| Orchestration | LangGraph `StateGraph`, SQLite checkpointer, `interrupt()` for reviews, `Send()` for parallel cards (fast copy only) |
| Canvas | Langflow 1.8.4 in its own conda env, `langflow`. Components are generated from the LangGraph steps. |
| Outputs | `openpyxl` live workbook, Markdown and JSON logs |
| Envs | conda `hack` (engine + LangGraph + tests), conda `langflow` |

---

## 2. One run, end to end

The most common path: `python -m p3graph run`, which runs a single-shot backtest.

```mermaid
flowchart TD
    S([run]) --> SETUP[setup: run_id, RunConfig,<br/>LLM + retrieval chosen]
    SETUP --> A1[A1 chain → WhyNow]
    A1 --> R1{guided?}
    R1 -- yes --> REV1[/review A1/] --> A2
    R1 -- no --> A2[A2 chain → 3-5 graded cards]
    A2 --> R2{guided?}
    R2 -- yes --> REV2[/review A2/] --> NEXT
    R2 -- no --> NEXT{next_card:<br/>queue empty?}
    NEXT -- no --> A3[A3 chain → TPP for card]
    A3 --> R3{guided?} -- yes --> REV3[/review A3/] --> A45
    R3 -- no --> A45[A4 ∥ A5 hooks<br/>person 4]
    A45 --> A6[A6 chain → Verdict]
    A6 --> ROUTE{verdict}
    ROUTE -- "revise (first time,<br/>target a3/a4/a5)" --> BACK[feedback set] --> A3
    BACK -.-> A45
    ROUTE -- "kill" --> KILL[log kill + criterion] --> R6
    ROUTE -- "survives / 2nd revise" --> R6{guided?}
    R6 -- yes --> REV6[/review A6/] --> NEXT
    R6 -- no --> NEXT
    NEXT -- yes --> REP[report: report.md,<br/>result.json, analysis.xlsx]
    REP --> E([END])
```

### What happens, step by step

1. **Setup.** The run gets an id and its folder `runs/<run_id>/`. The as-of date is fixed (`today` resolves to today's date). The model is chosen: stub fixtures, a single forced model, or a per-agent profile. The retrieval and search sources are chosen.
2. **A1 works out "why now".** It gathers dated evidence from the 5 years before the cutoff and asks the model for shifts and patient segments. It then removes citations that aren't in the evidence and drops any shift dated on or after the cutoff. Finally it checks its own bars: at least 3 shifts, at least one `negative`, cited, and segments with 2+ inclusion criteria.
3. **(Guided only) A reviewer checks A1.** Choosing *rerun* sends A1 back with the comment added to its prompt.
4. **A2 proposes mechanisms.** It pulls the Open Targets association table, the drugs already in the disease, and literature per segment and per top-15 target, and it counts publications per target. The model writes cards. **Code then overwrites the model's evidence tier and validation level**, rejects cards that fail the gates, ranks the rest, and keeps 3–5.
5. **(Guided only) A reviewer checks the card set.** An edit here also rewrites the processing queue.
6. **Cards are processed one at a time, in rank order.** In the fast copy they run in parallel, up to 3 at a time.
7. **A3 writes the product profile** for the card. Every must-have has to contain a number, a threshold or a dosing rule, or it goes back to the model once.
8. **A4 and A5 run in parallel.** These are person 4's hooks. With no hook set they return nothing, and A6 runs without an asset landscape.
9. **A6 red-teams the card.** Code first builds a checklist from the evidence: prior failures, terminated trials with their stated reason, retractions matched by DOI, single-lab findings, safety signals and contradictions. The model has to answer every non-empty item, then give a verdict.
10. **Routing.** On a first `revise` that targets A3 or A4/A5, the red team's reason is stored as feedback and the card goes back once. `kill` is logged together with its kill criterion. Anything else continues to the next card.
11. **Report.** When the queue is empty, the run writes `report.md` and `result.json` and rewrites `analysis.xlsx` one last time.

---

## 3. Inside one agent: the step chain

Each agent is split into graph nodes, so a failure shows exactly where it happened:

```mermaid
flowchart LR
    RT[retrieve] --> PM[prompt] --> LM[llm] --> PA{parse<br/>valid JSON?}
    PA -- no --> FX[fix] --> LM
    PA -- yes --> V{validate / postcheck<br/>agent's bars OK?}
    V -- no, first time --> FX
    V -- no, second time --> FAIL([raise: run fails loudly])
    V -- yes --> CM[commit: emit events,<br/>write to state]
```

| Agent | Order after `parse` | Why |
|---|---|---|
| A1 | `postcheck → validate → commit` | The post-check can drop late shifts, so the "≥3 shifts" bar must be checked **after** it. |
| A2 | `grade → postcheck → commit` | No semantic retry. The bars are enforced by code gates, not by the model. |
| A3, A6 | `validate → postcheck → commit` | Validation also fixes ids deterministically before citations are checked. |
| A6 | adds `route` after `commit` | Revise / kill / survive routing |

### 3.1 The exact prompt shape

Every agent sends one system message and one user message. Fixed text goes first so that prompt caching can reuse it. The user message is assembled in `frontier-engine/agents/analysis/_common.py → build_messages`, verbatim:

```text
AS_OF_DATE: <as_of>  (you may only use evidence dated before this)

DISEASE: <disease> (<disease_id>)

AUDIENCE: <audience> | RISK_LEVEL: <risk_level> | MODALITY: <modality> | CONSTRAINTS: <constraints or 'none'>

INPUT:
<agent payload as JSON>

EVIDENCE PACK (cite only these ids):
[<id> | <YYYY-MM-DD> | <title>] | {"phase":…, "status":…, "why_stopped":…}
<body, first 600 chars>
…

REVIEWER FEEDBACK (address this explicitly):     ← only when feedback is set
<comment>
```

The system prompts are in `frontier-engine/agents/prompts/<agent>.md`. These are the hard rules the four prompts share:

```text
EVIDENCE RULES (hard)
- Use ONLY the EVIDENCE PACK and INPUT. Never use knowledge of events on or after AS_OF_DATE.
- Every claim is kind "evidence" with pack source_ids, or kind "reasoning" with []. Never invent ids.
```

Agent-specific lines that drive behaviour (verbatim):

| Agent | Line |
|---|---|
| A1 | `negative: something that RAISES the bar: a failed program, a stronger standard of care, a safety signal, a tougher regulatory stance. You must include at least one.` |
| A2 | `Set evidence_tier and validation_level to your best estimate; code recomputes both from the data, so do not inflate them.` |
| A2 | `Favour non-consensus ideas where evidence allows: a heavily published target is not automatically the best.` |
| A3 | `Each MUST be measurable: a named endpoint with an effect size or threshold, a dosing frequency, or a safety limit.` |
| A6 | `You are rewarded for being right, not for being agreeable.` |
| A6 | `Step 1 - Steelman the bear case … Step 2 - Only then decide the verdict` |

**Prompt overrides, highest priority first:**
1. A prompt typed into a Langflow node (`state["prompts"]`).
2. `langgraph-person3/prompts/<agent>.md`, which replaces the prompt.
3. `langgraph-person3/prompts/<agent>.append.md`, which is appended to it.
4. The engine's `agents/prompts/<agent>.md`.

`python -m p3graph prompt <agent>` prints the prompt actually in use and where it came from.

### 3.2 Fix message (schema and semantic retries)

```text
Fix these problems and return the full JSON:
- <problem 1>
- <problem 2>
```

### 3.3 Citation post-check (`post_check`, deterministic)

The post-check runs on every claim of every agent:

1. Stamp the claim's `agent` field and give it a stable id, `<agent[:2]>-<prefix><nnn>`.
2. Repair near-miss source ids that the evidence pack can resolve (logged as `repaired_ids`).
3. Strip any source id that is not in the pack or is dated on or after `as_of` (`stripped_sources`).
4. If an `evidence` claim is left with no source, downgrade it to `reasoning` (`downgraded_to_reasoning`).
5. Cap confidence. Evidence claims are capped by the tier: `TIER_CAP = {1: 0.9, 2: 0.75, 3: 0.6, 4: 0.4}`. Reasoning claims are capped at `min(cap, REASONING_CAP=0.5)`. A1, A3 and A6 use a cap of 0.9; A2 uses each card's tier.

The change log goes to `signals.postcheck[agent]` and to the step trace.

### 3.4 A2 grading (`a2_mechanism.grade`, deterministic)

Constants: `GENETIC_STRONG, GENETIC_WEAK = 0.5, 0.2` and `MIN_CARDS, MAX_CARDS = 3, 5`.

```text
genetic          = max Open Targets genetic_score over the card's targets
phase_in_disease = max clinical phase of any drug on those targets in this disease
human_docs       = pack docs tagged "human" that mention a target symbol
failed           = pack docs tagged "failure" that mention a target symbol
lines            = (genetic ≥ 0.2) + (phase_in_disease ≥ 2) + (human_docs ≥ 1)

tier 1  if genetic ≥ 0.5  or (phase ≥ 3 and not failed)
tier 2  if genetic ≥ 0.2  or (phase ≥ 2 and not failed)
tier 3  if any human_docs
tier 4  otherwise
level   = gold if tier 1 and lines ≥ 2 · silver if tier ≤ 2 · bronze otherwise

reject  if no target in the association table
        if no tractable modality (small_molecule / antibody / other) for any target
        if genetic < 0.2 and lines < 2
rank    = sort by (tier, -lines)
```

### 3.5 A3 measurability check

A must-have passes if it matches this regex (verbatim, case-insensitive):

```text
\d+(\.\d+)?\s*(%|ml|mg|µg|ug|weeks?|months?|days?|cells|ppb|points?|units?)|
>=|<=|≥|≤|once[- ]daily|twice[- ]daily|q\d+w|every \d+ weeks|within \d+%|non-inferior
```

The schema also requires 1–2 must-haves (`must_haves: Field(min_length=1, max_length=2)`).

### 3.6 A6 checklist and verdict check

- **Retrieval:** checklist queries over the card's terms, scanned for these tags:
  - `failure` → prior failures
  - `unreplicated` / `single_lab` → single-lab or unreplicated findings
  - `safety` → safety signals
  - `contradiction` → contradictions

  Then:
  - `trials(condition)` filtered to `terminated` / `withdrawn` / `completed_negative` trials that match the terms.
  - `retractions()` matched by DOI against the pack.
  - `doc(id)` for every source cited upstream.
- **Allowed revise targets:** `{"a1_disease", "a2_mechanism", "a3_profile", "a4_inactive", "a5_active"}`.
- **Check:** a valid `revise_target` when the verdict is `revise`; at least one risk; every risk has a `kill_criterion`, a `cheapest_test` and a claim; the premortem is non-empty.

---

## 4. The LLM layer (`frontier-engine/agents/analysis/context.py`)

| Piece | Behaviour |
|---|---|
| `NebiusLLM` | Uses the OpenAI-compatible client. Base URL `NEBIUS_BASE_URL` (default `https://api.tokenfactory.us-central1.nebius.com/v1/`). If `LOCAL_LLM_BASE_URL` is set, it is used instead, with key `LOCAL_LLM_API_KEY` or `"local"`. JSON-schema output, `temperature=0.2`, `max_tokens=16000`. |
| `StubLLM` | Returns fixture JSON from `frontier-engine/tests/fixtures/`, spending 0 tokens |
| Model choice | `--model X` forces one model for every agent. Otherwise `NEBIUS_MODEL_<AGENT>` env, then the profile. |
| `Budget` | Optional caps from `MAX_CALLS_PER_RUN`, `MAX_OUTPUT_TOKENS_PER_RUN`, `MAX_TOTAL_TOKENS_PER_RUN` and `MAX_TOKENS_PER_CALL`; 0 means no cap. Raises `BudgetExceeded`. |
| Usage | Calls, input tokens, cached tokens, output tokens and latency per agent, summed across nodes by the `merge_usage` reducer. USD appears in the workbook when `PRICE_<MODEL>_IN/OUT` are set. |

Model profiles, verbatim:

```python
PROFILES = {
    "dev":   {"default": "deepseek-ai/DeepSeek-V4-Flash-0731"},
    "final": {"default": "deepseek-ai/DeepSeek-V4-Flash-0731",
              "a1_disease": "deepseek-ai/DeepSeek-V4-Flash-0731",
              "a2_mechanism": "Qwen/Qwen3.5-397B-A17B",
              "a3_profile": "Qwen/Qwen3.5-397B-A17B",
              "a6_redteam": "zai-org/GLM-5.1"},   # different family from the writers
}
```

The fast copy adds its own `fast` profile in `workflow.yaml`: gpt-oss-120b for A1, A2 and A3, and Qwen3.5-397B for A6.

---

## 5. Retrieval layer

All retrieval goes through `ctx.retrieval`, which is already bound to `as_of`. It provides `search`, `doc`, `trials`, `targets`, `drugs`, `approvals` and `retractions`.

| `--retrieval` / `--search` | Implementation | Leakage handling |
|---|---|---|
| `stub` + `corpus` | `StubRetrieval` (fixtures, ids start with `fx:`) | Date filter |
| URL + `corpus` | `HttpRetrieval(as_of, base_url)` calls person 1's FastAPI | `as_of` is passed to the API; the pack filters again |
| `web` / `both` | `retrieval_web.py` calls Tavily; only `search()` is replaced | Asks Tavily for results before the cutoff, then drops anything published on or after it; undated results are dropped in a backtest |
| `database` / `database+web` | `retrieval_db.py`: `rag_search` (Milvus child chunk → Postgres parent text), `get_document`, `query_table` (allow-listed, bound parameters, read-only) | Date filter inside the Milvus query and the SQL; retracted documents dropped |

The database names are mapped in **one file**, `frontier-engine/agents/analysis/db_mapping.yaml`. A sample schema for the database owner is in [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md).

**Evidence store.** The team's pre-loaded store (Postgres + pgvector, data 2023–2025, in Docker) has the same methods via `evstore.StoreClient`, plus table lookups and passage RAG. It is described in [§12](#12-evidence-store-internals) and is not wired to `--search` yet.

**The evidence pack is the last guard.** `EvidencePack.add()` drops any record whose `source_date` is missing or on or after `as_of`, whatever the source.

---

## 6. Graph state (`p3graph/state.py`)

The state is plain JSON, so the SQLite checkpointer can persist every step. Reducers let parallel branches write without conflicts.

| Key | Reducer | Holds |
|---|---|---|
| `config`, `llm`, `retrieval`, `search` | replace | Run setup |
| `why_now`, `cards`, `grades` | replace | A1 and A2 outputs |
| `profiles`, `assets`, `verdicts` | `merge` (dict) | Per-card outputs. A4 and A5 write in parallel. |
| `queue`, `current` | replace | The per-card loop |
| `feedback`, `reruns`, `revise_used`, `auto_approve`, `loops` | `merge` | Control. Feedback keys are `agent` or `agent:hid`. |
| `killed`, `decisions`, `events`, `steps`, `chat` | `operator.add` (append) | Audit trail |
| `signals` | `merge` | `pub_counts`, `a2_grades`, `a6_checklist`, `postcheck` |
| `usage` | `merge_usage` (sums) | Tokens per agent |
| `work` | `merge` | Per-step scratch, `"agent[:hid]"` → pack, msgs, raw, out, problems… |

---

## 7. Extension points (no edits to `p3graph/`)

| Want to | Do |
|---|---|
| Change a prompt | Add `prompts/<agent>.md` (replace) or `<agent>.append.md` (append) |
| Add a node after any step | Add an entry under `insert:` in `workflow.yaml` (`after`, `node: file:function`, `params`, `may_goto`). Three examples ship disabled: `a3_extra_evidence` (a tool call), `a3_refine` (a loop) and `a6_safety_gate` (human gate). |
| Plug in A4 / A5 | `export P3_A4_HOOK=pkg.mod:fn` and `P3_A5_HOOK=pkg.mod:fn`. Each is `async (card, profile, ctx) -> InactiveAssets / Landscape`. |
| Use real retrieval | `--retrieval http://host:port`, or `--search database` with `DB_BACKEND=live` |
| Use person 2's schemas | Create `agents/schemas.py`; `agents/analysis/schemas.py` switches to it automatically |
| Embed in person 5's flow | `p3graph.graph.build(checkpointer)` returns a compiled subgraph. Read `cards`, `profiles`, `verdicts`, `killed`, `decisions` and `usage` from its state. |

Full guide: [EDITING.md](EDITING.md).

---

## 8. Fast copy: what changes (`langgraph-person3-fast/`)

```mermaid
flowchart LR
    SETUP --> GATE{A1/A2 in cache?}
    GATE -- hit --> FAN
    GATE -- miss --> A1 --> A2 --> FAN[fan_out: one Send per card<br/>semaphore = 3]
    FAN --> C1[card 1: A3→A4∥A5→A6→route]
    FAN --> C2[card 2 …]
    FAN --> CN[card N …]
    C1 & C2 & CN --> JOIN[cards_join: merge in rank order] --> REP[report]
```

- **Cache key.** A hash of the disease, as-of date, config, retrieval, per-agent model, effective system prompt, A1/A2 feedback, the nodes inserted after A1/A2, and the A1/A2 engine source. Entries are stored in `runs/cache/<key>.json`.
- **New flags:** `--parallel N`, `--models a1=M,…`, `--top-k N`, `--no-revise`, `--reuse-upstream / --no-reuse-upstream` and `--sequential`.
- **Equivalence.** `tests/test_fast.py` proves that stub output equals the reference build's output.

---

## 9. Gotchas

> **A Langflow Playground message becomes feedback for every agent.** Any message other than `run`, `go` or `start` is appended to every agent's prompt as `REVIEWER FEEDBACK (address this explicitly)`. A casual "hi" changes the results.

> **A6 can only send a card back to A3 or A4/A5.** The allowed targets include `a1_disease` and `a2_mechanism`, but those agents run once for the whole card set. In the graph, a revise aimed at them is overridden to `survives`, with the logged reason `revise target … is run-wide; not rerun per hypothesis`. In `chain.py` it only emits a warning before A6 runs again.

> **A second revise silently becomes `survives`.** The card is not killed. Its premortem is prefixed with `[revise cap reached; unresolved]`, and a warning and a decision row are written. Look for that prefix when you read the report.

> **The rerun cap approves silently.** After 2 reruns of the same agent on the same card, a third `rerun` is treated as `approve`, with `[rerun cap 2 reached; approved as is]` appended to the decision reason.

> **"3 to 5 cards" can include cards that failed the gates.** If fewer than 3 cards pass, A2 pads the set with rejected cards. They are forced to `bronze`, and `[failed gate: …]` is prepended to their `biggest_uncertainty`.

> **The model's tier and validation level are thrown away.** A2's grader overwrites them. If a card's tier looks wrong, the cause is in the retrieval tags or in the Open Targets data, not in the prompt.

> **A1's post-check can trigger its own retry.** Shifts dated on or after the cutoff are dropped *before* validation, so a model that cites 2022 events can fall below 3 shifts and be sent back.

> **A stub run on `--search database` stops at A1.** The stub answers cite the old `fx:` ids, which are not in the database pack, so the post-check strips them. Use a live model.

> **Date selection is independent of model mode.** No implicit fixture-date override is applied. Use `today` for current-date analysis or set an explicit ISO cutoff for a reproducible backtest. Historical fixtures remain test data; they may not support a coherent stub run at an arbitrary date.

> **Training knowledge can still leak.** Every evidence path is filtered by date, but a model whose training data runs past the cutoff (every current model does, for 2025-01-01) can reason from memory. Uncited claims are capped at 0.5, not blocked. For a strict backtest, prefer the `corpus` source over `web`, because Tavily's dates are estimates.

> **A2 makes one search per target to count publications.** `pub_counts` calls `search(symbol, k=1000)` for every associated target. That is cheap on stubs but adds up against a live retrieval API.

> **The fast copy's workbook is not updated card by card.** It is written after A1, after A2, after `cards_join` and in `report`. Guided mode, and `--sequential`, fall back to one card at a time and never use the A1/A2 cache.

> **`max(256, cap)`.** When a budget is nearly spent, a call still asks for at least 256 output tokens, so the output cap can be exceeded slightly.

---

## 10. What you can observe after a run

Everything below is in `runs/<run_id>/` unless noted.

| Artefact | Written when | Shows |
|---|---|---|
| Terminal chat view | Live, per node | Plain-language narration (`--compact` gives one line per node instead) |
| `analysis.xlsx` | After every agent commit (fast copy: per stage) | WhyNow, Segments, Hypotheses (colored by verdict), TPP, RedTeam, Claims, Events, Meta (tokens/USD) |
| `chat.md` | Live | One message per node; follow it with `python -m p3graph chat -f` |
| `log.md`, `log.jsonl` | Live | Full prompts, raw answers, reasoning text, evidence packs |
| `live.jsonl` + monitor at `127.0.0.1:7862` | Langflow runs | Streaming tokens and reasoning per step |
| `steps.md`, `steps/NNN_<node>[_hid].json` | End of run | Step table and per-step diffs. `*_llm` files hold the full prompt. |
| `report.md` | End of run | Q1/Q2/Q3/Q5, kills and the decision log |
| `result.json` | End of run | Final state (input for A7 and A8) |
| `runs/checkpoints.sqlite` | Every step (CLI) | Paused and finished threads; used by `resume` and `status` |
| `runs/cache/` (fast copy) | After A1/A2 | Reusable upstream outputs |
| `frontier-engine/runs/model_compare/<ts>/` | `model_compare.py` | Identical prompts per model, raw traces, a pass/fail summary |

## 11. Tests

| Suite | Command (in the `hack` env) | Covers | Result (2026-11-08) |
|---|---|---|---|
| Engine | `cd frontier-engine && LLM_MODE=stub pytest -q` | Agent bars, post-check, calibration, grading, leakage, workbook | 21 passed, 2 skipped (live) |
| LangGraph | `cd langgraph-person3 && pytest -q` | End to end, step order, retries, guided actions, rerun cap, hooks, custom nodes, database (11), web (7), chat, live monitor | 44 passed |
| Fast | `cd langgraph-person3-fast && pytest -q` | All graph tests plus parallel/sequential equivalence and the cache | 38 passed |
| Live | `test_live_nebius.py` (needs `NEBIUS_API_KEY`) | One real call per agent | Skipped by default |

---

## 12. Evidence store internals

The code is in `upmc/evidence-store/`: `evstore/` for the package, `sql/` for the schema, `tests/` for the tests. Requirements are in [FSD §10](FSD.md#10-evidence-store), and commands are in [HOW_TO_RUN §8](HOW_TO_RUN.md#8-evidence-store-postgres--pgvector-rag-in-docker).

```mermaid
flowchart LR
  Z[frontier-store.zip] -->|load.py| D[(documents<br/>one row per version)]
  Z -->|load.py| T[(drugs · market_stats · shelved_programs<br/>trials · parent_chunks)]
  D -->|chunk.py| C[(chunks)]
  C -->|embed.py · Nebius Qwen3-Embedding-8B| V[(chunks.embedding vector 4096)]
  V -.cached.- EC[(embedding_cache)]
  D -->|extract.py · Nebius gpt-oss-120b| X[(paper_findings · label_events)]
  X -.cached.- EX[(extractions)]
  D -->|extract.py · deterministic| P[(publications · labels<br/>doc_drugs · trial_links)]
  V & T & X & P --> S[StoreClient as_of]
```

### 12.1 Build, step by step

1. **Schema.** `db.apply_schema` runs `sql/000_team_schema.sql`, person 1's file copied verbatim from `team-role-1-pubmed` @ e8a9065, and then `sql/001_rag_and_lookups.sql`. Every statement is `IF NOT EXISTS` or `CREATE OR REPLACE`, so it is safe to rerun, and 001 only adds things.
2. **Load** (`load.py`). The records become `documents`, upserted on `(id, source_date)`, so each ClinicalTrials.gov version is its own row. `version_label` and `full_text` are lifted out of `fields` into their own columns, and NUL bytes from PDF text are stripped. The tables are upserted with `drugs` first, because the others reference it.
3. **Chunk** (`chunk.py`). Each document version is cut into passages of about 1,200 characters, with a 150-character overlap, splitting at paragraphs first and then sentences.
   - PubMed: the abstract plus each full-text section. Boilerplate (author contributions, conflicts of interest, references and the like) is skipped.
   - ClinicalTrials.gov: each version's registry text.
   - FDA: each label section on its own.
   - Curated: the body.

   What gets embedded is `"title | section\n" + passage`, so each passage keeps its context. `embed_hash` is the SHA-256 of that exact string.
4. **Embed** (`embed.py`). A passage takes its vector from `embedding_cache` when the hash is already there. Otherwise it is sent to Nebius in batches of 16, 4 at a time. Queries get Qwen3's instruction prefix (`Instruct: … \nQuery: …`); passages don't.
5. **Deterministic tables** (`extract.py`):
   - `publications` from the PubMed fields.
   - `labels` from the FDA sections plus the Part D numbers.
   - `trial_links` from `NCT\d{8}` matches in the paper text.
   - `doc_drugs` from the ClinicalTrials.gov interventions, cleaned of doses and devices, and matched to `drugs` by name, brand, or the first word of a single-ingredient drug.
6. **LLM tables** (`extract.py`). One call per paper (prompt `paper_findings/v2`) and one per label (`label_events/v2`). Each uses a JSON schema with a fallback to `json_object`, and retries up to 3 times with the validation errors. Outputs go into the team `extractions` table, keyed by `(text_hash, prompt_version)`. The lookup tables are rebuilt from the cache on every run.

### 12.2 Checks on model output

| Field | Check | Result now |
|---|---|---|
| `paper_findings.nct_ids` | Kept only when the id appears verbatim in the paper | Applied |
| `paper_findings.quote_verified` | At least 80% of the quote's 4-word sequences occur in the paper, after normalizing to lowercase letters and digits | 42 / 50 |
| `paper_findings.interventions` | `placebo` removed | Applied |
| `label_events.term_verified` | Every content word of the term, by its first 6 letters, occurs in the label | 188 / 188 |
| `label_events` population cautions | Dropped when not verified | v1 invented "pregnancy" for Spiriva; v2 plus this check removed it |
| `label_events` placeholders | Terms like `none` or `not applicable` are dropped | Applied |

### 12.3 Reading (`retrieval.py`, `StoreClient`)

- **As-of filtering is done in SQL helpers, not in Python.**
  - `documents_as_of(d)`: `DISTINCT ON (id) … WHERE source_date < d ORDER BY id, source_date DESC`.
  - `trials_as_of(d)`: the same, per `trial_id`.
  - `chunks_as_of(d)`: only the chunks of the visible version of each document.
  - Every lookup method also filters its table's date column with `< as_of`.
- **RAG (`rag`)** takes the top 200 by cosine distance (`embedding <=> q`) over `chunks_as_of`. It also takes the top 200 by full text, using `ts_rank_cd(tsv, query, 1)`. The two lists are merged by weighted reciprocal rank: score = Σ w / (60 + rank), with w = 1.0 for vectors and 0.6 for full text. No document can contribute more than 2 passages.
- **Full-text query.** Each query word becomes `term_query(t)`, and the words are OR-ed together with `any_terms(...)`. A word found in more than 20% of chunks is ignored, which is a cheap substitute for IDF weighting. If every word is that common, the two rarest are kept.
- **`search`** turns passage hits into document `Record`s, keeping their best 3 passages in `fields.matched_chunks`.
- **`trials` / `approvals`.** A condition of "COPD" also matches "chronic obstructive". `approvals` matches the label's `indications_and_usage` section and returns it as `fields.indication`.
- **Concurrency.** psycopg is used synchronously behind a lock, and the async methods run it in a worker thread. The query embedding is the only network call.

### 12.4 Runtime

| Item | Value |
|---|---|
| Image / container | `pgvector/pgvector:pg16` (pgvector 0.8.7) / `evidence-store-db`, from `evidence-store/docker-compose.yml` |
| Port / URL | 5433 → 5432 / `postgresql://frontier:frontier@localhost:5433/frontier` |
| Volume | `evidence-store_pgdata`; `restart: unless-stopped` |
| Paid tables | `embedding_cache` (1,655 vectors) and `extractions` (112). Moved as tab-separated files with `cache-export` / `cache-import`, which works across Postgres and pgvector versions |
| Index | GIN on `chunks.tsv`. No vector index: pgvector's ANN indexes stop at 2,000 dimensions, and scanning 1.6k rows takes milliseconds |

### 12.5 Gotchas

> **The as-of check is strict (`<`).** A document dated exactly on the cutoff is hidden, the same rule the rest of the engine uses.

> **The on-market drug data is dated 2025.** The FDA labels are the 2025 label versions, and person 1 dated the drug and market rows by them. So a cutoff of 2025-01-01 shows only 3 shelved drugs, and no labels or market rows.

> **Changing a chunking rule changes the hashes.** Changed passages are embedded again (paid). Passages that didn't change still come from the cache. Changing a prompt means changing its `prompt_version`, which re-runs only that extraction.

> **`EMBED_MODEL` in your shell overrides the store's model.** The engine's `--search database` backend reads the same variable name. If they differ, the store would embed queries with the wrong model. Leave it unset, or set it to `Qwen/Qwen3-Embedding-8B`.

> **Only one database can hold port 5433.** The team repo's compose file uses the same port.

## Update 2026-11-08: evidence-store tools and the A6b judge

See [HANDOFF.md](../../HANDOFF.md) for the full detail.

- **Ports:** monitor 7872, Studio 2034, Langflow 7870. These replace 7862, 2024 and 7860.
- **New flags:** `--search evstore`, `--tools`, `--no-judge`, `--max-cards N`.
- **Graph:** each chain is now retrieve → prompt → **toolloop** → llm → parse → … → commit. A6's commit is followed by **a6b_judge**, then a6_route; with the judge on, A6b's decision is what gets routed.
- **Tools:** DB read for A1–A6, DB write for A1 only, web search for A1/A4/A5, and nothing for A6b. Every result is dated, within `2023-01-01 <= date < as_of`.
- **New state keys:** `tools`, `judge`, `max_cards`, `judgments`.
- **New workbook sheets:** ToolCalls, NewEvidence, Judge.

## Update 2026-11-09: A3, A4 and A5 in parallel, shared connections, account routing, report and the workspace launcher

Commits `36263ae` (parallel A3/A4/A5, shared connections, per-agent card limits, readable report) and `b88f9ef` (frontier-ui in the repo, with a one-command backend). The functional view is in [FSD, Update 2026-11-09](FSD.md#update-2026-11-09-a3-a4-and-a5-in-parallel-per-agent-limits-and-the-one-command-workspace).

### U.1 The Langflow step flow: fork and join

Before this change, the step flow was one chain: A1 → A2 → A3 → A4 → A5 → A6 → report. Now it forks after A2:

```mermaid
flowchart LR
    A2c["A2 commit<br/>(fork point)"] --> A3["A3 row<br/>retrieve … commit"]
    A2c --> A4["A4 row<br/>gather … commit"]
    A2c --> A5["A5 row<br/>gather … commit"]
    A3 -- "a3_state" --> A4p["A4 prompt"]
    A3 -- "a3_state" --> A5p["A5 prompt"]
    A2c -- base --> J["P3 · Join<br/>(A3 + A4 + A5)"]
    A3 -- branch --> J
    A4 -- branch --> J
    A5 -- branch --> J
    J --> A6["A6 row → A6b judge → route"] --> R["Report"]
```

**How it is built (`langflow/build_steps_flow.py`):**

1. `fork = row("a2", …)`: A2's last step output.
2. `row("a3", 3, fork)` and, for each `a` in `PARALLEL = ("a4", "a5")`, `row(a, …, fork)`. All three rows take their first input from the fork, so Langflow puts their first vertices in the same layer and runs them **concurrently**. Langflow runs vertices in one layer at the same time; that is the mechanism, there is no extra scheduler.
3. `comps["a4-prompt"].set(a3_state=a3)` and the same for A5. The A4/A5 **prompt** step has an extra `a3_state` handle (added in `gen_components.py`, *"A3 profile"*, optional). Langflow can't run that vertex until A3's commit has produced output, so A4/A5 run their early steps (gather, triage, shortlist, establish) beside A3 and **wait only at the prompt**.
4. `P3StepJoin` (`step_join.py`, generated from the `JOIN` template) takes `base = fork` and `branches = [A3 commit, A4 commit, A5 commit]` (`is_list=True`). Its output feeds A6's first step.
5. Built flow: **58 nodes, 62 edges.** The node positions put the join between the A5 row and the A6 row.

**Langflow step call:** each step component calls `lf_graph.execute(state, agent, step, …, a3_state=None)`. That function:

1. Deep-copies the incoming state, so a branch never changes another branch's object.
2. For A4/A5 at `prompt`, calls `take_profiles(st, a3_state)`. That merges A3's `profiles` into the branch, and A3's `failed_cards` with the branch's own (the branch's entries win).
3. Applies feedback, a prompt override and a model override, if set (unchanged).
4. Runs `_run_cards(st, agent, step)` (U.2).
5. Rewrites the workbook after `commit` and `route` steps.

**The join (`lf_graph.join(base, branches)`):**

1. `st = deepcopy(base)`, where `base` is A2's last state, the one every branch started from.
2. For each branch, `_merge_back(st, branch, base)`. It folds in only what that branch **added or changed relative to `base`**, so the A2-era data isn't counted three times.
3. Clears `current`, rewrites `analysis.xlsx` (each branch wrote a partial workbook; this one has everything) and returns the status `joined 3 branches · N profiles · M A4/A5 outputs · failed cards: …`.

**`_merge_back` rules, per state key:**

| Key type | How it merges |
|---|---|
| `operator.add` lists (steps, events, claims, decisions, …) | Append only the items after `len(base[k])`, so each branch adds its own tail |
| `usage` (`merge_usage`) | Add the **delta** (`_usage_delta(branch, base)`), never the branch's whole total |
| `signals` | Recursive diff (`_deep_changes`) |
| `failed_cards`, `prompts` | Per key: anything a branch added or changed is kept |
| other `merge` dicts (profiles, assets, verdicts, feedback, work, …) | Per key: only entries that differ from `base` |
| everything else | Replaced, if it differs from `base` |

### U.2 Cards in parallel inside one step (`_run_cards`)

- **Card list:** `_hids(st, agent)` minus cards already in `failed_cards`. If A0 rejected the pitch (`intake.accepted is False`), nothing runs.
- **Limit:** `n = _parallel(st, agent)`. It takes the first value that parses as an int ≥ 1, in this order: `parallel_by_agent[agent]` (P3 Setup *A3 / A4 / A5 cards in parallel*, default `"3"`), then `parallel` (*Cards in parallel*, default 6), then `MAX_PARALLEL`. A1, A2 and A6 have no own field, so they use `parallel`.
- **n = 1:** cards run one after another on the same state object (the old behaviour).
- **n > 1:** `base = deepcopy(st)`, with an `asyncio.Semaphore(n)`. Each card runs `run_step` on **its own deep copy of `base`**. The results go through `asyncio.gather` and are merged back with `_merge_back` **in card order**, so steps, decisions and events stay deterministic whichever card finishes first.
- **Failure isolation:** an exception on one card goes to `log_failure`, which appends to `runs/<run_id>/failures.jsonl`, appends a readable block to `failures.log` (error, last 5 steps, the last 6,000 characters of the traceback) and records `failed_cards[hid] = "agent.step: error"`. The card is then skipped by every later step, including A6, and the report lists it as **failed**. For A1/A2 (`hid is None`) the error is logged and then re-raised, which stops the run. If **every** card fails at a step, the step raises `RuntimeError("every card failed at …")`.
- **Peak concurrency:** while A3, A4 and A5 overlap, up to `p_a3 + p_a4 + p_a5` card-steps run at once (9 with the defaults). Embeddings stay capped separately at 12 (U.4).

> **Gotcha.** The old gate that capped parallelism by the number of *distinct* cards was removed. With per-agent fields, three branches × N cards is the expected load, and U.4 makes it safe.

### U.3 A4/A5 start before A3 has finished (`p3graph/competitor.py`)

- **`_input(short, state, hid)`**: if `profiles[hid]` exists, it builds the real input. If not, it builds the agent's input with `model_construct(card=…, profile=ProductProfile.model_construct())`, an **unvalidated placeholder profile**. This is safe because gather, triage, shortlist and establish never read the profile.
- **`_with_profile(...)`** runs at the `prompt` step. It requires `profiles[hid]`, which by then has come in through `a3_state` → `take_profiles`, and rebuilds `s["inp"]` with the real, validated profile. If the profile is still missing, it raises *"A3 has not committed it (in Langflow, wire A3's last step into this node's 'A3 profile' input)"*.
- **In the CLI/LangGraph path,** A3 still runs before A4/A5, so the profile is always present and the placeholder is never used.

### U.4 Shared connections and the connection-storm fix

**Symptom (before the fix):** at the fork, A3, A4 and A5 each started several cards at once. Every node built a **new** `AsyncOpenAI` client, and every evidence-store query built a new embedding client, each opening fresh HTTPS connections. The burst was refused with "All connection attempts failed" and connection resets, and cards failed at retrieve, toolloop or llm.

**Fix, model calls (`agents/analysis/context.py`):**

| Piece | What it does |
|---|---|
| `_SHARED_CLIENTS` / `shared_client(key, base)` | One `AsyncOpenAI` client, and so one connection pool, per **(key, base URL, event-loop id)**, shared by every node. `timeout=300`, `max_retries=4` (SDK backoff on connection errors, 429 and 5xx) |
| `NebiusLLM.__init__` | `self.client = shared_client(key, base)` instead of a fresh client |
| `NebiusLLM.client_for(agent)` | Picks the account per call (U.5) and returns the shared client for that key |
| `NebiusLLM._request(agent, **kw)` | Every chat call, streamed or not, goes through `client_for(agent)` |
| `tool_loop._client(llm, agent)` | The tool loop uses `llm.client_for(agent)`, so tool rounds use the same account and pool |

The event-loop id is part of the key because an `AsyncOpenAI` client is bound to the loop it was first used in, and Langflow can run components on different loops.

**Fix, embeddings (`evidence-store/evstore/embed.py`):**

| Piece | Value / behaviour |
|---|---|
| `_CLIENTS[(key, EMBED_BASE_URL, loop)]` | One embedding client per account and loop. `timeout=120`, `max_retries=6` |
| `_GATES[loop]` = `asyncio.Semaphore(CONCURRENCY)` | **At most 12** embedding calls in flight per loop, across all agents, cards and branches. Each call takes about 1–3 s |
| `BATCH = 16` | Texts per embedding request when building the store |
| `_QUERY_CACHE[(model, query)]` | Query vectors cached for the life of the process (at most 4,096; the oldest is evicted first). Cards and agents search the same drug and target names, so repeats are free |
| `_PENDING[(model, query, loop)]` | **In-flight dedupe:** if the same query is already being embedded, later callers `await asyncio.shield(future)` instead of making a second call. On error the future carries the exception, and `fut.exception()` marks it retrieved, so asyncio doesn't warn "Future exception was never retrieved" |

### U.5 Account routing (several Nebius keys)

**Which key a model call uses (`client_for(agent)`):**

1. `NEBIUS_API_KEY_<AGENT>`: the agent label in upper case, e.g. `NEBIUS_API_KEY_A5_ACTIVE`.
2. Otherwise `ACCOUNT_KEY.get()`, the account of the **current async context** (U.5.1).
3. Otherwise the default client (`NEBIUS_API_KEY`).
4. With `LOCAL_LLM_BASE_URL` set (a local server), the keys are ignored and everything uses the local client.

**Which key an embedding call uses (`embed.client()`):** `KEY_OVERRIDE.get()` if set, else `EMBED_API_KEY`, else `NEBIUS_API_KEY` (`evstore/config.py`). `EMBED_BASE_URL` falls back to `NEBIUS_BASE_URL` in the same way. `agents/analysis/retrieval_db.py` (`--search database`) also reads `EMBED_API_KEY` / `EMBED_BASE_URL`, falling back to the `NEBIUS_*` values.

#### U.5.1 A4 and A5 on one shared account

- `competitor.account_key()` returns `NEBIUS_API_KEY_A4_A5` (stripped), or `""`.
- Every A4/A5 node body runs inside `with _Account(account_key()):`. On enter, if a key is set, it sets **both** context variables: `context.ACCOUNT_KEY` (model calls) and `evstore.embed.KEY_OVERRIDE` (embeddings; imported lazily, skipped if evstore isn't on the path). On exit, it resets them in reverse order.
- Because these are `contextvars`, the setting follows the `asyncio` task. **Everything** A4 and A5 trigger goes to that account: their own LLM calls, tool-loop rounds, evidence-store searches and any A7 citation check run inside their nodes. A3, running at the same time in another task, keeps its own account.

**`.env` keys (repo root, gitignored):**

| Variable | Used by | Fallback |
|---|---|---|
| `NEBIUS_API_KEY` | Default for every agent | none (live mode fails without it) |
| `NEBIUS_API_KEY_A4_A5` | All of A4 + A5 (U.5.1) | default |
| `NEBIUS_API_KEY_<AGENT>` | One agent label | context account, then default |
| `EMBED_API_KEY`, `EMBED_BASE_URL` | Evidence-store and database query embeddings | `NEBIUS_API_KEY`, `NEBIUS_BASE_URL` |

**`.env` parsing (`evstore/config.py::_load_env_file`):** files are read in this order: `evidence-store/.env`, then the repo-root `.env`, then `../frontier-engine/.env` (old layout). `setdefault` means the first value found wins, and the real environment beats all of them. **Unquoted values have inline comments removed** (`KEY=abc   # note` → `abc`). Quoted values keep exactly what is inside the quotes. Before this fix, an inline comment became part of the key and every embedding call returned 401.

### U.6 State and Setup fields

| Key / field | Type | Set by | Read by |
|---|---|---|---|
| `parallel` | int ≥ 1 | P3 Setup *Cards in parallel* (default 6) | `_parallel` for A1, A2, A6 and blank per-agent fields |
| `parallel_by_agent` | `{"a3": "3", "a4": "3", "a5": "3"}` (strings; blank = fall back) | P3 Setup *A3/A4/A5 cards in parallel* | `_parallel(st, "a3" / "a4" / "a5")` |
| `failed_cards` | `{hid: "agent.step: error"}` | `log_failure` | `_run_cards` (skip), join, `report_md` |
| `cite_mode` | `"node"` / `"end"` / `"off"` | P3 Setup *A7 citation check*, **now defaults to `off`** | A7 gate; `cite = mode != "off"` |

> **Gotcha.** Setup maps the dropdown with `{"every node": "node", "end only": "end", "off": "off"}.get(value, "node")`. An unknown value therefore turns the check **on** (`node`). Only the dropdown's own three values are safe.

> **Gotcha.** With `cite_mode` set to `node`, stub runs fail at A1 validate. The fixtures cite ids the A7 gate rejects. That's a fixture limit, not a bug: use `end` or `off` for stub runs.

### U.7 Report (`p3graph/nodes.py::report_md`)

The report is rewritten to read top-down for a VC reader. Plumbing stays in the workbook and logs. Its order:

1. **Title and summary line:** `As of … · X of Y hypotheses analysed · N survive · K killed · F failed`. Stub runs get a `> **Stub run.**` banner.
2. **At a glance:** one row per card that is done (profiled, verdicted or failed), with columns `# | Hypothesis | Evidence (tier · validation) | Red team | Competition | Licensable assets`.
   - The verdict reads `failed`, `killed`, `survives (revised)` and so on.
   - Competition comes from `_crowding(score)`: low < 0.34 ≤ medium < 0.67 ≤ high, plus the number of active programs.
   - Licensable assets are counted from A4's repurposing and shelved lists.
3. **The hypotheses:** one `###` section per card, containing:
   - The `_verdict_line`, which says "revision limit reached, the red team's top risk is still open" when the premortem contains `revise cap reached`, or "after one revision round". It appends A6b's `red_team_is` when the judge ran.
   - The judge's reasoning, the kill criterion and the biggest uncertainty.
   - **What the product must do** (A3 must-haves and the gap at launch).
   - **Biggest risk**, with its kill criterion and cheapest test, plus the other risks on one line.
   - **Competition** (A5: up to 4 programs and 3 readouts).
   - **Assets to license or revive** (A4: up to 5).
4. **Why now (A1):** shifts sorted by date.
5. **Run details:** mode, model, calls, tokens and where the files are.
6. **Decision log:** one line per card, `actor action → actor action → …`. A cap override is shown as "revision limit reached".

`_cell(text, limit)` makes text safe inside a table: it collapses whitespace, turns `|` into `/` and shortens at a word boundary with `…`. **The node-by-node transcript is no longer appended**; it is in `chat.md`. `tests/test_graph.py` asserts `"## At a glance"` in the report.

### U.8 Workspace launcher (`frontier-ui/server.py`)

**Command:** `python3 frontier-ui/server.py --port 7881` (default port 7880).

**Finding the engine:**
- `ENGINE` = `$FRONTIER_ENGINE` if set.
- Otherwise `../langgraph-person3`, when `frontier-ui` sits inside the repo. That is detected by `ROOT.parent` holding **both** `langgraph-person3/` and `agents/`. The `agents/` marker stops the old, separate `upmc/langgraph-person3` folder from being picked up by mistake.
- Otherwise `../grounded1to8/langgraph-person3`.
- `--engine` overrides all of these.

**`start_backend(engine, langflow_port)`** (skipped with `--no-backend`):

1. If nothing is listening on **5433** and `evidence-store/docker-compose.yml` exists, it runs `docker compose up -d` there and prints `up` or the last 300 characters of the error.
2. If Langflow's port (`--langflow-port`, else `$LANGFLOW_PORT`, else **7870**) is already open, it **reuses it and leaves it alone**.
3. Otherwise it starts `bash langflow/run_langflow.sh` with `PORT=<port>`, `start_new_session=True` (its own process group) and stdout/stderr appended to `frontier-ui/langflow.log` (gitignored).
4. A daemon thread polls the port once a second for up to 240 s. It prints "ready", "exited (code …)" or "still not answering after 4 min".

`port_open` is a 0.5 s TCP connect to `127.0.0.1`.

**`stop_backend(started)`:** on exit, for each process **this launcher started**, it sends `os.killpg(pid, SIGTERM)` and waits 20 s, then sends `SIGKILL`. Killing the whole group also takes down Langflow's child processes. The database is **not** stopped: it was started detached and keeps its data.

**UI fix:** in `static/app.js`, `verdict()` reads `verdicts[id].verdict` first, so the UI shows the right survives / killed / revise word for each card.

### U.9 Gotchas (new)

> **`.env.example` doesn't list `NEBIUS_API_KEY_A4_A5`.** It shows the older per-agent example `NEBIUS_API_KEY_A5_ACTIVE`. Add `NEBIUS_API_KEY_A4_A5=` to your `.env` by hand. Without it, A4/A5 fall back to the default key and share its rate limit with A3.

> **Langflow keeps the components it loaded at startup.** After regenerating components or the flow, restart Langflow (or the launcher) and re-import or sync the flow.

> **Only the Langflow step flow is parallel.** The CLI/LangGraph graph (`p3graph run`) still runs A3 → A4 → A5 → A6 per card, one after the other, and `HOW_TO_RUN.md` still describes that order.

> **A failed card doesn't stop the run, but it is easy to miss.** Check the report's *failed* rows and `runs/<run_id>/failures.log`.

> **The per-process caches are per process.** `_QUERY_CACHE` and the shared clients live in the Langflow process. Restarting Langflow empties them, which is harmless, but the first searches are slower again.

### U.10 Files touched

| File | Change |
|---|---|
| `agents/analysis/context.py` | `ACCOUNT_KEY`, `shared_client`, `client_for`, `_request` |
| `agents/analysis/tool_loop.py` | `_client(llm, agent)` |
| `evidence-store/evstore/config.py` | Repo-root `.env`, `EMBED_*` fallbacks, inline-comment and quote parsing |
| `evidence-store/evstore/embed.py` | `KEY_OVERRIDE`, shared clients, semaphore, query cache, in-flight dedupe |
| `langgraph-person3/p3graph/competitor.py` | Placeholder input, `_with_profile`, `account_key`, `_Account` |
| `langgraph-person3/p3graph/nodes.py` | `report_md` rewrite (`_cell`, `_crowding`, `_verdict_line`) |
| `langgraph-person3/p3graph/state.py` | `parallel`, `parallel_by_agent` documented |
| `langgraph-person3/langflow/lf_graph.py` | `take_profiles`, `execute(a3_state=)`, `join`, `_merge_back` per-key rules, `_parallel(st, agent)`; distinct-card gate removed; no transcript in the report |
| `langgraph-person3/langflow/gen_components.py` | `a3_state` handle on the A4/A5 prompt, `P3StepJoin`, Setup fields `parallel_a3/a4/a5`, `cite_mode` default `off` |
| `langgraph-person3/langflow/build_steps_flow.py` | Fork after A2, `a3_state` wiring, join before A6 |
| `langgraph-person3/tests/test_graph.py` | Asserts the new report heading |
| `frontier-ui/` (`server.py`, `static/app.js`, `.gitignore`) | Launcher, engine detection, verdict fix, `langflow.log` ignored |
