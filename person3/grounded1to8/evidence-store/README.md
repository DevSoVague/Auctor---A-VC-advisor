# evidence-store

The COPD evidence store, data dated 2023–2025, held in Postgres with pgvector. You can read it two ways:

- **Lookups:** tables you query by drug, status, phase and so on. These are fast, need no network, and need no RAG.
- **RAG:** hybrid passage search over papers, trials and FDA labels, using pgvector similarity plus Postgres full-text search.

Everything is bound to an **as-of date**. Nothing dated on or after it is ever returned. Where a record has several versions, you get the latest one before the date.

It is built from person 1's export (`frontier-store.zip`, branch `team-role-1-pubmed` @ e8a9065) and the team schema. All document ids stay as they are (`pmid:…`, `nct:…`, `fda:…`, `curated:…`), so citations work unchanged.

## How it fits together

```mermaid
flowchart LR
  Z[frontier-store.zip<br/>records + tables] -->|make build| D[(documents<br/>versioned)]
  Z --> T[(drugs · market_stats ·<br/>shelved_programs · trials ·<br/>parent_chunks)]
  D --> C[(chunks<br/>~1.6k passages)]
  C -->|make embed<br/>Nebius Qwen3-Embedding-8B| V[(chunks.embedding<br/>vector 4096)]
  D -->|make extract<br/>Nebius gpt-oss-120b| X[(paper_findings ·<br/>label_events)]
  D -->|deterministic| P[(publications · labels ·<br/>doc_drugs · trial_links)]
  V & T & X & P --> S[StoreClient as_of]
  S --> A[agents]
```

1. **`make build`** applies `sql/000_team_schema.sql` (Cindy's file, unchanged) and then `sql/001_rag_and_lookups.sql` (additive only). It loads the export, cuts the passages, and fills the tables that need no model. It is free.
2. **`make embed`** embeds every passage that has no vector yet. Vectors are kept forever in `embedding_cache`, so rerunning a build never re-embeds a passage. The first full run cost about 530k tokens.
3. **`make extract`** runs the LLM extraction into tables, one call per paper and one per label. Results are cached in the team `extractions` table, keyed by text hash and prompt version, so a rerun costs nothing. The first run cost about 400k tokens.

Both Nebius calls use `NEBIUS_API_KEY` from the environment, `evidence-store/.env`, or `../frontier-engine/.env`.

## Quick start

```bash
conda activate hack                       # needs psycopg[binary], pgvector, numpy, openai
cd evidence-store
make db-up                                # Docker: pgvector/pgvector:pg16 on :5433, volume evidence-store_pgdata
mkdir -p data && unzip ../frontier-store.zip -d data   # -> data/frontier-store/
make cache-import                         # optional: reuse paid embeddings + extractions (data/cache/*.tsv)
make all                                  # build + embed + extract + status (0 Nebius calls after cache-import)
make test                                 # 10 integration tests
make ask Q="anti-IL-33 antibody results in COPD" AS_OF=2025-01-01
```

Notes:

- The database runs in Docker as container `evidence-store-db`. It uses the same image, port and credentials as the team repo's `docker-compose.yml`, under its own compose project name, and has `restart: unless-stopped`.
- Data lives in the `evidence-store_pgdata` volume and survives `make db-down` and restarts.
- Connection URL: `postgresql://frontier:frontier@localhost:5433/frontier`, the default `DATABASE_URL`.
- Only one database can hold port 5433. Stop the team repo's `db` container, or this one, before starting the other.
- **Sharing paid work:** `make cache-export` writes `data/cache/embedding_cache.tsv` (about 83 MB) and `extractions.tsv`. A teammate who runs `make cache-import` and then `make all` gets the same store without calling Nebius.
- **Without Docker:** `make db-up-local` runs the same database from Postgres.app binaries in `.pgdata/`.
- To point at an export somewhere else, set `FRONTIER_STORE_DIR`.

## Using it from an agent

```python
from datetime import date
from evstore import StoreClient

s = StoreClient(date(2025, 1, 1))           # bound once; agents never pass dates

# lookups (no RAG)
await s.drug_table(status="shelved")                  # curated drugs
await s.shelved(reason="efficacy")                    # failed programs + reason + side effects
await s.market()                                      # Part D claims, spend, beneficiaries, label usage
await s.trial_table(drug_id="drug:tiotropium-bromide", phase="PHASE3")
await s.findings(drug="dupilumab")                    # per-paper n, endpoint, result, met_primary
await s.papers_for_drug("ensifentrine")               # papers + trials that test it
await s.label_events(drug_id="drug:roflumilast", kind="adverse_reaction")   # term, drug %, placebo %
await s.labels()                                      # indication/dosing text + summaries + Part D

# RAG
hits = await s.rag("does blocking IL-33 reduce exacerbations", k=8)   # ChunkHit: doc_id, section, text, score
recs = await s.search("ensifentrine dyspnea", k=5)    # Records ranked by passages, fields.matched_chunks

# team RetrievalClient protocol (same method names as frontier.core.retrieval)
await s.doc("pmid:37364283"); await s.trials("COPD"); await s.approvals("COPD"); await s.drugs()
```

`search`, `doc`, `trials`, `targets`, `drugs`, `approvals`, `filings` and `retractions` have the same signatures as the team's `StubRetrievalClient`, so `StoreClient` can replace it. `Record.source` also accepts `curated` and `news`.

In SQL, use the as-of helpers instead of the raw tables: `documents_as_of(d)`, `trials_as_of(d)` and `chunks_as_of(d)`.

## Tables

| Table | Rows | From | What you look up |
|---|---|---|---|
| `documents` | 112 (105 ids) | export | Every record, with one row per version. `full_text` is filled for 33 papers. |
| `drugs` | 12 | export | 6 drugs on the market, 6 shelved; class, pathway, status |
| `market_stats` | 6 | export | Medicare Part D 2023 claims, beneficiaries, spend; label usage, side effects, warnings |
| `shelved_programs` | 6 | export | Phase reached, reason category and detail, side effects |
| `trials` | 48 (41 trials) | export | Phase, status, dates, enrollment, primary outcome, why stopped. Kept per version (registration vs live). |
| `parent_chunks` | 259 | export | Full-text sections |
| `chunks` | 1,644 | build | RAG passages of about 1,200 chars, with a 4096-dim vector and a `tsvector` |
| `publications` | 50 | deterministic | Journal, DOI, PMCID, publication types, MeSH terms, RCT yes/no, phase, whether full text exists |
| `labels` | 6 | deterministic + LLM | Brand, generic, NDA number; indication, dosing and mechanism text; Part D numbers |
| `trial_links` | 56 | deterministic + LLM | Paper ↔ NCT id, and whether that trial is in the store |
| `doc_drugs` | 246 | ctgov + LLM | Which drugs each paper or trial tests (intervention or comparator), linked to `drug_id` when it is a curated drug |
| `paper_findings` | 50 | LLM | Design, phase, population, n, duration, interventions, comparators, primary endpoint and result, `met_primary`, safety, conclusion, NCT ids, supporting quote |
| `label_events` | 188 | LLM | Adverse reactions (with drug % and placebo % when given), contraindications, warnings, population cautions |

## How LLM output is checked

Every LLM row is checked against the text it came from, and the result is stored with the row:

- **`paper_findings.nct_ids`**: only ids that appear verbatim in the paper are kept.
- **`paper_findings.quote_verified`**: at least 80% of the quote's 4-word sequences must occur in the paper. 42 of 50 pass. For the other 8, read `primary_result` with care.
- **`label_events.term_verified`**: every content word of the term must appear in the label. All 188 pass.
  - Population cautions the label never names are dropped. The first prompt version produced "pregnancy" for Spiriva from model knowledge, and this check is what removed it.

## Known gaps

- **Backtests at 2025-01-01 see no on-market drug data.** The 6 FDA labels and the market rows built from them are dated by their 2025 label versions, so before 2025 there are no labels, `label_events` or `market` rows, and only the 3 shelved drugs reported in 2023–24. Person 1 would need to add older label versions.
- **`met_primary` is conservative.** It is "not_reported" for 31 of 50 papers, including some where the result is clearly significant (for example dupilumab BOREAS, p<0.001). Many of these are crossover or post-hoc papers with no single primary endpoint. Use `primary_result`, which always contains the numbers.
- **`targets` and `filings` are empty:** there is no Open Targets or EDGAR data. `retractions` is not filled either.
- **`pmid:38724168` (dupilumab NOTUS) has no abstract** in the export.
- **Some citations point outside the store.** A few `source_ids` in the shelved and market rows are URLs or `news:` ids with no document behind them.
- **Retrieval tuning:**
  - Vectors are weighted 1.0 and full-text 0.6 in the merge.
  - Query words found in more than 20% of passages are ignored for full-text ranking.
  - Each document contributes at most 2 passages per answer.
  - With about 1.6k passages there is no ANN index; pgvector indexes stop at 2,000 dimensions, and a full scan takes milliseconds.

## Files

```
sql/000_team_schema.sql        team schema (copied verbatim from team-role-1-pubmed)
sql/001_rag_and_lookups.sql    chunks, embedding_cache, lookup tables, as-of + full-text helpers
evstore/load.py                export -> documents + structured tables
evstore/chunk.py               passages (skips author/COI/reference boilerplate)
evstore/embed.py               Nebius embeddings, cached
evstore/extract.py             deterministic tables + LLM extraction with verification
evstore/retrieval.py           StoreClient
tests/test_store.py            as-of leakage, versioning, lookups, embeddings, live RAG
```

## Restore the full database from the snapshot (fastest)

`data/dump/frontier.sql.gz` is a `pg_dump` of the live store taken 2026-11-08, including evidence A1 ingested
during runs (not reproducible from the caches). Needs `git lfs pull` after cloning.

```bash
make db-up
gunzip -c data/dump/frontier.sql.gz | docker exec -i evidence-store-db psql -U frontier -d frontier
```
