"""Generate analysis-agent fixtures: a small COPD retrieval corpus + stub LLM outputs.

All ids are prefixed `fx:` -- these are FIXTURE records for stub tests, not real PMIDs/NCTs.
Content is paraphrased from well-known public COPD history; dates are approximate.
Run:  python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def doc(id, source, date, title, body, tags=(), doi=None):
    d = {"id": id, "source": source, "source_date": date, "title": title, "body": body,
         "tags": list(tags), "url": None}
    if doi:
        d["doi"] = doi
    return d


DOCS = [
    # ---- approvals (fda)
    doc("fx:fda-roflumilast-2011", "fda", "2011-02-28", "Approval: roflumilast for severe COPD",
        "Oral PDE4 inhibitor (PDE4B/PDE4D) approved to reduce COPD exacerbations in severe COPD "
        "with chronic bronchitis.", ["approval", "PDE4"]),
    doc("fx:fda-trelegy-2017", "fda", "2017-09-18", "Approval: single-inhaler triple therapy (FF/UMEC/VI) for COPD",
        "Once-daily inhaled ICS/LAMA/LABA triple therapy approved for COPD maintenance.", ["approval", "soc"]),
    doc("fx:fda-breztri-2020", "fda", "2020-07-23", "Approval: budesonide/glycopyrrolate/formoterol for COPD",
        "Twice-daily inhaled triple therapy approved for COPD maintenance.", ["approval", "soc"]),
    doc("fx:fda-ensifentrine-2024", "fda", "2024-06-26", "Approval: ensifentrine for COPD maintenance",
        "Nebulised dual PDE3/PDE4 inhibitor (PDE3A/PDE4B) approved for COPD maintenance treatment.",
        ["approval", "PDE3", "PDE4"]),
    doc("fx:fda-dupilumab-copd-2024", "fda", "2024-09-27", "Approval: dupilumab add-on for COPD with eosinophilic phenotype",
        "IL-4R alpha antibody (IL4R) approved as add-on maintenance for COPD with raised blood eosinophils.",
        ["approval", "IL4R", "type2"]),
    # ---- literature (pubmed)
    doc("fx:pm-gold2019", "pubmed", "2018-11-15", "GOLD 2019: blood eosinophils guide ICS in COPD",
        "Human COPD guideline: blood eosinophil count >=300 cells/uL identifies patients most likely to benefit "
        "from ICS; <100 unlikely. First formal biomarker-defined segment in COPD treatment.",
        ["human", "guideline", "segment", "eosinophil"]),
    doc("fx:pm-impact-2018", "pubmed", "2018-04-18", "IMPACT: triple therapy vs dual therapy in COPD",
        "In 10,355 patients with COPD and exacerbation history, single-inhaler triple therapy reduced "
        "moderate/severe exacerbations vs dual therapy; larger benefit with higher eosinophils.",
        ["human", "soc", "eosinophil"]),
    doc("fx:pm-ethos-2020", "pubmed", "2020-06-24", "ETHOS: triple therapy reduces exacerbations, mortality signal",
        "Triple therapy reduced moderate/severe exacerbations and showed lower all-cause mortality vs dual "
        "therapy. Raises the bar for any add-on therapy, which must now be tested on top of triple therapy.",
        ["human", "soc", "negative_bar"]),
    doc("fx:pm-endpoint-exac-2019", "pubmed", "2019-03-10", "Exacerbation rate as primary endpoint for COPD biologics",
        "Review: annualised moderate/severe exacerbation rate is the accepted primary endpoint for COPD "
        "add-on biologics; eosinophil enrichment reduces required sample size; SGRQ and pre-BD FEV1 as key secondaries.",
        ["endpoint", "trial_tool", "eosinophil"]),
    doc("fx:pm-ct-imaging-2018", "pubmed", "2018-08-02", "Quantitative CT as a trial enrichment tool in COPD",
        "Human cohort (COPDGene): quantitative CT airway and emphysema measures identify subtypes and "
        "predict progression; proposed for enrichment.", ["human", "trial_tool"]),
    doc("fx:pm-type2-copd-2019", "pubmed", "2019-06-12", "Type 2 inflammation in a subset of COPD",
        "Human sputum and blood studies: 20-40% of COPD patients show type 2 inflammation (IL4R/IL13 pathway, "
        "raised FeNO and eosinophils), associated with exacerbation risk.",
        ["human", "segment", "type2", "IL4R", "IL13"]),
    doc("fx:pm-dupi-asthma-2018", "pubmed", "2018-05-21", "Dupilumab in moderate-to-severe asthma",
        "IL4R antibody reduced severe exacerbations in asthma, greater with eos>=300 and high FeNO. "
        "Mechanistic precedent in type 2 airway disease.", ["human", "IL4R", "type2"]),
    doc("fx:pm-metrex-2017", "pubmed", "2017-09-11", "Mepolizumab in eosinophilic COPD (METREX/METREO)",
        "Anti-IL5 antibody: modest, inconsistent exacerbation reduction across two Phase 3 trials in "
        "eosinophilic COPD.", ["human", "failure", "IL5"]),
    doc("fx:pm-crl-mepo-2018", "pubmed", "2018-09-07", "FDA declines mepolizumab COPD approval",
        "Complete response letter for anti-IL5 (IL5) in COPD citing insufficient efficacy evidence.",
        ["regulatory", "negative_bar", "failure", "IL5"]),
    doc("fx:pm-galathea-2019", "pubmed", "2019-05-20", "Benralizumab in COPD (GALATHEA/TERRANOVA)",
        "Anti-IL5RA antibody did not significantly reduce exacerbations in COPD with eosinophils >=220 "
        "despite eosinophil depletion. Suggests eosinophils are a marker, not the driver, in COPD.",
        ["human", "failure", "IL5RA", "IL5", "contradiction"]),
    doc("fx:pm-il33-genetics-2019", "pubmed", "2019-01-14", "IL33 and IL1RL1 loci in airway disease genetics",
        "Human genetics: variants at IL33 and IL1RL1 (ST2) associated with asthma and lung function; "
        "IL33 loss-of-function variant protective in asthma.", ["human", "genetics", "IL33", "IL1RL1"]),
    doc("fx:pm-il33-smoke-2020", "pubmed", "2020-03-03", "IL-33 release and ST2 shift in smokers' airways",
        "Human lung tissue: cigarette smoke drives epithelial IL33 release and shifts IL1RL1 (ST2) expression "
        "toward NK and macrophage cells, a type-2-independent pathway.", ["human", "IL33", "IL1RL1", "mechanism"]),
    doc("fx:pm-itepekimab-ph2-2021", "pubmed", "2021-08-01", "Itepekimab Phase 2 in COPD",
        "Anti-IL33 antibody missed primary endpoint overall; exacerbations reduced in former smokers (subgroup).",
        ["human", "IL33", "unreplicated"]),
    doc("fx:pm-tslp-asthma-2017", "pubmed", "2017-09-07", "Tezepelumab in uncontrolled asthma (PATHWAY)",
        "Anti-TSLP antibody reduced asthma exacerbations regardless of eosinophil count.", ["human", "TSLP"]),
    doc("fx:pm-tslp-mouse-2019", "pubmed", "2019-11-20", "TSLP in a murine smoke-exposure model",
        "Single-lab mouse study: TSLP blockade reduced smoke-induced airway inflammation. Not replicated.",
        ["preclinical", "TSLP", "single_lab", "unreplicated"]),
    doc("fx:pm-pde3-4-ph2-2018", "pubmed", "2018-10-01", "Ensifentrine Phase 2b on top of tiotropium",
        "Human trial: nebulised dual PDE3A/PDE4B inhibitor improved FEV1 added to LAMA; bronchodilator and "
        "anti-inflammatory; GI effects low vs oral PDE4.", ["human", "PDE3", "PDE4"]),
    doc("fx:pm-roflu-safety-2016", "pubmed", "2016-05-10", "Roflumilast tolerability",
        "Oral PDE4 (PDE4B/PDE4D) inhibition: diarrhoea, nausea, weight loss; high discontinuation.",
        ["human", "safety", "PDE4"]),
    doc("fx:pm-hhip-gwas-2017", "pubmed", "2017-02-06", "COPD GWAS: HHIP, FAM13A, CHRNA5 loci",
        "Human genetics: HHIP among the strongest COPD susceptibility loci; no tractable binding site known.",
        ["human", "genetics", "HHIP"]),
    doc("fx:pm-danirixin-2019", "pubmed", "2019-04-15", "CXCR2 antagonist danirixin in COPD",
        "No efficacy and a numerical increase in exacerbations; programme stopped.",
        ["human", "failure", "safety", "CXCR2"]),
    doc("fx:pm-il5-mouse-2018", "pubmed", "2018-02-01", "Eosinophil depletion restores airway function in mice",
        "Preclinical IL5 study claiming eosinophil depletion reverses emphysema.", ["preclinical", "IL5"],
        doi="10.0000/fx.2018.001"),
    # ---- post-2021 (live run only; must never reach the backtest)
    doc("fx:pm-boreas-2023", "pubmed", "2023-05-21", "BOREAS: dupilumab in COPD with type 2 inflammation",
        "IL4R antibody reduced moderate/severe exacerbations ~30% in COPD with eos>=300 on triple therapy.",
        ["human", "IL4R", "type2"]),
    doc("fx:pm-enhance-2023", "pubmed", "2023-01-10", "ENHANCE-1/2: ensifentrine Phase 3",
        "Dual PDE3A/PDE4B inhibitor improved FEV1 and reduced exacerbation rate in Phase 3.",
        ["human", "PDE3", "PDE4"]),
    doc("fx:pm-il33-ph3-2025", "pubmed", "2025-05-20", "Anti-IL-33 Phase 3 results in COPD",
        "Anti-IL33 Phase 3 programmes reported mixed results: one trial met the primary endpoint, a replicate did not.",
        ["human", "IL33", "unreplicated"]),
]

TRIALS = [
    dict(id="fx:nct-galathea", source="ctgov", source_date="2014-06-01", title="Benralizumab in COPD exacerbations",
         phase="3", sponsor="AstraZeneca", intervention="benralizumab", mechanism="IL5RA antibody",
         primary_endpoint="annualised exacerbation rate", primary_completion="2018-08-01",
         inclusion_criteria=["COPD", ">=2 moderate or >=1 severe exacerbation in prior year", "on ICS/LABA/LAMA"],
         status_history=[{"on": "2014-06-01", "status": "recruiting"},
                         {"on": "2018-09-01", "status": "completed_negative"}]),
    dict(id="fx:nct-danirixin-2b", source="ctgov", source_date="2016-11-01", title="Danirixin (CXCR2) in COPD",
         phase="2", sponsor="GSK", intervention="danirixin", mechanism="CXCR2 antagonist",
         primary_endpoint="respiratory symptoms", primary_completion="2019-01-01",
         inclusion_criteria=["COPD", "mild to moderate airflow limitation"],
         status_history=[{"on": "2016-11-01", "status": "recruiting"},
                         {"on": "2018-12-01", "status": "terminated",
                          "why_stopped": "lack of efficacy and exacerbation imbalance"}]),
    dict(id="fx:nct-boreas", source="ctgov", source_date="2019-04-01", title="Dupilumab in COPD with type 2 inflammation (BOREAS)",
         phase="3", sponsor="Sanofi", intervention="dupilumab", mechanism="IL4R antibody",
         primary_endpoint="annualised moderate/severe exacerbation rate", primary_completion="2022-12-01",
         inclusion_criteria=["COPD 40-80 years", "blood eosinophils >=300 cells/uL",
                             ">=2 moderate or >=1 severe exacerbation in prior year", "on triple therapy"],
         status_history=[{"on": "2019-04-01", "status": "recruiting"},
                         {"on": "2023-03-01", "status": "completed"}]),
    dict(id="fx:nct-matinee", source="ctgov", source_date="2019-10-01", title="Mepolizumab in COPD (MATINEE)",
         phase="3", sponsor="GSK", intervention="mepolizumab", mechanism="IL5 antibody",
         primary_endpoint="annualised exacerbation rate", primary_completion="2024-06-01",
         inclusion_criteria=["COPD", "blood eosinophils >=300 cells/uL", "on triple therapy"],
         status_history=[{"on": "2019-10-01", "status": "recruiting"}]),
    dict(id="fx:nct-itepekimab-2", source="ctgov", source_date="2018-09-01", title="Itepekimab (anti-IL-33) Phase 2 in COPD",
         phase="2", sponsor="Regeneron", intervention="itepekimab", mechanism="IL33 antibody",
         primary_endpoint="annualised exacerbation rate", primary_completion="2020-08-01",
         inclusion_criteria=["COPD", ">=2 moderate or >=1 severe exacerbation", "current or former smokers"],
         status_history=[{"on": "2018-09-01", "status": "recruiting"},
                         {"on": "2020-09-01", "status": "completed"}]),
    dict(id="fx:nct-astegolimab-2", source="ctgov", source_date="2019-02-01", title="Astegolimab (anti-ST2) Phase 2 in COPD",
         phase="2", sponsor="Genentech", intervention="astegolimab", mechanism="IL1RL1 (ST2) antibody",
         primary_endpoint="exacerbation rate", primary_completion="2021-06-01",
         inclusion_criteria=["COPD", ">=1 exacerbation in prior year"],
         status_history=[{"on": "2019-02-01", "status": "recruiting"}]),
    dict(id="fx:nct-tezepelumab-copd", source="ctgov", source_date="2019-07-01", title="Tezepelumab Phase 2 in COPD",
         phase="2", sponsor="AstraZeneca", intervention="tezepelumab", mechanism="TSLP antibody",
         primary_endpoint="exacerbation rate", primary_completion="2022-03-01",
         inclusion_criteria=["COPD", ">=2 moderate or >=1 severe exacerbation", "on triple therapy"],
         status_history=[{"on": "2019-07-01", "status": "recruiting"}]),
    dict(id="fx:nct-enhance1", source="ctgov", source_date="2020-09-01", title="Ensifentrine Phase 3 (ENHANCE-1)",
         phase="3", sponsor="Verona Pharma", intervention="ensifentrine", mechanism="PDE3/PDE4 inhibitor",
         primary_endpoint="FEV1 AUC0-12h", primary_completion="2022-08-01",
         inclusion_criteria=["COPD 40-80 years", "FEV1 30-70% predicted", "symptomatic (mMRC>=2)"],
         status_history=[{"on": "2020-09-01", "status": "recruiting"},
                         {"on": "2022-12-01", "status": "completed"}]),
    dict(id="fx:nct-aerify1", source="ctgov", source_date="2021-06-01", title="Itepekimab Phase 3 (AERIFY-1)",
         phase="3", sponsor="Sanofi", intervention="itepekimab", mechanism="IL33 antibody",
         primary_endpoint="annualised exacerbation rate", primary_completion="2025-01-01",
         inclusion_criteria=["COPD", "former smokers", ">=2 moderate or >=1 severe exacerbation"],
         status_history=[{"on": "2021-06-01", "status": "recruiting"}]),
]


def assoc(sym, gen, phase, sm=False, ab=False, other=False, safety=()):
    return {"target_id": f"hgnc:{sym}", "symbol": sym, "genetic_score": gen,
            "known_drug_max_phase_in_disease": phase,
            "tractability": {"small_molecule": sm, "antibody": ab, "other": other},
            "safety": list(safety)}


OT_2020 = [
    assoc("PDE4B", 0.10, 4, sm=True, safety=["GI intolerance", "weight loss"]),
    assoc("PDE4D", 0.20, 4, sm=True, safety=["emesis"]),
    assoc("PDE3A", 0.00, 2, sm=True, safety=["cardiac inotropy with systemic exposure"]),
    assoc("IL4R", 0.15, 3, ab=True, safety=["conjunctivitis", "transient eosinophilia"]),
    assoc("IL13", 0.20, 2, ab=True),
    assoc("IL5", 0.05, 3, ab=True),
    assoc("IL5RA", 0.00, 3, ab=True),
    assoc("IL33", 0.35, 2, ab=True),
    assoc("IL1RL1", 0.45, 2, ab=True),
    assoc("TSLP", 0.25, 2, ab=True),
    assoc("CXCR2", 0.00, 2, sm=True, safety=["neutropenia"]),
    assoc("HHIP", 0.85, 0),
    assoc("MMP12", 0.40, 2, sm=True),
]
OT_2026 = [dict(a) for a in OT_2020]
for a in OT_2026:
    a["known_drug_max_phase_in_disease"] = {"PDE3A": 4, "IL4R": 4, "IL33": 3, "IL1RL1": 3,
                                            "TSLP": 3}.get(a["symbol"], a["known_drug_max_phase_in_disease"])

OT_RELEASES = [
    {"id": "fx:ot-2020-11", "source": "opentargets", "source_date": "2020-11-18", "associations": OT_2020},
    {"id": "fx:ot-2026-09", "source": "opentargets", "source_date": "2026-09-15", "associations": OT_2026},
]


def drug(id, date, name, mech, syms, inds, phase):
    return {"id": id, "source": "chembl", "source_date": date, "title": f"ChEMBL: {name}",
            "body": f"{name}: {mech}. Indications: {', '.join(inds)}. Max phase in COPD: {phase}.",
            "name": name, "mechanism": mech, "target_symbols": syms, "indications": inds,
            "max_phase_in_indication": phase}


DRUGS = [
    drug("fx:chembl-roflumilast", "2012-01-01", "roflumilast", "PDE4 inhibitor", ["PDE4B", "PDE4D"], ["COPD"], 4),
    drug("fx:chembl-ensifentrine", "2020-10-01", "ensifentrine", "PDE3/PDE4 inhibitor", ["PDE3A", "PDE4B"], ["COPD"], 3),
    drug("fx:chembl-dupilumab", "2019-06-01", "dupilumab", "IL4R antibody", ["IL4R"], ["asthma", "atopic dermatitis", "COPD"], 3),
    drug("fx:chembl-mepolizumab", "2018-01-01", "mepolizumab", "IL5 antibody", ["IL5"], ["asthma", "COPD"], 3),
    drug("fx:chembl-benralizumab", "2018-01-01", "benralizumab", "IL5RA antibody", ["IL5RA"], ["asthma", "COPD"], 3),
    drug("fx:chembl-itepekimab", "2019-06-01", "itepekimab", "IL33 antibody", ["IL33"], ["COPD", "asthma"], 2),
    drug("fx:chembl-astegolimab", "2019-06-01", "astegolimab", "IL1RL1 (ST2) antibody", ["IL1RL1"], ["COPD", "asthma"], 2),
    drug("fx:chembl-tezepelumab", "2020-06-01", "tezepelumab", "TSLP antibody", ["TSLP"], ["asthma", "COPD"], 2),
    drug("fx:chembl-danirixin", "2017-01-01", "danirixin", "CXCR2 antagonist", ["CXCR2"], ["COPD"], 2),
]

RETRACTIONS = [
    {"id": "fx:rw-2020-001", "source": "retraction", "source_date": "2020-02-01",
     "title": "Retraction notice", "body": "Retraction of 10.0000/fx.2018.001 (image duplication).",
     "doi": "10.0000/fx.2018.001"},
]

# ------------------------------------------------------------------ stub LLM outputs


def claim(text, src=(), conf=0.7):
    return {"id": "x", "agent": "x", "text": text, "kind": "evidence" if src else "reasoning",
            "confidence": conf, "source_ids": list(src)}


A1_IN = {"disease": "COPD", "disease_id": "EFO_0000341", "as_of": "2021-01-01", "mode": "single_shot",
         "profile": "dev"}
A1_OUT = {
    "shifts": [
        {"on": "2018-11-15", "type": "segment", "effect_on": ["selection"],
         "summary": "GOLD 2019 made blood eosinophils (>=300 cells/uL) a treatment-defining biomarker, creating a "
                    "biomarker-selectable COPD segment.",
         "claims": [claim("GOLD 2019 uses eosinophils >=300 to identify ICS responders", ["fx:pm-gold2019"], 0.85)]},
        {"on": "2019-03-10", "type": "endpoint", "effect_on": ["time", "cost"],
         "summary": "Annualised moderate/severe exacerbation rate is the accepted primary endpoint for add-on "
                    "biologics; eosinophil enrichment shrinks sample size.",
         "claims": [claim("Exacerbation rate is the accepted primary endpoint; enrichment lowers N",
                          ["fx:pm-endpoint-exac-2019"], 0.75)]},
        {"on": "2018-08-02", "type": "trial_tool", "effect_on": ["selection"],
         "summary": "Quantitative CT subtyping offers enrichment for airway-predominant disease.",
         "claims": [claim("Quantitative CT measures identify progression-prone subtypes", ["fx:pm-ct-imaging-2018"], 0.6)]},
        {"on": "2020-06-24", "type": "negative", "effect_on": ["cost", "time"],
         "summary": "Triple therapy cut exacerbations and showed a mortality signal (ETHOS), so new agents must "
                    "beat triple therapy as add-on, raising trial size and cost.",
         "claims": [claim("ETHOS: triple therapy reduced exacerbations with lower all-cause mortality",
                          ["fx:pm-ethos-2020"], 0.85)]},
        {"on": "2019-05-20", "type": "negative", "effect_on": ["selection"],
         "summary": "Anti-IL5 programs failed in eosinophilic COPD: eosinophil count alone is not a sufficient "
                    "selection biomarker for biologics.",
         "claims": [claim("Benralizumab did not reduce exacerbations despite eosinophil depletion",
                          ["fx:pm-galathea-2019"], 0.85),
                    claim("FDA declined mepolizumab for COPD", ["fx:pm-crl-mepo-2018"], 0.85)]},
    ],
    "segments": [
        {"name": "Type 2 / eosinophilic exacerbator on triple therapy",
         "inclusion_criteria": ["blood eosinophils >=300 cells/uL", ">=2 moderate or >=1 severe exacerbation in prior year",
                                "on ICS/LAMA/LABA triple therapy", "FeNO >=25 ppb (optional enrichment)"],
         "claims": [claim("20-40% of COPD shows type 2 inflammation linked to exacerbations",
                          ["fx:pm-type2-copd-2019"], 0.7)]},
        {"name": "Former-smoker frequent exacerbator, non-type-2",
         "inclusion_criteria": ["former smoker >=10 pack-years", "blood eosinophils <300 cells/uL",
                                ">=2 moderate exacerbations in prior year"],
         "claims": [claim("Smoke-driven IL-33 release is type-2-independent", ["fx:pm-il33-smoke-2020"], 0.6)]},
        {"name": "Symptomatic moderate COPD needing better bronchodilation",
         "inclusion_criteria": ["FEV1 30-70% predicted", "mMRC >=2 despite LAMA or LAMA/LABA"],
         "claims": [claim("Dual PDE3/4 inhibition improved FEV1 on top of LAMA", ["fx:pm-pde3-4-ph2-2018"], 0.7)]},
    ],
}


def card(id, mech, targets, direction, modality, tract, safety, unc, other, claims, tier=2, level="silver"):
    return {"id": id, "mechanism": mech, "target_ids": targets, "direction": direction,
            "evidence_tier": tier, "validation_level": level, "modality": modality,
            "tractability": tract, "safety_clues": safety, "biggest_uncertainty": unc,
            "rationale_other_conditions": other, "claims": claims}


A2_OUT = [
    card("h1", "Dual PDE3/PDE4 inhibition (inhaled)", ["hgnc:PDE3A", "hgnc:PDE4B", "hgnc:PDE4D"], "inhibit",
         "inhaled small molecule", "PDE3A/PDE4B small-molecule tractable; oral PDE4 precedent approved",
         ["oral PDE4 GI intolerance; inhaled route may avoid it"],
         "Whether bronchodilation + anti-inflammatory effect translates into exacerbation reduction",
         "PDE4 inhibition approved in psoriasis/atopic dermatitis",
         [claim("Ensifentrine improved FEV1 on top of LAMA", ["fx:pm-pde3-4-ph2-2018"], 0.75),
          claim("Oral PDE4 inhibition is approved for COPD exacerbations", ["fx:fda-roflumilast-2011"], 0.9),
          claim("Oral PDE4 causes GI intolerance", ["fx:pm-roflu-safety-2016"], 0.8)]),
    card("h2", "IL-4Ra blockade in type 2 COPD", ["hgnc:IL4R", "hgnc:IL13"], "inhibit", "monoclonal antibody",
         "Antibody-tractable; approved antibody in asthma", ["conjunctivitis", "transient eosinophilia"],
         "Whether type 2 COPD responds like type 2 asthma, given anti-IL5 failures",
         "IL-4Ra blockade effective in asthma and atopic dermatitis",
         [claim("Type 2 inflammation in 20-40% of COPD", ["fx:pm-type2-copd-2019"], 0.7),
          claim("IL4R antibody reduced asthma exacerbations, more with eos>=300", ["fx:pm-dupi-asthma-2018"], 0.8),
          claim("Phase 3 in eosinophilic COPD on triple therapy is under way", ["fx:nct-boreas"], 0.85)]),
    card("h3", "IL-33 / ST2 alarmin blockade in former smokers", ["hgnc:IL33", "hgnc:IL1RL1"], "inhibit",
         "monoclonal antibody", "Antibody-tractable ligand and receptor", ["none found in evidence"],
         "Whether the effect is limited to former smokers and replicates",
         "IL33/IL1RL1 genetics in asthma",
         [claim("IL33 and IL1RL1 variants associate with airway disease", ["fx:pm-il33-genetics-2019"], 0.7),
          claim("Smoke drives IL-33 release, type-2-independent", ["fx:pm-il33-smoke-2020"], 0.65),
          claim("Anti-IL33 and anti-ST2 Phase 2 programs active", ["fx:nct-itepekimab-2", "fx:nct-astegolimab-2"], 0.8)]),
    card("h4", "TSLP blockade upstream of type 2 and non-type 2", ["hgnc:TSLP"], "inhibit", "monoclonal antibody",
         "Antibody-tractable", ["none found in evidence"],
         "COPD efficacy unknown; asthma data may not transfer",
         "Anti-TSLP reduced asthma exacerbations regardless of eosinophils",
         [claim("Anti-TSLP reduced asthma exacerbations independent of eosinophils", ["fx:pm-tslp-asthma-2017"], 0.75),
          claim("TSLP blockade reduced smoke-induced inflammation in mice", ["fx:pm-tslp-mouse-2019"], 0.4)]),
    card("h5", "IL-5 / eosinophil depletion", ["hgnc:IL5", "hgnc:IL5RA"], "inhibit", "monoclonal antibody",
         "Antibody-tractable; approved in asthma", ["none found in evidence"],
         "Eosinophils may be a marker, not a driver, in COPD",
         "Effective in eosinophilic asthma",
         [claim("Mepolizumab modest, inconsistent in eosinophilic COPD", ["fx:pm-metrex-2017"], 0.8),
          claim("Eosinophil depletion reverses emphysema in mice", ["fx:pm-il5-mouse-2018"], 0.5)]),
    card("h6", "HHIP pathway restoration", ["hgnc:HHIP"], "activate", "unknown",
         "No known tractable binding site", ["none found in evidence"],
         "No modality exists", "none",
         [claim("HHIP is a top COPD GWAS locus", ["fx:pm-hhip-gwas-2017"], 0.85)]),
]


def tpp(hid, gap, mins, grail, musts, exp, claims):
    return {"hypothesis_id": hid, "soc_gap_at_launch": gap, "minimum_specs": mins,
            "holy_grail_specs": grail, "must_haves": musts, "expansion_opportunity": exp, "claims": claims}


A3_OUT = {
    "h1": tpp("h1", "Triple therapy leaves symptomatic patients with residual breathlessness; no approved agent "
              "combines bronchodilation and anti-inflammatory action without oral PDE4 GI toxicity.",
              ["FEV1 AUC0-12h improvement >=100 mL vs placebo on top of LAMA", "GI AE rate within 5% of placebo"],
              ["once-daily dry-powder inhaler", ">=30% exacerbation reduction"],
              [">=100 mL FEV1 improvement on top of LAMA", "GI adverse-event rate within 5% of placebo"],
              "Non-CF bronchiectasis and asthma via the same dual mechanism",
              [claim("Ensifentrine Phase 3 under way", ["fx:nct-enhance1"], 0.8),
               claim("Oral PDE4 GI intolerance limits use", ["fx:pm-roflu-safety-2016"], 0.8)]),
    "h2": tpp("h2", "At launch, eosinophilic exacerbators on triple therapy still exacerbate; anti-IL5 failed, so "
              "no approved biologic is expected for this segment.",
              [">=25% reduction in moderate/severe exacerbations vs placebo on top of triple therapy in eos>=300"],
              [">=40% exacerbation reduction plus >=100 mL FEV1 gain", "dosing every 4 weeks or less often"],
              [">=25% reduction in annualised moderate/severe exacerbations on triple therapy, eos>=300"],
              "Asthma-COPD overlap and chronic rhinosinusitis comorbidity",
              [claim("Phase 3 in eos>=300 on triple therapy", ["fx:nct-boreas"], 0.85),
               claim("Anti-IL5 failed in eosinophilic COPD", ["fx:pm-galathea-2019"], 0.85)]),
    "h3": tpp("h3", "Former smokers with low eosinophils have no targeted biologic; type 2 agents will not cover them.",
              [">=20% reduction in exacerbations in former smokers"],
              [">=30% exacerbation reduction across smoking status"],
              [">=20% reduction in annualised exacerbations in patients with eos<300 cells/uL"],
              "Asthma; idiopathic pulmonary fibrosis exploratory",
              [claim("IL-33 is type-2-independent", ["fx:pm-il33-smoke-2020"], 0.6)]),
    "h4": tpp("h4", "No biologic covers both type 2 and non-type 2 COPD at launch.",
              [">=20% exacerbation reduction regardless of eosinophil count"],
              [">=30% reduction, dosing every 4 weeks"],
              [">=20% reduction in annualised exacerbations independent of eosinophil count"],
              "Asthma (precedent); nasal polyps",
              [claim("Anti-TSLP asthma effect independent of eosinophils", ["fx:pm-tslp-asthma-2017"], 0.7)]),
    "h5": tpp("h5", "Eosinophilic COPD remains uncovered after anti-IL5 failures.",
              [">=20% exacerbation reduction in eos>=300"], [">=35% reduction"],
              [">=20% reduction in annualised exacerbations in eos>=300 on triple therapy"],
              "Hypereosinophilic syndromes",
              [claim("Anti-IL5 effect in COPD inconsistent", ["fx:pm-metrex-2017"], 0.8)]),
}


def risk(text, kc, test, claims):
    return {"text": text, "kill_criterion": kc, "cheapest_test": test, "claims": claims}


A6_OUT = {
    "h1": {"hypothesis_id": "h1", "verdict": "survives", "revise_target": None,
           "risks": [risk("FEV1 gains may not translate into fewer exacerbations",
                          "Phase 3 shows FEV1 benefit but no exacerbation trend",
                          "Pre-specified exacerbation analysis pooled across ENHANCE trials",
                          [claim("Phase 3 primary endpoint is FEV1, not exacerbations", ["fx:nct-enhance1"], 0.8)]),
                     risk("Nebulised delivery limits adoption vs handheld inhalers",
                          "Patient-preference data show <20% acceptance of nebuliser",
                          "Survey 200 COPD patients on current inhalers", [claim("Reasoning on delivery burden")])],
           "premortem": "The drug improved lung function but did not cut exacerbations enough to justify a nebulised regimen."},
    "h2": {"hypothesis_id": "h2", "verdict": "survives", "revise_target": None,
           "risks": [risk("Anti-IL5 failures suggest eosinophils are not the driver in COPD",
                          "BOREAS misses exacerbation primary endpoint",
                          "Compare IL-13/FeNO-high vs eos-only subgroups in existing Phase 3 data",
                          [claim("Benralizumab failed despite eosinophil depletion", ["fx:pm-galathea-2019"], 0.85)])],
           "premortem": "IL-4Ra blockade worked in asthma but COPD type 2 inflammation was too heterogeneous."},
    "h3": {"hypothesis_id": "h3", "verdict": "revise", "revise_target": "a3_profile",
           "risks": [risk("TPP population (all eos<300) is broader than the evidence, which points to former smokers",
                          "Effect absent in current smokers in a prospective trial",
                          "Restrict the TPP to former smokers and re-check the must-have effect size",
                          [claim("Smoke-driven IL-33 release suggests smoking status matters",
                                 ["fx:pm-il33-smoke-2020"], 0.6)]),
                     risk("Benefit may be limited to a former-smoker subgroup",
                          "Prospective trial in former smokers shows <15% reduction",
                          "Re-analyse Phase 2 by smoking status",
                          [claim("Phase 2 enrolled current and former smokers", ["fx:nct-itepekimab-2"], 0.7)])],
           "premortem": "The effect seen in former smokers did not replicate in a prospective trial."},
    "h4": {"hypothesis_id": "h4", "verdict": "survives", "revise_target": None,
           "risks": [risk("COPD rationale rests on asthma data and a single-lab mouse study",
                          "COPD Phase 2 shows no exacerbation effect",
                          "Wait for tezepelumab COPD Phase 2 readout; cost zero",
                          [claim("Mouse TSLP study is single-lab, not replicated", ["fx:pm-tslp-mouse-2019"], 0.7)])],
           "premortem": "Upstream alarmin blockade did not move exacerbations in a smoke-driven disease."},
    "h5": {"hypothesis_id": "h5", "verdict": "kill", "revise_target": None,
           "risks": [risk("The same mechanism already failed in the same population for efficacy",
                          "Two or more Phase 3 trials in eosinophilic COPD miss exacerbation endpoint",
                          "Already observed: GALATHEA/TERRANOVA and METREX/METREO",
                          [claim("Benralizumab failed in COPD", ["fx:pm-galathea-2019"], 0.9),
                           claim("FDA declined mepolizumab for COPD", ["fx:pm-crl-mepo-2018"], 0.9)]),
                     risk("Key preclinical support was retracted",
                          "Retraction confirmed", "Check Retraction Watch for cited DOIs",
                          [claim("Mouse eosinophil-depletion paper retracted", ["fx:rw-2020-001"], 0.9)])],
           "premortem": "Eosinophil depletion was already shown not to reduce COPD exacerbations."},
}


def main() -> None:
    (OUT / "retrieval").mkdir(parents=True, exist_ok=True)
    corpus = {"documents": DOCS, "trials": TRIALS, "opentargets_releases": OT_RELEASES,
              "drugs": DRUGS, "retractions": RETRACTIONS}
    files = {
        "retrieval/corpus.json": corpus,
        "a1_disease_in.json": A1_IN, "a1_disease_out.json": A1_OUT,
        "a2_mechanism_in.json": A1_OUT, "a2_mechanism_out.json": A2_OUT,
        "a3_profile_in.json": A2_OUT[0], "a3_profile_out.json": A3_OUT,
        "a6_redteam_out.json": A6_OUT,
    }
    for name, data in files.items():
        (OUT / name).write_text(json.dumps(data, indent=1))
    # a6 input = RedTeamInput for h1 (A4/A5 left None until person 4's fixtures land)
    (OUT / "a6_redteam_in.json").write_text(json.dumps(
        {"card": A2_OUT[0], "profile": A3_OUT["h1"], "why_now": A1_OUT,
         "inactive": None, "landscape": None}, indent=1))
    print("wrote", len(files) + 1, "fixture files to", OUT)


if __name__ == "__main__":
    main()
