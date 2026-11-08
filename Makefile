.DEFAULT_GOAL := help
UV ?= uv
COMPOSE ?= docker compose

.PHONY: help setup rebuild up down schema reset-db ingest test lint run backtest api

help:
	@echo "setup     install the locked environment (uv sync --locked)"
	@echo "rebuild   delete .venv and rebuild it from uv.lock, then test"
	@echo "up        start Postgres with pgvector"
	@echo "down      stop Postgres (data is kept in the pgdata volume)"
	@echo "schema    apply src/frontier/store/schema.sql (safe to rerun)"
	@echo "reset-db  drop and recreate the evidence tables (keeps paid tables)"
	@echo "ingest    load COPD data            (not built yet)"
	@echo "test      ruff + pytest with LLM_MODE=stub"
	@echo "run       live COPD run, guided      (not built yet)"
	@echo "backtest  as_of 2021-01-01           (not built yet)"
	@echo "api       FastAPI on :8000"

setup:
	$(UV) sync --locked

# The reproducibility check: the environment must come back from the lockfile alone.
rebuild:
	rm -rf .venv
	$(UV) sync --locked
	$(MAKE) test

up:
	$(COMPOSE) up -d --wait db

down:
	$(COMPOSE) down

schema:
	$(COMPOSE) exec -T db psql -v ON_ERROR_STOP=1 -U frontier -d frontier < src/frontier/store/schema.sql

# Drops the evidence tables only. extractions, llm_cache and llm_calls hold paid model
# work and the cost log, so a reset never drops them. No CASCADE: fail loudly if
# anything new depends on these tables.
EVIDENCE_TABLES := doc_entities, documents, entities, programs
reset-db:
	$(COMPOSE) exec -T db psql -v ON_ERROR_STOP=1 -U frontier -d frontier -c "DROP TABLE IF EXISTS $(EVIDENCE_TABLES);"
	$(MAKE) schema

ingest:
	@echo "make ingest: not built yet (PRD build order step 4)"; exit 1

test:
	$(UV) run --locked ruff check .
	LLM_MODE=stub $(UV) run --locked pytest

lint:
	$(UV) run --locked ruff check --fix .
	$(UV) run --locked ruff format .

run:
	@echo "make run: not built yet (PRD build order step 7)"; exit 1

backtest:
	@echo "make backtest: not built yet (PRD build order step 10)"; exit 1

api:
	$(UV) run --locked uvicorn frontier.api.main:app --reload --port 8000
