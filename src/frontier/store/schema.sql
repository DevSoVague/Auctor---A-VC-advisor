-- Safe to rerun: every statement is IF NOT EXISTS, so `make schema` never drops or
-- changes an existing table. To rebuild the evidence tables, use `make reset-db`.
-- After the Saturday-night load, changes go in store/migrations/ (see its README).
CREATE EXTENSION IF NOT EXISTS vector;

-- evidence (person 1)
-- documents is versioned: one row per version of a record. source_date is the date
-- that version became public. Retrieval returns, per id, the latest version dated
-- before as_of. ids follow the prefix list in agents/schemas.py and stay the same
-- across versions and releases.
CREATE TABLE IF NOT EXISTS documents (
  id text NOT NULL,                  -- 'pmid:12345678', 'nct:NCT01234567', 'fda:NDA021395', 'sec:<accession>', 'pat:US1234567B2', 'ret:<DOI>', 'ot:<disease>:<ENSG>', 'chembl:CHEMBL1234', 'news:<16-hex headline hash>'
  source text NOT NULL,              -- pubmed | ctgov | fda | opentargets | chembl | edgar | retraction | patent | news
  source_date date NOT NULL,         -- the date the as-of filter uses
  version_label text NOT NULL,       -- e.g. aact-2020-12, ctgov-live, ot-20.11, ot-24.06, chembl-27
  title text, body text, url text,   -- body comes from this version, never the current record
  fields jsonb NOT NULL DEFAULT '{}', -- source-specific extras retrieval filters on (Record.fields)
  text_hash text NOT NULL,
  embedding vector(4096),            -- Qwen3-Embedding-8B; no ANN index (see note)
  PRIMARY KEY (id, source_date)
);
CREATE TABLE IF NOT EXISTS entities (
  id text PRIMARY KEY,               -- 'hgnc:8593', 'efo:EFO_0000341', 'mondo:...', 'chembl:CHEMBL1234', 'nct:...', 'cik:0000123456'
  kind text NOT NULL,                -- gene | disease | drug | trial | patent | company
  name text NOT NULL, synonyms text[]
);
-- doc_id has no foreign key: documents.id is no longer unique once records are versioned.
CREATE TABLE IF NOT EXISTS doc_entities (doc_id text NOT NULL, entity_id text REFERENCES entities,
  PRIMARY KEY (doc_id, entity_id));
CREATE TABLE IF NOT EXISTS extractions (  -- extract once, never again (paid: never reset)
  text_hash text, prompt_version text, model text, output jsonb, created_at timestamptz DEFAULT now(),
  PRIMARY KEY (text_hash, prompt_version));
CREATE TABLE IF NOT EXISTS programs (     -- one row per asset per disease, as of a date (agent 5)
  id serial PRIMARY KEY, asset_entity text, owner_entity text, owner_type text,
  mechanism text, modality text, stage text, status text, valid_from date, valid_to date,
  lead_trial text, source_ids text[]);

-- runs (person 2 and person 5)
CREATE TABLE IF NOT EXISTS runs (id uuid PRIMARY KEY, config jsonb, status text, created_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS agent_outputs (run_id uuid, agent text, hypothesis_id text, version int,
  output jsonb, status text,         -- draft | awaiting_review | approved | superseded
  PRIMARY KEY (run_id, agent, hypothesis_id, version));
CREATE TABLE IF NOT EXISTS claims (id text PRIMARY KEY, run_id uuid, agent text, hypothesis_id text,
  text text, kind text, confidence real, source_ids text[],
  ledger_status text);               -- unchecked | supported | partly | unsupported | removed
CREATE TABLE IF NOT EXISTS decisions (id serial PRIMARY KEY, run_id uuid, at timestamptz DEFAULT now(),
  actor text,                        -- reviewer | red_teamer | orchestrator
  action text, target text, reason text);  -- approve | edit | rerun | revise | kill | override
-- paid: never reset
CREATE TABLE IF NOT EXISTS llm_calls (id serial PRIMARY KEY, run_id uuid, agent text, model text,
  input_tokens int, cached_tokens int, output_tokens int, list_price_usd numeric,
  latency_ms int, cache_hit boolean, at timestamptz DEFAULT now());
-- model response cache (person 2's router); paid: never reset
CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key text PRIMARY KEY,        -- sha256 of model, messages, schema, temperature
  model text NOT NULL,
  response text NOT NULL,            -- raw JSON text the model returned
  input_tokens int, output_tokens int,
  created_at timestamptz DEFAULT now());

-- documents: extra columns from the team's DATABASE_SCHEMA.md (v0). Safe to rerun.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_path text;           -- original file, if kept
ALTER TABLE documents ADD COLUMN IF NOT EXISTS origin text DEFAULT 'seed'; -- seed | scraper | agent

-- Structured tables (person 1), built from documents by `python -m frontier.ingest.structured`.
-- Every row carries a date the as-of filter uses (as_of_date or published_date): a backtest
-- must drop rows dated on or after its cutoff, exactly as for documents.
-- source_ids hold documents.id values (pmid:..., nct:..., fda:..., curated:...), not a foreign
-- key, since documents.id repeats across versions.
CREATE TABLE IF NOT EXISTS drugs (
  drug_id         text PRIMARY KEY,          -- 'drug:tiotropium'
  name            text NOT NULL,
  brand_names     text[],
  drug_class      text,                      -- LAMA | LABA | ICS | PDE4 | biologic ...
  pathway         text,                      -- mechanism / target pathway
  targets         text[],                    -- gene symbols or Open Targets ids (not filled yet)
  status          text NOT NULL,             -- in_market | shelved | in_trial (for COPD)
  as_of_date      date NOT NULL,             -- date this row's facts were public
  source_ids      text[]
);
CREATE TABLE IF NOT EXISTS market_stats (
  drug_id         text NOT NULL REFERENCES drugs(drug_id),
  period          date NOT NULL,             -- start of the period the numbers describe
  patients_est    int,                       -- Medicare Part D beneficiaries in the period
  annual_cost_usd numeric,                   -- Part D spend per beneficiary in the period
  total_spend_usd numeric,
  total_claims    int,
  insurance       text,                      -- covered | partial | not_covered | unknown
  coverage_notes  text,
  usage           text,                      -- label: indications and dosage
  side_effects    text[],                    -- label: adverse reactions
  warnings        text[],                    -- label: contraindications, warnings and precautions
  published_date  date NOT NULL,             -- when these numbers and label text were public
  source_ids      text[],
  PRIMARY KEY (drug_id, period)
);
CREATE TABLE IF NOT EXISTS shelved_programs (
  drug_id         text PRIMARY KEY REFERENCES drugs(drug_id),
  shelved_date    date NOT NULL,             -- first public report of the failure
  phase_reached   text,                      -- phase1 | phase2 | phase3
  reason_category text,                      -- efficacy | safety | commercial | other
  reason_detail   text,
  side_effects    text[],
  published_date  date NOT NULL,             -- latest source used in the row
  source_ids      text[]
);
CREATE TABLE IF NOT EXISTS trials (
  trial_id        text NOT NULL,             -- 'NCT01234567'
  published_date  date NOT NULL,             -- registration or last-update date of this version
  version_label   text NOT NULL,             -- ctgov-registration | ctgov-live
  drug_id         text REFERENCES drugs(drug_id),
  title           text,
  phase           text,
  status          text,                      -- null in a registration-only version
  start_date      date,
  completion_date date,                      -- primary completion
  enrollment      int,
  primary_outcome text,
  why_stopped     text,
  sponsor         text,
  PRIMARY KEY (trial_id, published_date)
);
CREATE TABLE IF NOT EXISTS targets (         -- disease to target associations (not filled yet)
  target_id text NOT NULL, symbol text, disease_id text NOT NULL,
  association_score numeric, evidence_types text[], as_of_date date NOT NULL,
  PRIMARY KEY (target_id, disease_id, as_of_date)
);
CREATE TABLE IF NOT EXISTS approvals (       -- regulatory status (not filled yet)
  id serial PRIMARY KEY, drug_id text REFERENCES drugs(drug_id),
  agency text, approved_date date NOT NULL, indication text, label_doc_id text
);
CREATE TABLE IF NOT EXISTS retractions (     -- never cite these (not filled yet)
  doc_id text PRIMARY KEY, retracted_date date NOT NULL, reason text
);
CREATE TABLE IF NOT EXISTS ingest_log (
  id serial PRIMARY KEY, source text, query text, fetched int, stored int, skipped int,
  started_at timestamptz DEFAULT now(), error text
);
CREATE INDEX IF NOT EXISTS documents_source_date ON documents (source_date);
CREATE INDEX IF NOT EXISTS trials_drug ON trials (drug_id);
