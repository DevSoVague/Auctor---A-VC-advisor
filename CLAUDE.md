# Frontier Hypothesis Engine

The product and build spec live in [docs/PRD.md](docs/PRD.md) (original: docs/PRD.pdf). Read it before making changes; it defines the schemas, agent contracts, flow and build order. Do not edit docs/PRD.md to match the code; it is the team's verbatim copy.

## Layout: src/ package, not the PRD's flat folders

The repo uses a src/ layout (one importable package, `frontier`). Map PRD paths like this:

| PRD path | Repo path | Import |
|---|---|---|
| core/ | src/frontier/core/ | `frontier.core` |
| store/ | src/frontier/store/ | `frontier.store` |
| data/ (ingest code) | src/frontier/ingest/ | `frontier.ingest` |
| api/ | src/frontier/api/ | `frontier.api` |
| agents/ (schemas.py, prompts/, analysis/, competitor/, citations/) | src/frontier/agents/ | `frontier.agents` |
| flow/ | src/frontier/flow/ | `frontier.flow` |
| memo/ | src/frontier/memo/ | `frontier.memo` |
| eval/ | src/frontier/eval/ | `frontier.eval` |
| tests/, pitch/, app/, docs/ | same, top level | |

Top-level `data/` holds raw downloads only and is git-ignored. Never commit data, `.env`, or run outputs.
Exception (team decision, 2026-11-08): `person3/grounded1to8/evidence-store/data/` is committed in full (export, caches, pg_dump) so the repo holds everything from Person 3's grounded1to8. The embedding cache and the pg_dump are in git LFS (`person3/grounded1to8/.gitattributes`): install git-lfs (`brew install git-lfs && git lfs install`) and run `git lfs pull` after cloning. Keep it.

## Where the repo differs from the PRD

The PRD text stays verbatim; these are the team's deliberate departures from it. Follow the repo, and add a row here (and a run log entry) for any new one.

| PRD says | Repo does | Why |
|---|---|---|
| Flat folders (`core/`, `agents/`, `data/`...) | `src/` layout; the PRD's `data/` is `src/frontier/ingest/` | Course reproducibility practice; the mapping table above |
| Orchestration: CrewAI Flows (`@persist`, `kickoff`) | **LangGraph**: fixed code edges, a Postgres checkpointer keyed by run_id, `interrupt()` at checkpoints, resume with `Command(resume=action)` | CrewAI pulls in packages with no Intel-Mac builds (lancedb, onnxruntime, cryptography), so it can't install there; no code used it yet. LangGraph installs everywhere and is already the PRD's pick for "Ask the memo" |
| Postgres on `localhost:5432` | `localhost:5433` | A local Postgres (e.g. Postgres.app) on 5432 intercepts connections |
| Model IDs in `core/profiles.yaml` | Exact Nebius IDs; closest available where a PRD model isn't on our account (noted per line) | The PRD said to copy exact IDs from the console |

The PRD's rule still holds with LangGraph: **no model decides what runs next.** Every edge is code; conditional edges are plain Python functions over the run state.

## Environment

- Python is pinned in `.python-version`; dependencies are locked in `uv.lock`. Add packages with `uv add <pkg>` (or `uv add --dev`), never pip, and commit `pyproject.toml` and `uv.lock` together.
- `make rebuild` deletes `.venv`, rebuilds from the lockfile and runs the tests. Use it to prove the environment reproduces.

## Commands

```bash
make setup       # uv sync --locked
make rebuild     # delete .venv, rebuild from uv.lock, run tests
make up          # docker compose up: Postgres 16 with pgvector
make down        # stop Postgres (data kept in the pgdata volume)
make schema      # apply src/frontier/store/schema.sql (safe to rerun)
make reset-db    # drop and recreate the evidence tables; keeps extractions, llm_cache, llm_calls
make ingest      # run the ingest scripts for COPD (not built yet)
make test        # ruff + pytest with LLM_MODE=stub
make lint        # ruff fix + format
make run         # live COPD run, guided mode, dev profile (not built yet)
make backtest    # as_of 2021-01-01, single shot (not built yet)
make api         # FastAPI on :8000
```

## Run log (required)

After every task that changes files, git state, the database or the environment, use the `runlog` skill to append an entry to `runlog/YYYY-MM-DD-name.md`: the user's prompt word for word, what was done, the justification, and the verification. Include the entry in the same commit as the work. Format: [runlog/README.md](runlog/README.md).

## Conventions

- Prompts live in src/frontier/agents/prompts, never inline; no keys in code.
- Every evidence claim carries source_ids.
- Changes to src/frontier/agents/schemas.py go through person 2 via a reviewed pull request.
- Branches are named person-feature; keep pull requests small. CI runs `make test` on every pull request.
- Commit messages and PR descriptions carry no AI co-author or "generated with" lines.
