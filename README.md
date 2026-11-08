# Frontier Hypothesis Engine

An agent workflow that takes one disease area and returns 2 to 3 pursuits a biotech VC could act on this year, with every claim cited and dated. The first disease is **COPD**.

Built for the UPMC Enterprises investment team. Early diligence is slow and manual, so good leads fall through. This tool generates and screens hypotheses before a human team commits weeks to one.

> **Status:** scaffold in place (environment, database, tests, CI); agents not built yet. The spec is in [docs/PRD.md](docs/PRD.md) (original: [docs/PRD.pdf](docs/PRD.pdf)).

## What it produces

A recommendation memo, exported to Word:

- **Two-page front** for a partner: the 2 to 3 recommended pursuits, each with a form (invest in a company, access an asset, or build a new one), the main risk, and what would change the call. Also a "Proceed with Caution" flag and upcoming catalysts with dates.
- **Appendix** for diligence: the five questions answered for each hypothesis, the competitor landscape, a decision log, the hypotheses we killed and why, and a claim-by-claim citation ledger with run costs.

There is also a competitor program table, exported to Excel.

Every surviving hypothesis answers five questions:

1. Why is now the time to work on this disease?
2. Which mechanism(s) should be targeted?
3. What would a winning product look like?
4. Which existing assets or companies could get there?
5. What could kill the hypothesis?

## How it works

Eight agents run in a fixed order. The flow is ordinary code; no model decides what runs next.

| # | Agent | Job |
|---|---|---|
| 1 | Disease analysis | Why now: recent shifts in endpoints, segments and trial tools |
| 2 | Mechanistic analysis | 3 to 5 ranked hypothesis cards with evidence tiers |
| 3 | Optimal solution | Product profile and must-haves per hypothesis |
| 4 | Inactive-in-disease assets | Repurposing and shelved-asset candidates |
| 5 | Active-in-disease assets | Competitor landscape, crowding score, catalysts |
| 6 | Red Teamer | Verdict (survives, revise, kill), risks, cheapest test |
| 7 | Citation Checker | Confirms every claim's source and that it predates the cutoff |
| 8 | Orchestrator | Ranks hypotheses, picks pursuits, writes the memo |

Hypotheses go through one at a time. Agents 4 and 5 run in parallel, the red teamer gets one revise round, and the memo is blocked until every claim passes the citation check.

**Two modes.** *Guided* pauses after each agent so a reviewer can approve, edit or rerun. *Single-shot* runs straight through to a final review.

**Backtest.** The same pipeline runs as of January 1, 2021, using only records dated before then. Its picks are scored against what actually happened (for example, the 2024 ensifentrine and dupilumab approvals) and compared with a naive "most-published targets" baseline.

## Data sources

Public sources only, each record stored with its date: PubMed, ClinicalTrials.gov, Drugs@FDA / openFDA, Open Targets, ChEMBL, SEC EDGAR, Retraction Watch (via Crossref) and patents.

## Stack

Python 3.11+ (uv), FastAPI and Pydantic v2, Postgres 16 with pgvector, LangGraph for the flow, LiteLLM routing to Nebius Token Factory models (OpenAI and Gemini as fallbacks), python-docx and openpyxl for exports, and a Base44 app for review.

## Getting started

You need [uv](https://docs.astral.sh/uv/) and Docker. uv installs the pinned Python (3.11) itself.

```bash
make setup             # install the exact locked environment
cp .env.example .env   # add API keys
make up                # Postgres 16 with pgvector
make schema            # create tables (fresh database)
make test              # lint + tests, no model calls
make api               # FastAPI on :8000, try /health
```

Not built yet: `make ingest` (load COPD data), `make run` (live COPD run) and `make backtest` (run as of 2021-01-01).

`make rebuild` deletes `.venv`, rebuilds it from `uv.lock` and reruns the tests: the check that the environment reproduces on any machine.

## Repo layout

```
src/frontier/          the importable package
  core/                config, model router, cache, cost log, profiles.yaml, prices.yaml
  store/               schema.sql, database helpers
  ingest/              ingestion, extraction, embeddings, baseline
  api/                 retrieval API (as-of filtered) and run API
  agents/              schemas, prompts, and agents 1 to 7
  flow/                orchestration, checkpoints
  memo/                agent 8, Word and Excel export
  eval/                backtest and scoring
data/                  raw downloads only, git-ignored
tests/                 tests and fixtures
notebooks/             exploration
pitch/                 pitch deck
app/                   Base44 app
docs/                  PRD
```

The PRD lists these folders at the top level; [CLAUDE.md](CLAUDE.md) has the mapping.

## Team

Five owners, one area each:

| Role | Owns |
|---|---|
| 1: Data | Store, ingestion, baseline, backtest scoring, pitch |
| 2: Platform and app | Schemas, router, retrieval and run APIs, citation checker, app |
| 3: Analysis | Agents 1, 2, 3 and 6 |
| 4: Competitor | Agents 4 and 5 |
| 5: Flow and memo | Flow, agent 8, exports, live and backtest runs |

## Contributing

New to the repo? Start with [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md): setup, where your code goes, and how changes reach `main`. Every change gets an entry in the [run log](runlog/).

- Work on your own branch, named `person-feature`, and merge through a pull request one teammate reviews.
- CI runs lint and stub tests on every pull request.
- Changes to `agents/schemas.py` go through person 2, since everyone codes against it.
- Prompts live in `agents/prompts/`, never inline. No API keys in code.
