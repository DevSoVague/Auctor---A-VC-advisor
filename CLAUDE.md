# Frontier Hypothesis Engine

The product and build spec live in [docs/PRD.md](docs/PRD.md). Read it before making changes; it defines the repo layout, schemas, agent contracts, flow and build order.

## Commands

```bash
make up          # docker compose up: Postgres with pgvector
make schema      # apply store/schema.sql
make ingest      # run data/ingest_*.py for COPD
make test        # ruff + pytest with LLM_MODE=stub
make run         # live COPD run, guided mode, dev profile
make backtest    # as_of 2021-01-01, single shot
make api         # FastAPI on :8000
```

## Conventions

- Prompts live in agents/prompts, never inline; no keys in code.
- Every evidence claim carries source_ids.
- Changes to agents/schemas.py go through person 2 via a reviewed pull request.
- Branches are named person-feature; keep pull requests small.
