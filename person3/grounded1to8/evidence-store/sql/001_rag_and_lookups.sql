-- 001: RAG chunks + extracted lookup tables + as-of helpers.
-- Additive only (team migrations rule): no existing table or column is changed.
-- Every new row carries a date, and every helper below takes the cutoff `d` and
-- returns only what was public strictly before it.

-- ---------------------------------------------------------------------------
-- RAG: child chunks with embeddings
-- ---------------------------------------------------------------------------
-- One row per passage. doc_id is the unchanged documents.id ('pmid:...', 'nct:...'),
-- so citations keep using document ids; chunk_id is only for retrieval bookkeeping.
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id        text PRIMARY KEY,          -- 'chunk:pmid:36725331:p5:c0', 'chunk:nct:NCT05838703@2023-05-01:b0'
  doc_id          text NOT NULL,             -- documents.id
  doc_version     date NOT NULL,             -- documents.source_date of the version cut from
  parent_id       text,                      -- parent_chunks.parent_id when cut from full text
  source          text NOT NULL,             -- pubmed | ctgov | fda | curated
  section         text,                      -- Abstract | Methods | Results | adverse_reactions | Registry record ...
  position        int  NOT NULL,             -- order within the document
  text            text NOT NULL,             -- the passage shown to agents
  published_date  date NOT NULL,             -- as-of filter
  embed_hash      text NOT NULL,             -- sha256 of the exact string that was embedded
  embedding       vector(4096),              -- Qwen/Qwen3-Embedding-8B (Nebius). No ANN index:
                                             -- pgvector indexes stop at 2000 dims, and ~2k rows scan in ms.
  tsv             tsvector GENERATED ALWAYS AS
                    (to_tsvector('english', coalesce(section, '') || ' ' || text)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks (doc_id, doc_version);
CREATE INDEX IF NOT EXISTS chunks_published ON chunks (published_date);
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin (tsv);

-- Paid: never reset. Re-chunking reuses vectors for any passage already embedded.
CREATE TABLE IF NOT EXISTS embedding_cache (
  embed_hash  text NOT NULL,
  model       text NOT NULL,
  embedding   vector(4096) NOT NULL,
  created_at  timestamptz DEFAULT now(),
  PRIMARY KEY (embed_hash, model)
);

-- ---------------------------------------------------------------------------
-- Lookup tables (tabular answers without RAG)
-- ---------------------------------------------------------------------------
-- Papers: bibliographic facts, deterministic from the PubMed record.
CREATE TABLE IF NOT EXISTS publications (
  doc_id            text PRIMARY KEY,        -- 'pmid:...'
  pmid              text NOT NULL,
  doi               text, pmcid text,
  title             text,
  journal           text,
  published_date    date NOT NULL,
  publication_types text[],
  mesh_terms        text[],
  is_rct            boolean,                 -- 'Randomized Controlled Trial' in publication types
  phase             text,                    -- from publication types: phase1..phase4
  has_full_text     boolean NOT NULL,
  full_text_from    text,                    -- pmc | europepmc | unpaywall-pdf ...
  oa_url            text,
  url               text
);

-- Papers: what the study found. LLM-extracted (cached in extractions), one row per paper.
-- quote_verified = the model's supporting quote was found verbatim in the source text.
CREATE TABLE IF NOT EXISTS paper_findings (
  doc_id            text PRIMARY KEY,
  published_date    date NOT NULL,
  study_design      text,                    -- rct | crossover_rct | observational | post_hoc | pooled | pk_pd | other
  phase             text,                    -- phase1 | phase2 | phase3 | phase4 | na
  population        text,
  n_participants    int,
  duration_weeks    numeric,
  interventions     text[],                  -- generic names, lowercase
  comparators       text[],                  -- 'placebo' or generic names
  primary_endpoint  text,
  primary_result    text,
  met_primary       text,                    -- yes | no | mixed | not_reported
  key_secondary     text,
  safety_summary    text,
  conclusion        text,
  nct_ids           text[],                  -- only ids found verbatim in the text
  supporting_quote  text,
  quote_verified    boolean,
  model             text,
  prompt_version    text
);

-- Which drugs each document is about (papers: from paper_findings; trials: from ctgov interventions).
-- drug_id is set when the drug is in the curated drugs table; drug_name is always set.
CREATE TABLE IF NOT EXISTS doc_drugs (
  doc_id          text NOT NULL,
  drug_name       text NOT NULL,             -- normalized lowercase
  drug_id         text,                      -- drugs.drug_id or NULL
  role            text NOT NULL,             -- intervention | comparator
  published_date  date NOT NULL,
  PRIMARY KEY (doc_id, drug_name, role)
);
CREATE INDEX IF NOT EXISTS doc_drugs_name ON doc_drugs (drug_name);
CREATE INDEX IF NOT EXISTS doc_drugs_drug ON doc_drugs (drug_id);

-- Paper <-> trial registry links (NCT ids written in the paper).
CREATE TABLE IF NOT EXISTS trial_links (
  trial_id        text NOT NULL,             -- 'NCT01234567'
  doc_id          text NOT NULL,             -- 'pmid:...'
  in_store        boolean NOT NULL,          -- the trial is in documents
  published_date  date NOT NULL,             -- the paper's date
  PRIMARY KEY (trial_id, doc_id)
);

-- FDA labels: one row per label version, deterministic fields + LLM summaries.
CREATE TABLE IF NOT EXISTS labels (
  label_doc_id       text PRIMARY KEY,       -- 'fda:NDA021395'
  drug_id            text,
  brand              text,
  generic            text,
  manufacturer       text,
  application_number text,
  drug_class         text,
  label_effective    date,
  published_date     date NOT NULL,
  indication_text    text,                   -- label section 1, verbatim
  dosage_text        text,                   -- label section 2, verbatim
  mechanism_text     text,                   -- label 12.1, verbatim
  indication_summary text,                   -- LLM, one or two sentences
  dosing_summary     text,                   -- LLM
  part_d_year        int,
  part_d_claims      numeric,
  part_d_beneficiaries numeric,
  part_d_spend_usd   numeric,
  part_d_spend_per_beneficiary_usd numeric
);

-- FDA labels: side effects, contraindications, warnings, population cautions. LLM-extracted.
CREATE TABLE IF NOT EXISTS label_events (
  id              serial PRIMARY KEY,
  label_doc_id    text NOT NULL,
  drug_id         text,
  kind            text NOT NULL,             -- adverse_reaction | contraindication | warning | boxed_warning | population_caution
  term            text NOT NULL,             -- 'pharyngitis', 'paradoxical bronchospasm', 'pregnancy'
  frequency_pct   numeric,                   -- drug arm incidence, when the label gives one
  comparator_pct  numeric,                   -- placebo/comparator incidence, when given
  detail          text,
  term_verified   boolean,                   -- term found in the label text
  published_date  date NOT NULL
);
CREATE INDEX IF NOT EXISTS label_events_drug ON label_events (drug_id, kind);

-- ---------------------------------------------------------------------------
-- As-of helpers: use these instead of the raw tables in any backtest query.
-- ---------------------------------------------------------------------------
-- Latest version of every document public before d.
CREATE OR REPLACE FUNCTION documents_as_of(d date) RETURNS SETOF documents
LANGUAGE sql STABLE AS $$
  SELECT DISTINCT ON (id) * FROM documents WHERE source_date < d ORDER BY id, source_date DESC
$$;

-- Latest version of every trial public before d.
CREATE OR REPLACE FUNCTION trials_as_of(d date) RETURNS SETOF trials
LANGUAGE sql STABLE AS $$
  SELECT DISTINCT ON (trial_id) * FROM trials WHERE published_date < d ORDER BY trial_id, published_date DESC
$$;

-- Chunks of the visible version of each document.
CREATE OR REPLACE FUNCTION chunks_as_of(d date) RETURNS SETOF chunks
LANGUAGE sql STABLE AS $$
  SELECT c.* FROM chunks c
  JOIN (SELECT id, source_date FROM documents_as_of(d)) v ON v.id = c.doc_id AND v.source_date = c.doc_version
  WHERE c.published_date < d
$$;

-- Full-text helpers. Postgres parses 'IL-33' as il + '-33' but 'Anti-IL-33' as il + '33',
-- so a hyphenated query term matches either spelling.
CREATE OR REPLACE FUNCTION term_query(t text) RETURNS tsquery
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN t LIKE '%-%'
              THEN phraseto_tsquery('english', t) || phraseto_tsquery('english', replace(t, '-', ' '))
              ELSE plainto_tsquery('english', t) END
$$;
-- OR of several terms.
CREATE OR REPLACE FUNCTION any_terms(ts text[]) RETURNS tsquery
LANGUAGE sql IMMUTABLE AS $$
  SELECT coalesce(string_agg('(' || term_query(t)::text || ')', ' | '), '')::tsquery
  FROM unnest(ts) t WHERE term_query(t)::text <> ''
$$;
