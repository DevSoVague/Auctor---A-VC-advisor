Frontier Hypothesis Engine: COPD evidence store export (person 1).
All data dated 2023-01-01 to 2025-12-31.

retrieval/records.json: documents rows in the retrieval Record shape.
  Use with StubRetrievalClient: FRONTIER_FIXTURES_DIR=<this folder>.
  Papers with full text (fields.full_text): 33.
  Free-to-read PDF text is for the team's own analysis, not for republishing.
retrieval/records-other-sources.json: curated rows (shelved drugs, market context);
  'curated' is not yet a retrieval Source, so the stub does not load them.
tables/: structured tables and parent_chunks (full-text sections).

Rows by source: ctgov 48, curated 8, fda 6, pubmed 50
records.json: 104 rows
records-other-sources.json: 8 rows
tables/drugs.json: 12 rows
tables/market_stats.json: 6 rows
tables/shelved_programs.json: 6 rows
tables/trials.json: 48 rows
tables/parent_chunks.json: 259 rows
