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
