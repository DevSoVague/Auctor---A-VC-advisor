# COPD evidence database: sample schema (v0, generic names)

Sample only. Names are placeholders; rename freely, but keep the **bold** rules.
Stores: **Postgres** (original documents, parent chunks, tabular stats) and **Milvus** (child-chunk vectors for RAG).
Text questions go to RAG. Numbers and stats go to SQL.

## Rules the analysis agents depend on
1. **Every document has `published_date`, never null.** The backtest drops anything dated on or after its cutoff, and an undated doc is dropped. Every chunk and every tabular row carries a date too.
2. **Child chunks are searched, parent text is returned.** Milvus holds children; a hit returns `parent_id`, and Postgres supplies the parent text and the original document.
3. **Ids are stable strings** (`doc:...`, `chunk:...`, `drug:...`). The agents cite them in their answers.
4. Agents get **read-only** access, to a fixed list of tables.

## Postgres

```sql
-- Original documents: papers, trial records, labels, news, patents
CREATE TABLE documents (
  doc_id          TEXT PRIMARY KEY,            -- 'doc:pmid:12345678'
  source          TEXT NOT NULL,               -- pubmed | clinicaltrials | openfda | opentargets | news | patent
  doc_type        TEXT NOT NULL,               -- paper | trial | label | news | patent
  title           TEXT NOT NULL,
  url             TEXT,
  authors         TEXT[],
  published_date  DATE NOT NULL,               -- RULE 1
  full_text       TEXT,                        -- the original document
  metadata        JSONB DEFAULT '{}',          -- journal, pmid, nct_id, doi, ...
  ingested_at     TIMESTAMPTZ DEFAULT now()
);

-- Parent chunks (large, e.g. a section or ~1500 tokens). Children live in Milvus.
CREATE TABLE parent_chunks (
  parent_id       TEXT PRIMARY KEY,            -- 'chunk:pmid:12345678:p3'
  doc_id          TEXT NOT NULL REFERENCES documents(doc_id),
  position        INT  NOT NULL,               -- order within the document
  section         TEXT,                        -- abstract | methods | results | ...
  text            TEXT NOT NULL,
  published_date  DATE NOT NULL                -- copied from documents
);

-- Drugs (one row per drug, any status)
CREATE TABLE drugs (
  drug_id         TEXT PRIMARY KEY,            -- 'drug:tiotropium'
  name            TEXT NOT NULL,
  brand_names     TEXT[],
  drug_class      TEXT,                        -- LAMA | LABA | ICS | PDE4 | biologic ...
  pathway         TEXT,                        -- mechanism / target pathway
  targets         TEXT[],                      -- gene symbols or Open Targets ids
  status          TEXT NOT NULL,               -- in_market | shelved | in_trial
  as_of_date      DATE NOT NULL                -- date this row's facts were true
);

-- In-market stats (a row per drug per period, so history is kept)
CREATE TABLE market_stats (
  id              BIGSERIAL PRIMARY KEY,
  drug_id         TEXT NOT NULL REFERENCES drugs(drug_id),
  period          DATE NOT NULL,               -- start of the period the numbers describe
  patients_est    INT,                         -- how many people use it
  annual_cost_usd NUMERIC,
  insurance       TEXT,                        -- covered | partial | not_covered | unknown
  coverage_notes  TEXT,
  side_effects    TEXT[],
  warnings        TEXT[],                      -- e.g. pregnancy, contraindications
  source_doc_id   TEXT REFERENCES documents(doc_id)
);

-- Shelved or failed programmes
CREATE TABLE shelved_programs (
  id              BIGSERIAL PRIMARY KEY,
  drug_id         TEXT NOT NULL REFERENCES drugs(drug_id),
  shelved_date    DATE NOT NULL,
  phase_reached   TEXT,                        -- phase1 | phase2 | phase3
  reason_category TEXT,                        -- efficacy | safety | commercial | other
  reason_detail   TEXT,
  side_effects    TEXT[],
  source_doc_id   TEXT REFERENCES documents(doc_id)
);

-- Trials (structured fields from ClinicalTrials.gov)
CREATE TABLE trials (
  trial_id        TEXT PRIMARY KEY,            -- NCT number
  drug_id         TEXT REFERENCES drugs(drug_id),
  title           TEXT,
  phase           TEXT,
  status          TEXT,                        -- completed | terminated | recruiting ...
  start_date      DATE,
  completion_date DATE,
  enrollment      INT,
  primary_outcome TEXT,
  why_stopped     TEXT,
  published_date  DATE NOT NULL                -- registration or last-update date
);

CREATE INDEX ON documents (published_date);
CREATE INDEX ON parent_chunks (doc_id);
CREATE INDEX ON market_stats (drug_id, period);
```

## Milvus: collection `copd_child_chunks`

| field | type | note |
|---|---|---|
| `chunk_id` | VARCHAR (primary key) | `chunk:pmid:12345678:p3:c2` |
| `embedding` | FLOAT_VECTOR(dim) | dim depends on the embedding model; use one model for index and query |
| `text` | VARCHAR | the small child text (about 200 to 300 tokens) |
| `parent_id` | VARCHAR | links to `parent_chunks.parent_id` |
| `doc_id` | VARCHAR | links to `documents.doc_id` |
| `source` | VARCHAR | same values as `documents.source` |
| `doc_type` | VARCHAR | same values as `documents.doc_type` |
| `published_date` | INT64 | `YYYYMMDD`, so the date cutoff can be a filter at search time (RULE 1) |

Index: HNSW or IVF_FLAT, metric COSINE. Filter example: `published_date < 20210101`.

## Query flow the agents use
- **RAG search** → Milvus top-k children (date-filtered) → fetch the `parent_chunks` text by `parent_id` → return it with `doc_id`, `published_date`, `source`.
- **Get document** → `documents` by `doc_id`.
- **Stats** → `drugs`, `market_stats`, `shelved_programs`, `trials` through fixed, parameterised queries.

## Starter data (for tomorrow's demo)
About 50 COPD drug-trial papers, 6 to 10 shelved drugs and 6 to 10 in-market drugs. Decide the date cutoff first (open question: the papers are 2023 to 2025, and the backtest cutoff must sit after the newest one).

---

# Everything the database needs (checklist)

## A. Containers (docker compose)
| service | why |
|---|---|
| `postgres` | tables, document text, parent chunks, ingestion log |
| `milvus` (standalone) | child-chunk vectors for RAG. It also needs `etcd` and an object store (`minio`) of its own |
| `minio` (or a mounted `/data/files` folder) | the **original files** (PDF, XML, HTML). Postgres keeps only the path |
| `api` (optional) | a thin read-only service that the agents call, instead of connecting to Postgres and Milvus directly |
| `ingest` job | scrapes, chunks, embeds, and writes to all of the above |

## B. What is stored, and where
| data | store | used by |
|---|---|---|
| Original files (PDF, XML, HTML) | minio / file folder; `documents.file_path` points to them | citations, re-chunking, showing the source |
| Extracted full text, one per document | Postgres `documents.full_text` | `get_document` |
| Parent chunks (large sections) | Postgres `parent_chunks` | context returned after a RAG hit |
| Child chunks and vectors | Milvus `copd_child_chunks` | `rag_search` |
| Drug facts: class, pathway, targets, status | Postgres `drugs` | stats questions, mechanism cards |
| Market stats: cost, usage, insurance, side effects, warnings | Postgres `market_stats` | stats questions |
| Shelved programmes: reason, phase, side effects | Postgres `shelved_programs` | A6 red team ("tried before, failed") |
| Trials: phase, status, why stopped, enrollment | Postgres `trials` | trial history |
| Disease to target evidence (Open Targets) | Postgres `targets` (below) | A2 mechanism cards |
| Approvals and retractions (OpenFDA, retraction lists) | Postgres `approvals`, `retractions` (below) | A3, A6 checks |
| Ingestion log: what was loaded, when, from where | Postgres `ingest_log` (below) | debugging, no duplicates |
| New finds pushed by A1 | same tables, flagged `origin = 'agent'` | the novelty check, so it isn't found twice |

## C. Extra tables to add
```sql
-- original file location (add to documents)
ALTER TABLE documents ADD COLUMN file_path TEXT;     -- 's3://files/pubmed/12345678.pdf'
ALTER TABLE documents ADD COLUMN content_hash TEXT;  -- skip duplicates on re-ingest
ALTER TABLE documents ADD COLUMN origin TEXT DEFAULT 'seed';  -- seed | scraper | agent

CREATE TABLE targets (            -- disease to target associations
  target_id TEXT PRIMARY KEY,     -- gene symbol or Ensembl id
  symbol TEXT, disease_id TEXT,
  association_score NUMERIC,      -- 0..1
  evidence_types TEXT[],          -- genetic | literature | drug ...
  as_of_date DATE NOT NULL
);

CREATE TABLE approvals (          -- regulatory status
  id BIGSERIAL PRIMARY KEY,
  drug_id TEXT REFERENCES drugs(drug_id),
  agency TEXT, approved_date DATE NOT NULL, indication TEXT, label_doc_id TEXT
);

CREATE TABLE retractions (        -- never cite these
  doc_id TEXT PRIMARY KEY REFERENCES documents(doc_id),
  retracted_date DATE NOT NULL, reason TEXT
);

CREATE TABLE ingest_log (
  id BIGSERIAL PRIMARY KEY,
  source TEXT, query TEXT, fetched INT, stored INT, skipped INT,
  started_at TIMESTAMPTZ DEFAULT now(), error TEXT
);
```

## D. Needed for the demo
- Chunking is parent-child. Children go to Milvus, parents to Postgres, and the original file is kept.
- Every row and chunk has a date.
- Search can filter by date (`published_date < cutoff`).
- Retracted documents are excluded from search results.
- One embedding model for both indexing and querying.
- A read-only database user for the agents.
- Seed data: about 50 papers, 6 to 10 shelved drugs, 6 to 10 in-market drugs.
