CREATE EXTENSION IF NOT EXISTS vector;

-- evidence (person 1)
CREATE TABLE documents (
  id text PRIMARY KEY,               -- 'pmid:123', 'nct:NCT0...', 'fda:...', 'sec:...', 'pat:...'
  source text NOT NULL,              -- pubmed | ctgov | fda | opentargets | chembl | edgar | retraction | patent
  source_date date NOT NULL,         -- the date the as-of filter uses
  title text, body text, url text,
  text_hash text NOT NULL,
  embedding vector(4096)             -- Qwen3-Embedding-8B; no ANN index (see note)
);
CREATE TABLE entities (
  id text PRIMARY KEY,               -- 'hgnc:...', 'mondo:...', 'chembl:...', 'nct:...', 'cik:...'
  kind text NOT NULL,                -- gene | disease | drug | trial | patent | company
  name text NOT NULL, synonyms text[]
);
CREATE TABLE doc_entities (doc_id text REFERENCES documents, entity_id text REFERENCES entities,
  PRIMARY KEY (doc_id, entity_id));
CREATE TABLE extractions (           -- extract once, never again
  text_hash text, prompt_version text, model text, output jsonb, created_at timestamptz DEFAULT now(),
  PRIMARY KEY (text_hash, prompt_version));
CREATE TABLE programs (              -- one row per asset per disease, as of a date (agent 5)
  id serial PRIMARY KEY, asset_entity text, owner_entity text, owner_type text,
  mechanism text, modality text, stage text, status text, valid_from date, valid_to date,
  lead_trial text, source_ids text[]);

-- runs (person 2 and person 5)
CREATE TABLE runs (id uuid PRIMARY KEY, config jsonb, status text, created_at timestamptz DEFAULT now());
CREATE TABLE agent_outputs (run_id uuid, agent text, hypothesis_id text, version int,
  output jsonb, status text,         -- draft | awaiting_review | approved | superseded
  PRIMARY KEY (run_id, agent, hypothesis_id, version));
CREATE TABLE claims (id text PRIMARY KEY, run_id uuid, agent text, hypothesis_id text,
  text text, kind text, confidence real, source_ids text[],
  ledger_status text);               -- unchecked | supported | partly | unsupported | removed
CREATE TABLE decisions (id serial PRIMARY KEY, run_id uuid, at timestamptz DEFAULT now(),
  actor text,                        -- reviewer | red_teamer | orchestrator
  action text, target text, reason text);  -- approve | edit | rerun | revise | kill | override
CREATE TABLE llm_calls (id serial PRIMARY KEY, run_id uuid, agent text, model text,
  input_tokens int, cached_tokens int, output_tokens int, list_price_usd numeric,
  latency_ms int, cache_hit boolean, at timestamptz DEFAULT now());
