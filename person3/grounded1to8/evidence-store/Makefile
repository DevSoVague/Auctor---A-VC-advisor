.DEFAULT_GOAL := help
PY ?= conda run -n hack python
PGBIN ?= /Applications/Postgres.app/Contents/Versions/latest/bin
PGDATA := $(CURDIR)/.pgdata

.PHONY: help db-up db-down db-logs db-up-local db-down-local build embed extract all status test ask cache-export cache-import

help:
	@echo "db-up     start Postgres+pgvector in Docker on :5433 (volume evidence-store_pgdata)"
	@echo "db-down   stop the container (data kept in the volume)"
	@echo "db-logs   tail the container logs"
	@echo "cache-import  load data/cache/*.tsv (embeddings + extractions) so nothing is re-paid"
	@echo "cache-export  write the paid tables to data/cache/*.tsv"
	@echo "db-up-local / db-down-local   same database without Docker (Postgres.app, .pgdata/)"
	@echo "build     schema + load export + chunks + deterministic tables   (free)"
	@echo "embed     embed missing chunks, Qwen3-Embedding-8B on Nebius     (cached)"
	@echo "extract   LLM tables: paper_findings, label_events, gpt-oss-120b  (cached)"
	@echo "all       build + embed + extract + status"
	@echo "status    row counts, and what as_of AS_OF can see (default 2026-01-01)"
	@echo "ask       Q='...' [AS_OF=2025-01-01]  RAG passages for a question"
	@echo "test      integration tests (EVSTORE_LIVE=1 adds a Nebius RAG test)"

db-up:
	docker compose up -d --wait db
	@echo "postgresql://frontier:frontier@localhost:5433/frontier"

db-down:
	docker compose stop db

db-logs:
	docker compose logs -f db

cache-export:
	$(PY) -m evstore cache-export
cache-import:
	$(PY) -m evstore cache-import

db-up-local:
	@test -d $(PGDATA) || ($(PGBIN)/initdb -D $(PGDATA) -U frontier --auth=trust -E UTF8 >/dev/null)
	@$(PGBIN)/pg_ctl -D $(PGDATA) status >/dev/null 2>&1 || $(PGBIN)/pg_ctl -D $(PGDATA) -o "-p 5433 -k /tmp" -l $(PGDATA)/server.log start
	@sleep 1; $(PGBIN)/psql -h localhost -p 5433 -U frontier -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='frontier'" | grep -q 1 || $(PGBIN)/createdb -h localhost -p 5433 -U frontier frontier
	@echo "postgresql://frontier:frontier@localhost:5433/frontier"

db-down-local:
	$(PGBIN)/pg_ctl -D $(PGDATA) stop

build:
	$(PY) -m evstore build
embed:
	$(PY) -m evstore embed
extract:
	$(PY) -m evstore extract
all:
	$(PY) -m evstore all
status:
	$(PY) -m evstore status --as-of $(or $(AS_OF),2026-01-01)
ask:
	$(PY) -m evstore ask "$(Q)" --as-of $(or $(AS_OF),2026-01-01)
test:
	$(PY) -m pytest -q
