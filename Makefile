.DEFAULT_GOAL := help
UV ?= uv
COMPOSE ?= docker compose

.PHONY: help setup rebuild up down schema reset-db restore-evidence ingest test lint run backtest api

help:
	@echo "setup     install the locked environment (uv sync --locked)"
	@echo "rebuild   delete .venv and rebuild it from uv.lock, then test"
	@echo "up        start Postgres with pgvector"
	@echo "down      stop Postgres (data is kept in the pgdata volume)"
	@echo "schema    apply src/frontier/store/schema.sql (safe to rerun)"
	@echo "reset-db  drop and recreate the evidence tables (keeps paid tables)"
	@echo "restore-evidence  load Person 3's evidence store (pg_dump) into database frontier_evidence"
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

# Person 3's live evidence store (grounded1to8): 120 documents, 1,802 embedded chunks, trials,
# drugs, labels. The dump is in git LFS: run `git lfs install && git lfs pull` first. Loads into
# its own database, frontier_evidence, so the team database is untouched; then point .env at it:
#   DATABASE_URL=postgresql://frontier:frontier@localhost:5433/frontier_evidence
# Rerunning drops and reloads frontier_evidence (anything written to it since is lost).
EVIDENCE_DUMP := person3/grounded1to8/evidence-store/data/dump/frontier.sql.gz
restore-evidence:
	@gzip -t $(EVIDENCE_DUMP) 2>/dev/null || { echo "$(EVIDENCE_DUMP) is not the real file: run git lfs pull"; exit 1; }
	$(COMPOSE) exec -T db dropdb -U frontier --if-exists frontier_evidence
	$(COMPOSE) exec -T db createdb -U frontier frontier_evidence
	gunzip -c $(EVIDENCE_DUMP) | $(COMPOSE) exec -T db psql -q -v ON_ERROR_STOP=1 -U frontier -d frontier_evidence
	$(COMPOSE) exec -T db psql -q -v ON_ERROR_STOP=1 -U frontier -d frontier_evidence < src/frontier/store/schema.sql
	@$(COMPOSE) exec -T db psql -U frontier -d frontier_evidence -Atc "select 'documents: ' || count(*) from documents union all select 'chunks: ' || count(*) from chunks"

schema:
	$(COMPOSE) exec -T db psql -v ON_ERROR_STOP=1 -U frontier -d frontier < src/frontier/store/schema.sql

# Drops the evidence tables only. extractions, llm_cache and llm_calls hold paid model
# work and the cost log, so a reset never drops them. No CASCADE: fail loudly if
# anything new depends on these tables.
EVIDENCE_TABLES := parent_chunks, market_stats, shelved_programs, trials, approvals, drugs, targets, retractions, doc_entities, documents, entities, programs
reset-db:
	$(COMPOSE) exec -T db psql -v ON_ERROR_STOP=1 -U frontier -d frontier -c "DROP TABLE IF EXISTS $(EVIDENCE_TABLES);"
	$(MAKE) schema

ingest:
	@echo "make ingest: not built yet (PRD build order step 4)"; exit 1

# Tests write rows and roll them back, so they never use .env's DATABASE_URL (the shared Neon
# evidence store). They use the local Docker database; tests that need it skip when it is down.
TEST_DATABASE_URL ?= postgresql://frontier:frontier@localhost:5433/frontier
test:
	$(UV) run --locked ruff check .
	DATABASE_URL=$(TEST_DATABASE_URL) LLM_MODE=stub $(UV) run --locked pytest --ignore=tests/test_p3_citation_steps.py
	# Separate process: this test loads the older person3/1to6merged pipeline, and both
	# copies of Person 3's pipeline share the package name p3graph.
	DATABASE_URL=$(TEST_DATABASE_URL) LLM_MODE=stub $(UV) run --locked pytest tests/test_p3_citation_steps.py

lint:
	$(UV) run --locked ruff check --fix .
	$(UV) run --locked ruff format .

run:
	@echo "make run: not built yet (PRD build order step 7)"; exit 1

backtest:
	@echo "make backtest: not built yet (PRD build order step 10)"; exit 1

api:
	$(UV) run --locked uvicorn frontier.api.main:app --reload --port 8000
