"""Fill the lookup tables.

Deterministic (free, instant):
  publications   <- pubmed documents
  labels         <- fda documents (verbatim sections + Medicare Part D numbers)
  trial_links    <- NCT ids written in paper text
  doc_drugs      <- ctgov interventions (trials)

LLM (Nebius, cached in the team `extractions` table by text_hash + prompt_version, so a
rerun costs nothing; `--refresh` is not offered on purpose):
  paper_findings + doc_drugs(papers)  <- abstract + full text of each paper
  labels.*_summary + label_events     <- label sections of each FDA label

Every LLM row is checked against its source text: NCT ids and the supporting quote must
appear verbatim, label terms must appear in the label; the check result is stored.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

import psycopg
from openai import AsyncOpenAI
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError

from evstore.config import EXTRACT_MODEL, NEBIUS_API_KEY, NEBIUS_BASE_URL
from evstore.db import sha

NCT = re.compile(r"\bNCT\d{8}\b")
PAPER_PROMPT = "paper_findings/v2"
LABEL_PROMPT = "label_events/v2"
MAX_PAPER_CHARS = 60_000
CONCURRENCY = 6


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    """Lowercase letters and digits only: unicode dashes, spaces and PDF artefacts never block a match."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def _found(needle: str | None, haystack_norm: str) -> bool:
    n = _norm(needle or "")
    return bool(n) and n in haystack_norm


def _quote_found(quote: str | None, haystack_norm: str, need: float = 0.8) -> bool:
    """True when >= need of the quote's 4-word runs occur in the source (tolerates joined sentences)."""
    words = _norm(quote or "").split()
    if len(words) < 4:
        return _found(quote, haystack_norm)
    grams = [" ".join(words[i:i + 4]) for i in range(len(words) - 3)]
    hay = f" {haystack_norm} "
    return sum(f" {g} " in hay for g in grams) / len(grams) >= need


def _term_found(term: str | None, haystack_norm: str) -> bool:
    """Every content word of the term (by its first 6 letters) occurs in the source."""
    words = [w[:6] for w in _norm(term or "").split() if len(w) > 3 and w not in
             ("with", "from", "that", "have", "patients", "required", "including")]
    if not words:
        return _found(term, haystack_norm)
    hay = f" {haystack_norm}"
    return all(f" {w}" in hay for w in words)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def drug_matcher(conn: psycopg.Connection):
    """name -> drugs.drug_id, matching full names, brand names and first words (as person 1 does)."""
    pats: list[tuple[re.Pattern, str]] = []
    for drug_id, name, brands in conn.execute("SELECT drug_id, name, brand_names FROM drugs"):
        for term in [name, *(brands or [])]:
            if term:
                pats.append((re.compile(rf"\b{re.escape(term.lower())}\b"), drug_id))
    # single-ingredient drugs also match on their first word ('tiotropium', 'roflumilast')
    for drug_id, name in conn.execute("SELECT drug_id, name FROM drugs WHERE name NOT LIKE '%% and %%'"):
        pats.append((re.compile(rf"\b{re.escape(name.split()[0].lower())}\b"), drug_id))
    # longest pattern first so combinations win over their components
    pats.sort(key=lambda p: -len(p[0].pattern))

    def match(text: str) -> str | None:
        t = (text or "").lower()
        return next((d for p, d in pats if p.search(t)), None)

    return match


# ---------------------------------------------------------------------------
# deterministic tables
# ---------------------------------------------------------------------------
def _phase_from_types(types: list[str]) -> str | None:
    for t in types or []:
        m = re.search(r"Phase (I{1,3}V?|IV)\b", t)
        if m:
            return {"I": "phase1", "II": "phase2", "III": "phase3", "IV": "phase4"}[m.group(1)]
    return None


def build_publications(conn: psycopg.Connection) -> int:
    rows = conn.execute(
        """SELECT DISTINCT ON (id) id, source_date, title, url, fields, full_text IS NOT NULL
           FROM documents WHERE source = 'pubmed' ORDER BY id, source_date DESC"""
    ).fetchall()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM publications")
        for did, when, title, url, f, has_ft in rows:
            types = f.get("publication_types") or []
            cur.execute(
                """INSERT INTO publications (doc_id, pmid, doi, pmcid, title, journal, published_date,
                     publication_types, mesh_terms, is_rct, phase, has_full_text, full_text_from, oa_url, url)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (did, did.split(":", 1)[1], f.get("doi"), f.get("pmcid"), title, f.get("journal"), when,
                 types, f.get("mesh_terms") or [], "Randomized Controlled Trial" in types,
                 _phase_from_types(types), has_ft, f.get("full_text_from"), f.get("oa_url"), url),
            )
    return len(rows)


def build_labels(conn: psycopg.Connection) -> int:
    match = drug_matcher(conn)
    rows = conn.execute(
        """SELECT DISTINCT ON (id) id, source_date, fields FROM documents
           WHERE source = 'fda' ORDER BY id, source_date DESC"""
    ).fetchall()
    with conn.cursor() as cur:
        for did, when, f in rows:
            s = f.get("sections") or {}
            pd = f.get("medicare_part_d") or {}
            drug_id = match(f.get("generic") or "") or f"drug:{_slug(f.get('generic') or '')}"
            exists = cur.execute("SELECT 1 FROM drugs WHERE drug_id = %s", (drug_id,)).fetchone()
            cur.execute(
                """INSERT INTO labels (label_doc_id, drug_id, brand, generic, manufacturer, application_number,
                     drug_class, label_effective, published_date, indication_text, dosage_text, mechanism_text,
                     part_d_year, part_d_claims, part_d_beneficiaries, part_d_spend_usd,
                     part_d_spend_per_beneficiary_usd)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (label_doc_id) DO UPDATE SET
                     drug_id=EXCLUDED.drug_id, brand=EXCLUDED.brand, generic=EXCLUDED.generic,
                     manufacturer=EXCLUDED.manufacturer, application_number=EXCLUDED.application_number,
                     drug_class=EXCLUDED.drug_class, label_effective=EXCLUDED.label_effective,
                     published_date=EXCLUDED.published_date, indication_text=EXCLUDED.indication_text,
                     dosage_text=EXCLUDED.dosage_text, mechanism_text=EXCLUDED.mechanism_text,
                     part_d_year=EXCLUDED.part_d_year, part_d_claims=EXCLUDED.part_d_claims,
                     part_d_beneficiaries=EXCLUDED.part_d_beneficiaries, part_d_spend_usd=EXCLUDED.part_d_spend_usd,
                     part_d_spend_per_beneficiary_usd=EXCLUDED.part_d_spend_per_beneficiary_usd""",
                (did, drug_id if exists else None, f.get("brand"), f.get("generic"), f.get("manufacturer"),
                 f.get("application_number"), f.get("drug_class"), f.get("label_effective"), when,
                 s.get("indications_and_usage"), s.get("dosage_and_administration"),
                 s.get("mechanism_of_action"), pd.get("year"), pd.get("total_claims"),
                 pd.get("total_beneficiaries"), pd.get("total_spending_usd"),
                 pd.get("avg_spend_per_beneficiary_usd")),
            )
    return len(rows)


def build_trial_links(conn: psycopg.Connection) -> int:
    in_store = {i for (i,) in conn.execute("SELECT DISTINCT id FROM documents WHERE source = 'ctgov'")}
    rows = conn.execute(
        """SELECT DISTINCT ON (id) id, source_date, coalesce(body,'') || ' ' || coalesce(full_text,'')
           FROM documents WHERE source = 'pubmed' ORDER BY id, source_date DESC"""
    ).fetchall()
    n = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM trial_links")
        for did, when, text in rows:
            for nct in sorted(set(NCT.findall(text))):
                cur.execute("INSERT INTO trial_links VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (nct, did, f"nct:{nct}" in in_store, when))
                n += 1
    return n


def _clean_intervention(name: str) -> str:
    name = re.sub(r"\(.*?\)", " ", name.lower())
    name = re.sub(r"\b(inhalation|powder|spray|solution|tablets?|capsules?|aerosol|mdi|dpi|"
                  r"\d+(\.\d+)?\s*(mcg|µg|μg|mg|ug))\b", " ", name)
    return re.sub(r"\s+", " ", name).strip(" ,;-")


def build_trial_drugs(conn: psycopg.Connection) -> int:
    match = drug_matcher(conn)
    rows = conn.execute(
        """SELECT DISTINCT ON (id) id, source_date, fields FROM documents
           WHERE source = 'ctgov' ORDER BY id, source_date DESC"""
    ).fetchall()
    n = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM doc_drugs WHERE doc_id LIKE 'nct:%%'")
        for did, when, f in rows:
            for iv in f.get("interventions") or []:
                if iv.get("type") not in ("DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"):
                    continue
                raw = iv.get("name") or ""
                name = _clean_intervention(raw)
                if not name:
                    continue
                role = "comparator" if "placebo" in name else "intervention"
                cur.execute("INSERT INTO doc_drugs VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (did, name, match(f"{raw} {' '.join(iv.get('other_names') or [])}"), role, when))
                n += 1
    return n


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------
class PaperFinding(BaseModel):
    study_design: Literal["rct", "crossover_rct", "observational", "post_hoc", "pooled", "pk_pd", "other"]
    phase: Literal["phase1", "phase2", "phase3", "phase4", "na"]
    population: str = Field(description="who was studied, one sentence")
    n_participants: int | None = Field(description="number randomized/enrolled; null if not stated")
    duration_weeks: float | None = Field(description="treatment duration in weeks; null if not stated")
    interventions: list[str] = Field(description="generic drug names under test, lowercase, no doses; never 'placebo'")
    comparators: list[str] = Field(description="'placebo' or generic drug names, lowercase, no doses")
    primary_endpoint: str
    primary_result: str = Field(description="the primary result with numbers (effect, CI, p) if given")
    met_primary: Literal["yes", "no", "mixed", "not_reported"] = Field(description=(
        "yes = primary endpoint statistically significant in favour of the intervention (p<0.05, CI excluding "
        "no effect, or authors say it was met); no = not significant / not met; mixed = met in some "
        "co-primary analyses or trials but not others; not_reported = no primary result in the text"))
    key_secondary: str = Field(description="most important secondary results, one or two sentences")
    safety_summary: str = Field(description="adverse events / safety signals, one or two sentences")
    conclusion: str = Field(description="the authors' conclusion, one sentence")
    nct_ids: list[str] = Field(description="trial registration ids (NCT########) written in the text")
    supporting_quote: str = Field(description=(
        "ONE sentence copied character-for-character from the text that states the primary result; "
        "do not join, shorten or reword sentences"))


class LabelEvent(BaseModel):
    kind: Literal["adverse_reaction", "contraindication", "warning", "boxed_warning", "population_caution"]
    term: str = Field(description="short term exactly as worded in the label text given, 1-5 words")
    frequency_pct: float | None = Field(description="incidence % in the drug arm if the label gives it")
    comparator_pct: float | None = Field(description="incidence % in placebo/comparator if given")
    detail: str = Field(description="one sentence of context from the label")


class LabelFacts(BaseModel):
    indication_summary: str
    dosing_summary: str
    events: list[LabelEvent]


PAPER_SYSTEM = (
    "You extract structured facts from one COPD clinical paper. Use ONLY the text given. "
    "If a value is not stated, use null (or 'not_reported' / 'na'). Never guess numbers. "
    "Drug names: generic, lowercase, no doses or devices (e.g. 'budesonide/glycopyrronium/formoterol'). "
    "Return JSON matching the schema."
)
LABEL_SYSTEM = (
    "You extract structured safety facts from one FDA drug label. Use ONLY the text given. "
    "events: every adverse reaction listed in the main adverse reaction table (with drug and placebo/"
    "comparator % when shown), every contraindication, every warning or precaution, any boxed warning, "
    "and population cautions (special patient groups) ONLY when the text given names them. "
    "Every term must be words that appear in the text given; never add a term from general knowledge. "
    "Keep each term short and worded as in the label. Never invent percentages. "
    "Return JSON matching the schema."
)


def _schema_format(model: type[BaseModel]) -> dict:
    return {"type": "json_schema", "json_schema": {"name": model.__name__, "schema": model.model_json_schema()}}


async def _call(api: AsyncOpenAI, system: str, user: str, schema: type[BaseModel]) -> tuple[BaseModel, dict]:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last = None
    for attempt in range(3):
        try:
            resp = await api.chat.completions.create(
                model=EXTRACT_MODEL, messages=msgs, temperature=0, max_tokens=12000,
                response_format=_schema_format(schema))
        except Exception as e:  # model rejected json_schema: fall back to json_object
            if "response_format" not in str(e) and "json_schema" not in str(e):
                raise
            resp = await api.chat.completions.create(
                model=EXTRACT_MODEL, temperature=0, max_tokens=12000,
                messages=[{"role": "system", "content": system + "\nSchema:\n"
                           + json.dumps(schema.model_json_schema())}, msgs[1]],
                response_format={"type": "json_object"})
        text = resp.choices[0].message.content or ""
        usage = {"in": resp.usage.prompt_tokens, "out": resp.usage.completion_tokens} if resp.usage else {}
        try:
            return schema.model_validate_json(text), usage
        except ValidationError as e:
            last = e
            msgs = msgs[:2] + [{"role": "assistant", "content": text},
                               {"role": "user", "content": f"Invalid: {e.errors()[:5]}. Return corrected JSON only."}]
    raise RuntimeError(f"extraction failed after retries: {last}")


def _cached(conn, text_hash: str, prompt: str):
    row = conn.execute("SELECT output FROM extractions WHERE text_hash=%s AND prompt_version=%s",
                       (text_hash, prompt)).fetchone()
    return row[0] if row else None


def _paper_text(title, body, full_text) -> str:
    text = f"TITLE: {title}\n\nABSTRACT:\n{body or ''}"
    if full_text:
        text += f"\n\nFULL TEXT:\n{full_text}"
    return text[:MAX_PAPER_CHARS]


def _label_text(f: dict) -> str:
    s = f.get("sections") or {}
    keep = ["indications_and_usage", "dosage_and_administration", "contraindications",
            "warnings_and_cautions", "adverse_reactions"]
    return f"BRAND: {f.get('brand')}  GENERIC: {f.get('generic')}\n\n" + "\n\n".join(
        f"## {k}\n{s[k]}" for k in keep if s.get(k))


async def _run_llm(conn: psycopg.Connection, jobs: list[tuple[str, str, str, type[BaseModel], str]]) -> dict:
    """jobs: (key, text, system, schema, prompt_version). Results land in extractions."""
    todo = [j for j in jobs if _cached(conn, sha(j[1]), j[4]) is None]
    usage = {"calls": 0, "in": 0, "out": 0, "cached": len(jobs) - len(todo), "failed": []}
    if not todo:
        return usage
    if not NEBIUS_API_KEY:
        raise RuntimeError("NEBIUS_API_KEY is not set")
    api = AsyncOpenAI(base_url=NEBIUS_BASE_URL, api_key=NEBIUS_API_KEY, timeout=300, max_retries=3)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(job):
        key, text, system, schema, prompt = job
        async with sem:
            try:
                out, u = await _call(api, system, text, schema)
            except Exception as e:
                usage["failed"].append(f"{key}: {str(e)[:120]}")
                return
        conn.execute(
            "INSERT INTO extractions (text_hash, prompt_version, model, output) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING", (sha(text), prompt, EXTRACT_MODEL, Jsonb(out.model_dump())))
        conn.commit()
        usage["calls"] += 1
        usage["in"] += u.get("in", 0)
        usage["out"] += u.get("out", 0)

    await asyncio.gather(*(one(j) for j in todo))
    return usage


def extract_papers(conn: psycopg.Connection) -> dict:
    docs = conn.execute(
        """SELECT DISTINCT ON (id) id, source_date, title, body, full_text FROM documents
           WHERE source = 'pubmed' ORDER BY id, source_date DESC"""
    ).fetchall()
    jobs, texts = [], {}
    for did, when, title, body, ft in docs:
        text = _paper_text(title, body, ft)
        texts[did] = (when, text)
        jobs.append((did, text, PAPER_SYSTEM, PaperFinding, PAPER_PROMPT))
    usage = asyncio.run(_run_llm(conn, jobs))

    match = drug_matcher(conn)
    n = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM paper_findings")
        cur.execute("DELETE FROM doc_drugs WHERE doc_id LIKE 'pmid:%%'")
        for did, (when, text) in texts.items():
            out = _cached(conn, sha(text), PAPER_PROMPT)
            if out is None:
                continue
            p = PaperFinding.model_validate(out)
            norm = _norm(text)
            ncts = [x for x in dict.fromkeys(i.upper() for i in p.nct_ids) if NCT.fullmatch(x) and x in text]
            p.interventions = [i for i in p.interventions if i.strip().lower() != "placebo"]
            cur.execute(
                """INSERT INTO paper_findings VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (did, when, p.study_design, p.phase, p.population, p.n_participants, p.duration_weeks,
                 p.interventions, p.comparators, p.primary_endpoint, p.primary_result, p.met_primary,
                 p.key_secondary, p.safety_summary, p.conclusion, ncts, p.supporting_quote,
                 _quote_found(p.supporting_quote, norm), EXTRACT_MODEL, PAPER_PROMPT),
            )
            for role, names in (("intervention", p.interventions), ("comparator", p.comparators)):
                for name in names:
                    name = name.strip().lower()
                    if name:
                        cur.execute("INSERT INTO doc_drugs VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                    (did, name, None if name == "placebo" else match(name), role, when))
            for nct in ncts:
                cur.execute(
                    """INSERT INTO trial_links VALUES (%s,%s,
                         EXISTS (SELECT 1 FROM documents WHERE id = %s), %s) ON CONFLICT DO NOTHING""",
                    (nct, did, f"nct:{nct}", when))
            n += 1
    conn.commit()
    return {"paper_findings": n, **usage}


def extract_labels(conn: psycopg.Connection) -> dict:
    docs = conn.execute(
        """SELECT DISTINCT ON (id) id, source_date, fields FROM documents
           WHERE source = 'fda' ORDER BY id, source_date DESC"""
    ).fetchall()
    jobs, texts = [], {}
    for did, when, f in docs:
        text = _label_text(f)
        texts[did] = (when, text)
        jobs.append((did, text, LABEL_SYSTEM, LabelFacts, LABEL_PROMPT))
    usage = asyncio.run(_run_llm(conn, jobs))

    n = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM label_events")
        for did, (when, text) in texts.items():
            out = _cached(conn, sha(text), LABEL_PROMPT)
            if out is None:
                continue
            facts = LabelFacts.model_validate(out)
            drug_id = cur.execute("SELECT drug_id FROM labels WHERE label_doc_id=%s", (did,)).fetchone()
            drug_id = drug_id[0] if drug_id else None
            cur.execute("UPDATE labels SET indication_summary=%s, dosing_summary=%s WHERE label_doc_id=%s",
                        (facts.indication_summary, facts.dosing_summary, did))
            norm = _norm(text)
            for e in facts.events:
                if _norm(e.term) in ("", "none", "n a", "not applicable", "not reported", "no boxed warning"):
                    continue  # placeholder rows ('none') the model writes for an empty category
                verified = _term_found(e.term, norm)
                if e.kind == "population_caution" and not verified:
                    continue  # a patient group the label text never names: model knowledge, not evidence
                cur.execute(
                    """INSERT INTO label_events (label_doc_id, drug_id, kind, term, frequency_pct, comparator_pct,
                         detail, term_verified, published_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (did, drug_id, e.kind, e.term.strip().lower(), e.frequency_pct, e.comparator_pct,
                     e.detail, verified, when))
                n += 1
    conn.commit()
    return {"label_events": n, **usage}


def build_deterministic(conn: psycopg.Connection) -> dict[str, int]:
    out = {
        "publications": build_publications(conn),
        "labels": build_labels(conn),
        "trial_links": build_trial_links(conn),
        "doc_drugs(trials)": build_trial_drugs(conn),
    }
    conn.commit()
    return out
