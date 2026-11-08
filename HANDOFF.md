# Handoff: grounded1to8 (from db1to6merged)

> **Clone this repo and work on it separately.** It is its own repo (`DevSoVague/grounded1to8`), not a branch of the team repo or of `1to6merged`. Clone it into its own folder next to `evidence-store`, and do not copy changes back into `1to6merged` by hand:
>
> ```bash
> cd ~/Desktop/ctrl+alt+cure/upmc
> git clone https://github.com/DevSoVague/grounded1to8.git
> ```

The COPD agents 1–7 with the evidence store wired in as tool calls. This folder is a copy of `1to6merged`, which itself was not changed.

## What's new compared with 1to6merged

| Change | Where | What it does |
|---|---|---|
| DB tools | `agents/analysis/db_tools.py` | Every agent can query the evidence store through tool calls. Every result carries its publication date (`published`, `date_kind`, `age_days`). |
| Tool loop | `agents/analysis/tool_loop.py` | Two phases: the model calls tools, then the usual structured JSON call. Nebius models skip tools when both are sent together. |
| A1 ingest | `agents/analysis/db_ingest.py` | A1 searches PubMed and ClinicalTrials.gov, checks for duplicates, downloads, and stores records verbatim with their official date. Undated, out-of-window and duplicate items are rejected. |
| A6 checkers | `tool_loop.with_checkers` | Four parallel checkers (safety, already-exists, market, evidence) feed the red team. |
| A6b judge | `agents/analysis/a6b_judge.py`, `agents/prompts/a6b_judge.md` | Sees only A3's and A6's outputs, with no tools. Decides advance, revise or kill, and that decision drives the routing. |
| LangGraph | `langgraph-person3/p3graph/` | A `toolloop` step in each chain and an `a6b_judge` node. CLI flags `--tools`, `--no-judge`, `--max-cards` and `--search evstore`. |
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
  | A6b judge | | | |

  It is enforced in `db_tools.ACCESS`.
- **Linear V1:** 1 → 2 → 3 → 4 → 5 → 6 → 7 per card, with one revise round per card.

## Everything required to run this

| Need | Why | How |
|---|---|---|
| macOS/Linux, conda | envs | `conda create -n hack python=3.12` |
| Python packages (env `hack`) | engine, graph, DB client | `pip install -r requirements-person3.txt "psycopg[binary]" pgvector numpy langgraph langgraph-checkpoint-sqlite pyyaml openpyxl httpx` |
| Docker Desktop | runs the evidence-store Postgres | the `evidence-store-db` container on `:5433` |
| `evidence-store/` folder | the data, `evstore` client, schema, cached embeddings | next to this repo, or set `EVSTORE_PATH` |
| `frontier-store.zip` | person 1's export, to build the DB | `unzip ../frontier-store.zip -d ../evidence-store/data` |
| Nebius key | every LLM and embedding call | `NEBIUS_API_KEY` in `.env` |
| Tavily key (optional) | `web_search` for A1/A4/A5, and fetching plain web pages | `TAVILY_API_KEY` |
| NCBI key (optional) | faster PubMed (10 req/s instead of 3) | `NCBI_API_KEY` |
| Langflow env (optional) | the Langflow view | conda env `langflow` (Langflow 1.8.4), `./run_langflow.sh` |
| Free ports | monitor, Studio, Langflow | 7872, 2034, 7870 (override with `P3_MONITOR_PORT` / `PORT`) |

Checklist:

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
- **A6** said revise. **A6b** ruled the red team right and sent h2 back to A3 once. In the second round A6b ruled the red team partly right, and the card passed on the one-revise limit.
- **Tool calls:** 105 in total, every result dated.
- **Cost:** about 610k input and 85k output tokens on DeepSeek V4 Flash.

## Version control (how we do it)

1. **Never edit a working copy in place.** Before a big change, copy the folder (`cp -a 1to6merged db1to6merged`) and work only in the new copy. The old copy stays runnable.
2. **Each copy gets its own GitHub repo.** Use `gh repo create DevSoVague/<name> --private --source=. --push`. Commits are under your own git identity only (`git config user.name` / `user.email`), with no co-author lines.
3. **Never commit** `.env`, `runs/`, `backups/`, `*.sqlite` or `*.dump` (see `.gitignore`).
4. **Snapshot the database before anything writes to it** (A1 ingest, live runs):
   ```bash
   pg_dump -Fc postgresql://frontier:frontier@localhost:5433/frontier -f backups/evidence-store_<what>_$(date +%Y%m%d-%H%M).dump
   ```
   Restore with `pg_restore -c -d postgresql://frontier:frontier@localhost:5433/frontier <file>`.
5. **Each copy uses its own ports,** so two copies can run at the same time.
6. **Tests write to temp folders** (`conftest.py`), so `runs/` only holds real runs.

## Prompts must be editable (requirement)

- **System prompts** live in files: `agents/prompts/<agent>.md` (engine default), overridden by `langgraph-person3/prompts/` (see `workflow.yaml`), overridden by a prompt typed into a node (`state["prompts"][agent]`, the Langflow/LangGraph view). `python -m p3graph prompt <agent>` shows which file is in use.
- **To do: make user prompts editable the same way.** These are not editable today: the user-message template (`_common.build_messages`), the tool briefs (`tool_loop.BRIEFS`, `CHECKERS`, `gather_brief`, `final_messages`), A1's `NOVELTY_BRIEF` and `ANALYSIS_BRIEF`, and the A7 input template. They are Python strings. Move each into a prompt file, for example `prompts/<agent>.user.md` and `prompts/tools/<name>.md`, with `{placeholders}`. Load them through the same chain (file → workflow.yaml → node override) and show them as editable fields in Langflow/LangGraph Studio.
- **No hardcoded prompt text in code.** Code only fills placeholders (as_of, window, evidence pack, input JSON).
- Add `a6b_judge` to the `prompt` CLI choices and to the Langflow components (it is missing there now).

## Open questions and next tasks

1. **A1 "ingest a document" option. This is a priority.** The product is a startup's pitch document handed to a YC/VC firm, and the pipeline is built around it. Add an optional A1 input:
   - **Input:** an uploaded file or URL (PDF, DOCX or web page), passed as `--ingest <path|url>`, as a Langflow/LangGraph input field, or as `ingest_document` with a local file.
   - **Scan:** extract the text verbatim (pdf/docx parser), find the claims, drugs, targets, trials (NCT ids), papers (PMID/DOI) and dates it cites.
   - **Compare with the database:** for each cited item, run `check_exists`. If it's already stored, link to it. If not, `fetch_source` and ingest it with its official date. Flag claims that contradict stored evidence and anything dated outside the window.
   - **Store the pitch itself** as a `startup_doc` record. It has no publication date: use its submission date and mark `date_kind=submitted`.
   - **Hand off:** A2 builds its hypothesis cards from the pitch's mechanism, with the store as evidence. A3–A6b then test the pitch, not a generic idea. Without the option, the run stays as it is now (scan for anything new).
   - **Open questions:** do we keep the pitch private (not added to the shared store)? What do we do when the pitch cites unpublished data?
2. **A4/A5 return 0 assets on store data.** Their gather relies on ChEMBL naming, which has no data in the store. Owner: person 4. Options: map store `drugs` and `trials` into their Records, or add ChEMBL to the store.
3. **Ask person 1 to ingest more:**
   - Open Targets, into the `targets` table with tractability, genetic score and max phase. A2 uses the fixture's 2020-11 release until then.
   - Google News.
   - Market and label data dated before 2025, so backtests before 2025 see market data.
   - More 2023–25 papers than the current 50.
4. **Parallel 3/4/5 per card** (the whiteboard design). A4/A5 should take only A2's card; their `A4Input`/`A5Input` require A3's profile today. Do this after linear V1 has been timed.
5. **Tavily key:** until it's added, `web_search` errors and the agents carry on without it.
6. **`--profile final`:** Qwen3.5 truncates A3's JSON. Raise `max_tokens`, switch A3 to DeepSeek or GLM, or keep `dev`.
7. **Checkers stop at 6 calls** (`CHECKER_CALLS`) without writing a summary note. Consider one forced summary round.
8. **Cost per card is about 225k tokens** (A3 + A6 + A6b), and A1 + A2 is about 300k. Decide on budgets (`MAX_TOTAL_TOKENS_PER_RUN`) before running all cards.

## Update from the parallel session: Node Monitor and date defaults

### Node Monitor
- **Name:** the UI is now **Node Monitor** (it was "Person 3 / Live nodes").
- **Per-idea panels:** each idea gets its own workflow panel with a clickable focus mode, showing:
  - step names and model names
  - full system and user prompts
  - streamed output and exposed reasoning
  - results and errors
- **Timers:** each step has its own timer, which ticks during execution and freezes on completion or failure.
- **A4 and A5** are wired into the monitor's events and progress display.
- **Focus mode** has **Readable view** and **Step details**.
- **Readable explanations:** `narrative_markdown` was added to the agent output schemas and system prompts, so the readable explanation streams inside the required JSON. Older recordings show formatted structured results instead.
- **How it works:** the monitor reads `runs/<run_id>/live.jsonl` and polls every 250 ms. It only displays activity; it does not control concurrency or discover the whole graph by itself.

### The fixed 2021-01-01 cutoff is gone
- **Default `as_of` is now `today`,** resolved when the run starts.
- **Where:** active defaults, examples, help text, generated components and exported Langflow flows, in `langgraph-person3`/`frontier-engine`, `1to6merged` and `db1to6merged`.
- **Stub mode no longer resolves `backtest` differently.** Before, stub silently switched to the old 2021 fixture date; dates now behave the same in live and stub modes.
- **Still supported:** explicit ISO dates, and the `backtest` alias (which uses `BACKTEST_AS_OF`) in the merged copies.
- **Langflow:** four saved flows had their old Setup date defaults removed; their other settings are unchanged.
- **Kept as they were:** historical logs, evidence publication dates, test fixtures and fixture-generation data. Only the active defaults changed.
- **Side effect:** fixed stub answers may fail citation or quality checks at a different cutoff.

### Docs and checks
- FSD and HOW_TO_RUN were updated for monitor operation, readable focus mode and date behaviour.
- Workflow tests and targeted browser checks passed.

### After pulling
- Refresh open Langflow canvases to load the saved-setting changes.
- Restart running workflow servers to load the Python and schema changes before a new run.
- Older recordings won't gain events or narratives that were never captured.

## Database suggestions and input sources

### Ask person 1 to ingest (gaps today)
1. **Open Targets** into the empty `targets` table, with tractability, genetic score and max phase. Until then, A2 uses the fixture's 2020-11 release (13 targets), which is allowed under the "anything before 2025" rule for reference data.
2. **Google News**: the `news` source exists in the schema but has no records.
3. **Market and label data dated before 2025**: all FDA, label and market rows are dated 2025, so a backtest earlier than 2025 sees no market data.
4. **More 2023–25 papers** than the current 50.
5. **Trial version history**: the store keeps only the current ClinicalTrials.gov record. A trial updated in 2026 (for example NCT05166889) is out of window and gets rejected. Its older versions need the ClinicalTrials.gov history API.
6. **ChEMBL, or a mapping from the store's `drugs` and `trials` into A4/A5's Records**: A4/A5 found 0 assets because they rely on ChEMBL naming. This one is for person 4.

### Input sources A1 uses now (official, free)

| Source | Gives | Key |
|---|---|---|
| PubMed E-utilities | search, abstracts, `ArticleDate`/`PubDate` | `NCBI_API_KEY` optional, for 10 requests/s |
| PubMed Central | full text when open access | — |
| ClinicalTrials.gov v2 API | trial records, `last_update_posted` as the version date, plus first-posted, start and completion dates | — |
| Crossref | DOIs that aren't in PubMed | — |
| Tavily | web search, and extracting text from ordinary web pages (dates from the page's metadata) | `TAVILY_API_KEY`, still needed |

### Rules for writing to the database
- The date comes from the official record. Undated, year-only and out-of-window items are rejected.
- The text is stored verbatim; the model never writes record text.
- Duplicates are discarded when they match by id+version, DOI, URL, text hash, or title+year.
- Each write is chunked and embedded immediately, and logged in `ingest_log` with `origin='a1'`.
- Snapshot the database with `pg_dump` before anything writes to it.

### Next input: the startup's pitch document
See open question 1 above.
- A1 takes a PDF, DOCX or URL, extracts its text verbatim, and finds the papers, trials and drugs it cites.
- Each cited item is checked against the database; missing ones are fetched and ingested, and contradictions are flagged.
- The pitch itself is stored as a `startup_doc` with `date_kind=submitted`.
- Open questions: should the pitch stay private, outside the shared store? How should unpublished data be handled?


## grounded1to8: the citation gate after every node (2026-11-08)

This repo is a copy of `db1to6merged` with everything above, plus the following.

### Agent numbering
- **A7 = citation checker.** Ported from the team repo (`vidhi-ds-24/Ctrl-Alt-Cure`, branch `team-role-2-agent7`) into `agents/analysis/a7_citations.py`, with the prompt `agents/prompts/a7_citations.md`.
- **A6b = judge.** My judge, formerly A7, is now `a6b_judge` (`a6b_judge.py`, `a6b_judge.md`).

### Every node is grounded
- Each agent chain is now `… → postcheck/validate → **cite** → commit`. This covers A1, A2, A3 and A6.
- For A4 and A5, the check runs inside their commit node.
- **How A7 judges each (claim, source) pair:**
  - Code first: the source must exist (in the evidence pack, otherwise in the store) and be dated before `as_of`. Otherwise it is `not_found` or `after_cutoff`.
  - Then the model rules `yes`, `partly` or `no` from the source text. Skipped or garbled answers count as `no`.
- **Hallucination** means `no`, `not_found` or `after_cutoff`. The node **restarts**:
  - A1/A2/A3/A6 go back to `llm`, with the failed claims listed in the prompt.
  - A4/A5 redo prompt → llm → merge (→ score).
  - At most **2 restarts**. Whatever is still unsupported then has its sources stripped, and the claim becomes `[unverified]` reasoning with confidence ≤ 0.3.
- **`partly`** is not a restart. The claim is kept with confidence capped at 0.5.
- **The judge (A6b)** produces no claims, so it isn't checked.
- **Workbook:** new **Citations** sheet (the ledger) and **Restarts** sheet.
- **Events:** `citation_check`, `node_restart`.
- **Default: on.** `--no-cite` turns it off; in graph state, `cite: false`.
- **Cost:** one extra model call per agent output (batches of 10 pairs), plus any restarts. In stub mode only the code checks run, so it costs 0 tokens.
- **Tests:** 54 LangGraph and 138 engine tests pass, including the restart path. No live run yet: Docker and the evidence store were shut down on request.

### To run
```bash
cd ../evidence-store && make db-up     # start Docker first
cd ../grounded1to8/langgraph-person3
conda run -n hack python -m p3graph run --llm live --as-of 2026-01-01 --search evstore --tools --max-cards 1
```


## A0 intake: the input gate for the startup pitch (2026-11-08)

The startup's pitch document is the crux of the operation. A run can now be built around it: `--pitch <file|url>`. Without `--pitch`, runs work as before (an open scan for anything new).

**Flow:** `setup → intake (A0) → A1 → A2 → …`, in `agents/analysis/a0_intake.py` with the prompt `agents/prompts/a0_intake.md`.

### 1. Gate
The run stops here if any check fails, before a single token is spent. The reason goes into `report.md`.
- **Type:** PDF, DOCX, TXT, MD or URL.
- **Size:** at most `PITCH_MAX_MB` (default 20).
- **Text:** extracted verbatim (pypdf, DOCX XML, or page text), with at least 500 characters. A scanned PDF is rejected with "needs OCR".
- **Topic:** the document must be about the run's disease (at least 2 hits on terms like COPD, emphysema, FEV1, exacerbation).
- **Prompt injection:** a scan for phrases like "ignore previous instructions" or "always approve". Hits are **flagged, not obeyed**. The pitch text only ever reaches a model inside a fenced DATA block (`as_data`).

### 2. Extract
- **By code:** the cited ids (PMID, NCT, DOI, URLs).
- **By the model:** a `PitchProfile` with company, indication, mechanism, targets, modality, stage, the key claims (each with its references) and the dates mentioned. In stub mode this is code-only.

### 3. Compare with the database
Each cited item is matched against the store with `check_exists`:
- Already stored: `in_store`, linked by its store id.
- Missing: fetched and ingested through A1's write gate (official date, window, verbatim text, dedupe), giving `ingested`.
- Refused or not found: listed as a warning.

Store items cited by the pitch are added to A1's and A2's evidence packs, so they can be cited.

### 4. Store
- **Default: private.** The pitch is kept only in the run folder (`runs/<id>/pitch.txt`).
- **`--store-pitch`** also writes it to the store as `source='startup_doc'`, id `startup:<sha>`, with `date_kind='submitted'`.

### 5. Downstream
- **A1** asks why now for *this* pitch's mechanism and patients. Its shifts must rest on evidence-pack sources, not on the pitch's claims.
- **A2's first card** must be the pitch's own hypothesis, graded like any other. The other cards are the alternatives the pitch must beat.
- The pitch's claims are never treated as evidence: A7 checks every citation, and A6 attacks the product.

### Workbook and tests
- **Workbook:** a new **Intake** sheet with the gate result, the profile, the pitch's claims, and each cited item's status, store id and date.
- **Tests:** `tests/test_intake.py` (8) plus 2 graph tests: a rejected pitch spends 0 tokens, and an accepted pitch reaches A1 and A2. 56 LangGraph and 146 engine tests pass.

### Run
```bash
conda run -n hack python -m p3graph run --llm live --as-of 2026-01-01 --search evstore --tools --max-cards 1 --pitch ~/pitch.pdf
```

### Open
- **OCR** for scanned PDFs.
- **The pitch's unpublished data:** it currently only appears as a pitch claim and is never evidence. Should an analyst be able to vouch for it?
- **A shared store vs. a private one** for pitches.
- **A web form, or a Langflow/LangGraph upload field,** for the pitch.
- **Not live-tested yet:** the database and Docker were shut down.

## Fewer run folders and files (2026-11-08)

- **No folder until an agent runs.** Setup and intake no longer write anything, so a failed start, a rejected pitch, or a node error before A1 leaves no folder. Before, every Playground send created one.
- **No more `steps/` folder** (it held 90–330 JSON files per run). The same per-step record is one line in `log.jsonl`.
- **Each run now has:**

  | File | Contents |
  |---|---|
  | `live.jsonl` | Node Monitor events |
  | `log.jsonl` / `log.md` | Full step log |
  | `chat.md` | Readable chat of the run |
  | `steps.md`, `report.md`, `result.json`, `analysis.xlsx` | Written at the end |
  | `pitch.txt` | Only with `--pitch` |

- **Cleanup is never automatic.**
  - `python -m p3graph clean` lists unfinished runs: no `report.md` and idle for more than 30 minutes.
  - Add `--keep N` to also list finished runs beyond the newest N.
  - Nothing is deleted without `--yes`.
  - Only folders with uuid names are considered. `langflow.log` and `monitor.log` are never touched.
