# Plan: the investor memo (agent 8 and its renderers)

Owner: person 5 · Branch: `team-role-5` · Status: plan, not built

## 1. What we are building

A separate agent, **A8 Memo**, that runs once at the end of every run. It reads everything the
other agents produced, across all hypotheses that ran in parallel, and writes a 2-page investor
memo plus an appendix. Output: `memo.docx` and `memo.pdf` (and `programs.xlsx` for the program
table).

**Team decisions this plan follows** (they replace parts of docs/PRD.md and the problem
statement, which are guidance only):

| Topic | Decision | Source |
|---|---|---|
| Orchestration | Hypotheses run **in parallel**, one branch per card, then join | Team; `person3-full` FSD FR-HYP-2, TSD §8 |
| Run | **Live COPD memo only**; no 2021 backtest memo | Team |
| Data | 50 sources from 2023-2025 in the database, plus new 2026 sources | Team |
| Format | Word, with charts as images, plus a PDF copy | Team |
| "Return worth the risk?" | Qualitative rating built from data the agents already produce | Team |
| "Weak vs other diseases" | One disease, so absolute bars, and the memo says so | PRD coverage table |
| Spec of record for the flow | `person3/langgraph-person3/docs/FSD.md` and `TSD.md` on `person3-full` | Team |

## 2. Where A8 sits in the run

```
setup → A1 why-now → A2 cards (3-5)
          → fan_out: one branch per card (LangGraph Send), run in parallel
               card h1: A3 → (A4 ∥ A5) → A6 → route (revise once / kill / survive)
               card h2: …
               card hN: …
          → cards_join      (waits for every branch; merges in A2 rank order)
          → A7 citations    (gate: nothing reaches the memo unchecked)
          → A8 memo agent   (this plan)
          → render: memo.docx, memo.pdf, programs.xlsx
```

**How the parallel branches reach A8.** Every branch writes into shared state through reducers
that already exist in `p3graph/state.py`: `profiles`, `assets`, `verdicts` (dict merge, keyed by
hypothesis id) and `killed`, `decisions`, `events` (append, each row tagged with its hypothesis
id). `cards_join` only continues once every branch is done, so A8 always sees the complete set:
survivors, killed cards and every decision from every branch. A8 never reads a branch directly.
It reads one bundle (below).

## 2a. Hard rule: A8 uses no outside data

A8 **never retrieves, searches or browses**. Its only input is `MemoInput` (below), which holds
what agents 1 to 7 already produced and A7 already checked. This keeps the memo free of
hallucinated or unchecked sources.

- **No retrieval:** A8 gets a context with **no retrieval client and no web search**. Any attempt to use one raises an error, and a test proves A8 makes zero retrieval calls.
- **Allowed citations:** a statement may cite only claim ids present in `MemoInput`, and only claims A7 kept or downgraded. Source ids come along with those claims; A8 cannot add new ones.
- **Names:** company, asset and drug names must already appear in the A4/A5 outputs (the candidate menu). Nothing else is allowed.
- **Numbers:** every number must come from an upstream claim. A8 may not introduce figures from model memory: no market sizes, prices or deal values unless an upstream agent cited them.
- **A8's own reasoning:** comparisons and judgements are allowed, but they are labelled `reasoning`, cite nothing, are capped at confidence 0.5, and may not state new facts.
- **Missing information:** when something is missing (e.g. no deal precedent was found upstream), the memo says so instead of filling the gap.

## 3. The one contract: `MemoInput`

To "plug in once data arrives", A8 depends on exactly one input object, built by an adapter. The
adapter is the only code that knows where upstream data lives, so changes upstream touch one file.

`MemoInput` = person 2's `A8Input` (on `main`) plus the extras the memo needs:

| Field | From | Used for |
|---|---|---|
| `config` (RunConfig) | setup | Title, as-of date, audience |
| `why_now` | A1 | Appendix A, "why now" bullets |
| `survivors: list[HypothesisDossier]` | A2, A3, A4, A5, A6 per card | Pursuits, appendix B-E |
| `killed: list[HypothesisDossier]` | A6 kills | Killed hypotheses section |
| `ledger: list[LedgerEntry]` | A7 | Which claims may be used; reference list |
| `decisions: list[Decision]` | every branch | Decision log |
| `judge: dict[hid, JudgeDecision]` | Person 3's A7 Judge (`person3-full`, `1to6merged`): rules whether each red-team attack holds against the TPP (fatal / major / minor) | "What could go wrong": only attacks that hold; conviction |
| `financials` (per hypothesis, optional) | A1-A5 once they collect financial and market data (see `handoff.md`) | "Is the return worth the risk?" with cited figures |
| `grades`, `pub_counts` | A2 signals | Evidence and non-consensus scores |
| `usage` | router / state | Cost appendix |
| `gaps: list[str]` | adapter | Honest notes when an upstream piece is missing (e.g. A4/A5 hook not set) |

Adapters (`memo/inputs.py`):
- `from_graph_state(state)`: the LangGraph state at the join (person 3's keys: `cards`, `grades`,
  `profiles`, `assets["h1:inactive"]`, `assets["h1:landscape"]`, `verdicts`, `killed`,
  `decisions`, `usage`, `signals`).
- `from_result_json(path)`: person 3's `runs/<id>/result.json`, so the memo can be built from a
  finished run without rerunning anything.
- `from_fixture()`: `tests/fixtures/a8_orchestrator.input.json` on `main`, for tests.

Missing upstream pieces never crash the memo: they become `gaps`, and the memo states them.

## 4. Code layout (`src/frontier/memo/`)

| File | What it does | Code or model |
|---|---|---|
| `models.py` | Memo-specific models: `MemoInput`, `Scorecard`, `PursuitCandidate`, `InvestmentCase`, `MemoDraft`, `Statement` (text + claim ids + agent + confidence) | Code |
| `inputs.py` | The adapters above; orders hypotheses by A2 rank so output is deterministic | Code |
| `scoring.py` | One `Scorecard` per surviving hypothesis: evidence (tier, level), non-consensus (crowding score, publication count), fit (best asset or program vs the TPP), risk (verdict, revised?, risks with no cheap test), verification (share of claims A7 kept) | Code |
| `caution.py` | Already written. Switch its input from plain numbers to `Scorecard` | Code |
| `candidates.py` | The menu of possible pursuits per hypothesis: **invest** (private company program, fit yes/partly, from A5), **access** (A4 repurposing or shelved asset, or a deprioritized A5 program), **build** (when nothing clears the fit bar; carries A3's must-haves). Each candidate keeps the claim ids behind it | Code |
| `a8_memo.py` | The agent: `async run(inp: MemoInput, ctx) -> MemoDraft`. Gives the model the scorecards and candidates; the model picks 2-3 pursuits and writes the prose. Then a code post-check (below) | Model + code |
| `sections.py` | Turns `MemoDraft` + `MemoInput` into a plain document outline (headings, bullets, tables, figures); no Word code here | Code |
| `charts.py` | The visuals, as PNG files (matplotlib) | Code |
| `render_docx.py` | Outline → `memo.docx` (python-docx): styles, bold key terms, bullets, tables, images, caution banner, numbered citations | Code |
| `render_pdf.py` | `memo.docx` → `memo.pdf` with LibreOffice headless; skipped with a warning if LibreOffice is missing | Code |
| `export_xlsx.py` | Program table and landscape matrix → `programs.xlsx` (openpyxl) | Code |
| `node.py` | LangGraph nodes `memo_node` and `render_node` that call the above, for the join point of the flow | Code |

Prompt: `src/frontier/agents/prompts/a8_orchestrator.md` (started; the caution section exists).
Fixed instructions first, for prompt caching.

**Why so much is code.** Ranking, the candidate menu, the caution flag, numbers, tables and charts
are computed, so the memo is repeatable and cannot invent a company or a statistic. The model does
what needs judgement: choosing between close candidates and writing clear prose.

## 5. A8's post-check (how the five qualities are enforced)

| Quality | Enforced by |
|---|---|
| **Actionable** | Each pursuit must be one of the candidates (named company, named asset, or build with 1-2 must-haves) and include a concrete next step for this year. Invented names are rejected; the model gets one retry |
| **Non-consensus** | Crowding score and publication count feed the ranking; every pursuit shows its crowding score; a crowded pursuit must say why it is still worth it |
| **Evidence-backed** | Every `Statement` cites claim ids; a claim is usable only if A7 kept it (`keep`) or downgraded it (`downgrade`, shown with a lower confidence). Removed claims cannot be cited |
| **Honest about risk** | Each pursuit carries its main risk with A6's kill criterion and cheapest test, specific enough to start diligence |
| **Traceable and calibrated** | Each statement keeps the agent and confidence of its claims; A8's own reasoning is labelled reasoning and capped at 0.5 (the rule person 3 uses, TSD §3.3) |

The model's choice of `proceed_with_caution` is overwritten by `caution.py`; the model only writes
the reasons out in plain words. Schema failures: retry once, then fail loudly (PRD contract).

## 6. The three investor questions, per pursuit

| Question | What the memo shows | Built from |
|---|---|---|
| **Why is this a good investment?** | 3 bullets: the unmet need at launch, the evidence strength, why now | A3 SoC gap, A2 tier/level, A1 shifts |
| **What could go wrong?** | Main risk, kill criterion, cheapest test, prior failures on the mechanism | A6 risks, premortem |
| **Is the return worth the risk?** | A rating, **upside** (low/medium/high) vs **risk** (low/medium/high), with a one-line verdict and the drivers | Upside: segment definition from A1, SoC gap and expansion opportunity (A3), crowding (A5), and deal precedents only if an upstream claim already cites one. Risk: tier, verdict, revise history, open risks |

The rating is computed by a documented rule in `scoring.py`; the model explains it. A8 does not
look anything up (section 2a): any number, such as a deal value, must come from an upstream claim
or it is left out.

**With financial data (handoff.md).** Once A1-A5 collect cited financial and market data, the memo
adds a **Deal economics** line per pursuit:

| Pursuit | Figures shown |
|---|---|
| Invest | Suggested cheque and the last round |
| Access | Estimated cost to license or acquire, and comparable deals |
| Build | Estimated development cost to proof of concept |

It also shows market size and the potential return for every pursuit, with each figure cited and
dated. Without that data the memo shows only the low/medium/high upside-and-risk rating and says
nothing about missing financial data (decision, 2026-11-08: the safest and simplest fallback).
Section 10 lists the fields this needs.

## 7. What the memo looks like

Written for investors: short sentences, the conclusion first, bold for the words that matter,
bullets rather than paragraphs, one idea per table row.

**Front (2 pages)**
1. Title line: COPD · as of date · run id. A red **Proceed with Caution** banner when flagged.
2. **Bottom line**: 3 bullets.
3. **Recommended pursuits** table: form · name · hypothesis · conviction · upside vs risk · next step.
4. One block per pursuit:
   - **The move**
   - **Why it's a good investment**
   - **What could go wrong**
   - **Return vs risk**
   - **Gap to the ideal product**
   - **What would change our call**
   - Chips: validation level · crowding score
5. **Opportunity map** chart.
6. **Catalysts** (next 12 months): timeline chart plus bullets with dates.
7. **Caution reasons**, if flagged, and the line "judged against fixed quality bars; this run
   covers one disease".

**Appendix** (one section per agent layer)
- A. Why now (A1): shifts table, patient segments.
- B. Mechanisms (A2): cards with tier and level.
- C. Ideal product profiles (A3).
- D. Assets and competitors (A4, A5): landscape matrix chart, top programs, repurposing and shelved lists.
- E. Red team (A6): risks and premortem per hypothesis.
- F. **Decision log**: every reviewer, red-team and orchestrator decision from every parallel branch, grouped by hypothesis.
- G. **Killed hypotheses**: reason and the kill criterion that fired.
- H. Citations (A7): ledger summary and the numbered reference list with source dates.
- I. Method and cost: data window (50 sources 2023-2025 plus 2026), models, tokens and cost per agent, and any `gaps`.

**Charts** (`charts.py`, colour-blind-safe palette, readable in black and white):
- Opportunity map: evidence strength (x) vs crowding (y); bubble size = best fit; colour = verdict.
  The top-left quadrant (strong evidence, uncrowded) is the non-consensus sweet spot.
- Return vs risk: one marker per pursuit on a 3×3 grid.
- Catalyst timeline: the next 12 months.
- Landscape matrix: mechanism × stage heat map with program counts.
- Cost per agent (appendix): bar chart.

## 8. Build order

Each step ships as a small pull request. Each one works on fixtures before real data exists.

0. **Sync**: merge `origin/main` into `team-role-5` (schemas, `RunContext`, stub mode, LangGraph); `uv add matplotlib`.
1. **Contract**: `models.py`, `inputs.py` with `from_fixture` and `from_result_json`, and memo fixtures made from `main`'s A8 fixture plus person 3's example run.
2. **Numbers**: `scoring.py`, `candidates.py`, `caution.py` on scorecards.
3. **Document**: `sections.py`, `charts.py`, `render_docx.py`, `render_pdf.py`, `export_xlsx.py`. A memo renders from fixtures with no model call.
4. **Agent**: `a8_memo.py` and the full prompt, stub mode first, then one live call.
5. **Plug in**: `node.py` at the flow's join point (after `cards_join` and A7); `from_graph_state`.
6. **Live run**: the COPD run on real data, then review the memo against the five qualities.

## 9. Tests (`tests/memo/`, all with `LLM_MODE=stub`)

- **No outside data:** A8 runs with a context whose retrieval raises on any call; the stub run passes with zero retrieval calls. A statement citing a claim id or source id not in `MemoInput` is rejected. So is a name not in the candidate menu.
- **Adapters:** each adapter yields a valid `MemoInput`. A run with no A4/A5 output yields `gaps`, not a crash.
- **Order:** hypotheses come out in A2 rank order however the parallel branches finished.
- **Numbers:** scorecards and the return-vs-risk rating match hand-worked fixture values.
- **Candidates:** build is offered only when no asset clears the fit bar.
- **Post-check:**
  - It rejects an invented company name.
  - It rejects a statement citing a claim A7 removed.
  - It rejects 1 or 4 pursuits.
- **Decision log:** it contains rows from every branch, including one revise and one kill.
- **Render:** `memo.docx` has every front and appendix heading and at least the opportunity map image. The PDF test is skipped when LibreOffice is absent.
- **End to end:** fixture → `memo.docx` + `memo.pdf` + `programs.xlsx`.

## 10. Dependencies and open items

| Item | Owner | Until then |
|---|---|---|
| Parallel `fan_out` / `cards_join` code (FSD describes `langgraph-person3-fast/`; not on GitHub as of 2026-11-08, `1to6merged` is still sequential) | Person 3 / person 5 | `from_result_json` on sequential runs |
| Naming clash: two different "agent 7"s. Person 3's **A7 Judge** (A3 vs A6 arbiter) and person 2's **A7 Citation Checker** (claim ledger). The memo needs both; agree names (e.g. `a6b_judge`) | Persons 2, 3 | Adapter reads both under separate keys |
| Citation Checker merged to `main` (`team-role-2-agent7`) | Person 2 | Fixture ledger |
| Financial fields (handoff.md), each as a cited claim. Agent 1: segment size, market size. Agent 3: development cost estimate. Agent 4: acquisition or licence cost, comparable deals. Agent 5: funding rounds, valuation, deal terms. Schema changes go via person 2 | Persons 2, 3, 4 | Qualitative rating only |
| Web search (Tavily) in A1-A5 per handoff.md: results must be dated and pass the Citation Checker before the memo may use them | Persons 2, 3, 4 | Database sources only |
| A4/A5 hooks wired into the graph (`team-role-4-adam`) | Person 4 | `gaps` note; build-only candidates |
| Richer memo fields (investment case, rating) in `schemas.py` | Person 2, by reviewed PR | Kept in `memo/models.py`; A8 still also returns the PRD `Memo` |
| Crowding score scale (assumed 0 to 1) | Person 4 | Assumption in `caution.yaml` |
| Bar values in `caution.yaml` | Team | Proposed defaults |
| LibreOffice for the PDF | Whoever runs the demo | Word only, with a warning |
