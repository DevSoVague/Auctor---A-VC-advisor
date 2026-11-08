# Migrations

Until the COPD store is loaded on Saturday night, change `../schema.sql` directly and rebuild the evidence tables with `make reset-db`.

After that load, every schema change goes here as a numbered file: `001_<name>.sql`, `002_<name>.sql`, and so on, applied in order with `psql`. Rules:

- Additive only: add tables, columns or indexes. Never drop or rewrite existing data.
- Also apply the change to `../schema.sql`, so a fresh database matches a migrated one.
- Never touch the paid tables (`extractions`, `llm_cache`, `llm_calls`) in a way that loses rows: they hold model work we paid for and the cost log.
