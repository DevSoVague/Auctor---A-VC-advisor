# Person 3 hooks: analysis agents A1, A2, A3, A6

This page is for persons 1, 2, 4 and 5. It covers how your work plugs into ours and how ours plugs into yours. Everything below works today with stubs:

```
LLM_MODE=stub pytest -q
python scripts/run_analysis.py
```

## Calling our agents (hooks out)

| Agent | Call | Returns | Answers |
|---|---|---|---|
| A1 | `await a1_disease.run(cfg: RunConfig, ctx)` | `WhyNow` | Q1, why now |
| A2 | `await a2_mechanism.run(why_now, ctx)` | `list[HypothesisCard]`, 3 to 5 cards, ranked | Q2, mechanism |
| A3 | `await a3_profile.run(card, ctx)` | `ProductProfile` | Q3, TPP |
| A6 | `await a6_redteam.run(RedTeamInput(card, profile, why_now, inactive, landscape), ctx)` | `Verdict` | Q5, risks and kill |

All four live in `agents/analysis/`. `agents/analysis/chain.py` shows the full order, including the one revise round. Person 5 can copy that order into `flow/flow.py`.

### What `ctx` must have

Duck-typed; no import needed. Person 2's `RunContext` only has to provide these attributes:

- `config`: a `RunConfig` (`as_of`, `disease`, `disease_id`, `audience`, …)
- `retrieval`: async methods `search`, `doc`, `trials`, `targets`, `drugs`, `approvals`, `retractions`, already bound to `as_of`
- `llm(agent=, messages=, schema=)`: an async call that returns a validated Pydantic instance
- `emit(event, payload)`: the event hook
- `feedback: str | None`: the reviewer's comment for a rerun. Person 5 sets it before a checkpoint rerun and clears it after.
- `signals: dict`: side outputs (below)

## Events (`ctx.emit`)

These events feed live status in the API and UI, the decisions table and the workbook.

| Event | Payload | Who uses it |
|---|---|---|
| `agent_started` | `agent`, `hypothesis_id` | Person 2 API/UI status |
| `agent_done` | `agent`, `hypothesis_id`, `output` (JSON) | Person 5 checkpoint; workbook |
| `claim_added` | `agent`, `hypothesis_id`, `claim` | Person 2 `claims` table / A7 ledger |
| `card_rejected` | `hypothesis_id`, `reason` (A2 gate) | Person 5 kill log |
| `revise` | `hypothesis_id`, `revise_target`, `reason` | Person 5 flow, `decisions` |
| `kill` | `hypothesis_id`, `reason`, `kill_criterion` | Person 5 `decisions` + memo appendix |
| `signal` | `name`, `value` | See signals |
| `warning` | `msg` | Logs |

## Signals (`ctx.signals`)

- **`pub_counts`** (`{target_symbol: n_docs}`): person 4 uses it for the crowding score; person 1 uses it for the naive most-published baseline.
- **`a2_grades[hid]`**: per card, the following fields:
  - `tier`, `level`
  - `lines` (converging human evidence lines)
  - `genetic`
  - `phase_in_disease`
  - `failed_precedent`
  - `rank`
  - `pub_count`
- **`a6_checklist[hid]`**: the deterministic red-team findings (retractions, prior failures, single-lab, safety, contradictions).
- **`postcheck[agent]`**: every citation we stripped or downgraded before A7.

## What we need from you (hooks in)

| From | Need | Until then |
|---|---|---|
| Person 2 | `agents/schemas.py` | `agents/analysis/schemas.py` switches to it automatically. Please merge `A2Output` and `RedTeamInput` from `_schemas_draft.py`. |
| Person 2 | `RunContext` + router `ctx.llm` | `agents/analysis/context.py`: `NebiusLLM` (live), `StubLLM` (fixtures) |
| Person 1 | Retrieval API with `as_of` | `StubRetrieval` (fixture corpus); `HttpRetrieval(as_of, base_url)` is ready for your FastAPI service |
| Person 1 | Retrieval records shaped as below | See the record shapes |
| Person 4 | `assets_hook(card, profile, ctx) -> (InactiveAssets, Landscape)` | Pass it to `run_chain`; until then A6 runs without the landscape |
| Person 5 | Flow honours `Verdict.revise_target` once and sets `ctx.feedback` on reruns | `chain.py` does this locally |

### Record shapes we read from retrieval

Every record needs `id`, `source_date` (ISO), `title` and `body`. Optional `tags` improve A2 grading and the A6 checklist:

| Tag | Meaning |
|---|---|
| `human` | Human data |
| `failure` | Failed program or trial |
| `safety` | Safety signal |
| `unreplicated` or `single_lab` | Finding not reproduced elsewhere |
| `contradiction` | Contradicts another source |
| `genetics` | Genetic evidence |

Optional `doi` lets A6 match retractions.

The other endpoints need these fields:

- **`/trials`**: `phase`, `status` (as of the cutoff), `why_stopped`, `sponsor`, `intervention`, `mechanism`, `primary_endpoint`, `inclusion_criteria`
- **`/targets/{disease_id}`**:
  - `target_id` (for example `hgnc:IL33`)
  - `symbol`
  - `genetic_score` (0 to 1)
  - `known_drug_max_phase_in_disease`
  - `tractability` (`{small_molecule, antibody, other}` as booleans)
  - `safety` (list)
- **`/drugs`**: `name`, `mechanism`, `target_symbols`, `indications`, `max_phase_in_indication`

## Backend workbook (live)

`exports/analysis_workbook.py` builds `AnalysisWorkbook(ctx, path).attach()`. It writes `runs/<run_id>/analysis.xlsx` and rewrites it on every event, so it fills in live during a run.

| Sheet | Contents |
|---|---|
| WhyNow | Shifts |
| Segments | Patient segments |
| Hypotheses | Cards with tier/level/grades and status (colored by verdict) |
| TPP | Profiles; `version` goes up on a rerun |
| RedTeam | Verdicts and risks |
| Claims | Every claim from our agents |
| Events | Event log |
| Meta | Run config and tokens/USD per agent |

Every row carries `hypothesis_id` and `source_ids`, so the sheets join to person 4's `programs.xlsx` and person 2's ledger. Person 2 can serve the file at `GET /runs/{id}/analysis.xlsx`.

## Guarantees

- **Leakage:** agents only read through `ctx.retrieval`. Our post-check also strips any citation not in the evidence pack or dated on or after `as_of`; an evidence claim left with no source is downgraded to reasoning.
- **Calibration:** claim confidence is capped by the card's evidence tier (tier 1 ≤ 0.9, tier 2 ≤ 0.75, tier 3 ≤ 0.6, tier 4 ≤ 0.4); reasoning claims are capped at 0.5.
- **Grading in code:** the model does not set the tier or validation level. A2 computes them from Open Targets genetics, in-disease clinical phase and human literature. A failed precedent in the disease cannot earn tier 1. Cards are rejected if they have no tractable modality, or weak genetics with fewer than 2 converging human lines.
- **Measurable TPPs:** A3 rejects must-haves with no number, threshold or dosing (one retry, then it fails loudly).
- **Grounded red team:** A6 must answer a deterministic checklist (retractions, terminated trials with `why_stopped`, single-lab findings, safety, contradictions) before it gives a verdict.

## Fixtures

The fixtures are in `tests/fixtures/`; regenerate them with `python scripts/make_fixtures.py`. All fixture IDs start with `fx:`. They are fake, for stub tests only.

## Evidence store as tool calls (all agents)

The COPD evidence store (`../evidence-store`, Postgres + pgvector on `localhost:5433`) is available to every agent as OpenAI-format tool calls. Tools are off unless `AGENT_TOOLS=1`, or `--tools` in the LangGraph CLI.

**Using the tools from your own agent (for example A4 or A5):**

```python
from agents.analysis.db_tools import ToolExecutor, tools_for
from agents.analysis.tool_loop import with_tools        # or gather() + final_messages() for full control

msgs = await with_tools(ctx, "a4_inactive", msgs, pack)  # phase 1: the model calls tools; results go into pack
out = await ctx.llm(agent="a4_inactive", messages=msgs, schema=YourSchema)   # phase 2: the usual structured call
```

The loop has two phases on purpose. DeepSeek, Qwen and GLM on Nebius skip tools when `tools` and a json_schema `response_format` are sent in the same call.

### Who may call what

`db_tools.ACCESS` enforces this; a call outside it returns an error to the model.

| Agent | DB read | DB write | Web (Tavily) |
|---|---|---|---|
| A1 new data | ✓ | ✓ (`pubmed_search`, `trials_search`, `check_exists`, `fetch_source`, `ingest_document`) | ✓ |
| A2 hypothesis | ✓ | | |
| A3 product | ✓ | | |
| A4 pre-existing | ✓ | | ✓ |
| A5 competitors | ✓ | | ✓ |
| A6 red team (+ checkers) | ✓ | | |
| A6b judge | none: it sees only A3's and A6's outputs | | |

**Read tools:** `store_coverage`, `search_evidence`, `get_document`, `list_trials`, `list_drugs`, `market_stats`, `shelved_programs`, `paper_findings`, `papers_for_drug`, `label_events`, plus `web_search` (A1/A4/A5).

### Dates

Every result carries:
- `published`: the record's date.
- `date_kind`: what that date means. Examples are `pubmed_publication_date`, `ctgov_record_version_date`, `fda_label_published` and `curated_as_of`.
- `age_days`: days before `as_of`.
- The record's own dated fields: trial `first_posted`, `start` and `completion`; `shelved_date`; the Medicare `period`.

### Date window

`2023-01-01 <= published < as_of`. The floor is `DATA_FLOOR`; it excludes the COVID-era data from 2020–22. The `as_of` cutoff is enforced in SQL and again in the pack. Open Targets associations are a reference table and are exempt from the floor: any release dated before `REFERENCE_CUTOFF` (2025-01-01) may be used.

### A1 writes

- **Dates:** the publication date comes from the official record: PubMed `ArticleDate` or `PubDate`, ClinicalTrials.gov `last_update_posted`, Crossref, or page metadata. A record with no date, a year-only date, or a date outside the window is rejected.
- **Text:** the body is stored verbatim.
- **Duplicates:** discarded when they match by id+version, DOI, URL, text hash, or title+year.
- **After a write:** the new record is chunked and embedded at once.
- **Limits:** `A1_MAX_INGESTS` per run (default 5). `A1_INGEST_DRY_RUN=1` rolls back.
- **Logs:** every write goes to `ingest_log` and `documents.origin = 'a1'`.

### Events

| Event | Payload |
|---|---|
| `tool_call` | `{agent, tool, args, n, ids, dates, error, ms, checker?}` |
| `tools_done` | — |
| `new_evidence` | `{inserted, outcomes}` (A1) |

The workbook has matching **ToolCalls**, **NewEvidence** and **Judge** sheets.

### Open data gaps (ask person 1)

- **Open Targets:** the store's `targets` table is empty. Until it's filled, A2 uses the fixture's 2020-11 release (13 targets).
- **Google News:** no records yet.
- **Market data for backtests:** FDA, label and market data are all dated 2025, so a backtest before 2025 sees none of it.
