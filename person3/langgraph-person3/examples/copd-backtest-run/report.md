# Person 3 analysis · COPD · as of 2021-01-01
Run `75a887be-a20b-4dcc-9d57-b90aa238c6b5` · mode single_shot · LLM live (dev profile) · 17 calls · 42,816 in / 120,830 out tokens

## Q1 Why now
- **2019-03-10 · endpoint** (time, cost): Annualised moderate/severe exacerbation rate accepted as primary endpoint for COPD add-on biologics; eosinophil enrichment reduces required sample size, shortening trials and cutting cost.
- **2018-11-15 · segment** (selection): GOLD 2019 formalises blood eosinophil count >=300 cells/uL as a biomarker-defined segment that benefits from ICS; this segment is now used to enrich biologic trials (BOREAS, MATINEE).
- **2019-05-20 · negative** (selection): Benralizumab (anti-IL5RA) failed to reduce exacerbations in eosinophilic COPD despite eosinophil depletion, and FDA declined mepolizumab; this raises the bar for eosinophil-targeted therapies and suggests eosinophils are a marker, not the driver.
- **2020-06-24 · negative** (cost, time): Triple therapy (ICS/LAMA/LABA) becomes standard of care with mortality benefit (ETHOS) and multiple approvals; any new add-on must be tested on top of triple therapy, increasing trial cost and duration.
- **2018-08-02 · trial_tool** (selection): Quantitative CT imaging identifies COPD subtypes and predicts progression, proposed as an enrichment tool to select patients for trials.

## Q2 Mechanisms and Q5 verdicts

| # | Hypothesis | Tier / level | Verdict | Top risk | Cheapest test |
|---|---|---|---|---|---|
| 1 | IL4R/IL13 blockade | 1 / gold | survives | The strongest bear case is that eosinophil/type2 inflammation is a marker, not a | Re-analyze GALATHEA/TERRANOVA data to test whether any type2 |
| 2 | PDE4B/D inhibition | 1 / gold | survives | The dual PDE4B/D mechanism is self-limiting: PDE4D inhibition is the known drive | Literature and patent review for PDE4D-selective emesis data |
| 3 | PDE3/PDE4 dual inhibition | 1 / gold | survives | Efficacy on top of triple therapy is unproven. Phase 2b (fx:pm-pde3-4-ph2-2018)  | Monitor ENHANCE-1 results (fx:nct-enhance1) or analyze phase |
| 4 | IL33/ST2 axis inhibition | 2 / silver | survives (revised) | The product profile's must-have depends on a serum IL33/sST2 biomarker threshold | Measure IL33 and soluble ST2 in stored serum from ECLIPSE or |
| 5 | TSLP inhibition | 2 / silver | survives | Translation from asthma to COPD may fail. The only human efficacy evidence for T | A phase 2a proof-of-concept trial in COPD patients with exac |

## Q3 Must-haves (TPP)
- **h3**: >=30% reduction in annualised moderate/severe exacerbation rate vs placebo on top of triple therapy in patients with blood eos >=300 cells/uL (primary endpoint, with lower bound of 95% CI >0); No increase in serious adverse events vs placebo (upper bound of 95% CI for SAE rate <1.5)
- **h4**: >=25% reduction in annualized moderate/severe exacerbation rate vs placebo on top of triple therapy in patients with chronic bronchitis and >=2 moderate or >=1 severe exacerbation in prior year (52-week endpoint); Discontinuation rate due to GI adverse events <=10% at 52 weeks
- **h5**: Efficacy: At least 20% reduction in annualized moderate/severe exacerbation rate vs placebo on top of triple therapy in patients with FEV1 30-70% predicted and ≥2 moderate or ≥1 severe exacerbation in the prior year.; Safety: No increase in MACE (cardiovascular death, non-fatal MI, non-fatal stroke) with an upper bound of the 95% CI for the hazard ratio vs placebo ≤1.5.
- **h1**: Demonstrate >=30% reduction in annualized moderate/severe exacerbation rate vs placebo on top of triple therapy at 52 weeks in a biomarker-selected population (e.g., serum IL33 or soluble ST2 above a predefined threshold).; Subcutaneous dosing with a frequency of no more than once every 4 weeks.
- **h2**: Demonstrated ≥20% reduction in annualised moderate/severe exacerbations vs placebo on top of triple therapy in the subgroup with blood eosinophils <300 cells/uL (i.e., non-type 2 patients); Subcutaneous administration with a dosing interval of at least 4 weeks (i.e., no more frequent than monthly) to ensure adherence and competitiveness with other biologics

## Decision log

| actor | action | target | reason |
|---|---|---|---|
| orchestrator | override | a6_redteam:h4 | revise target a2_mechanism is run-wide; not rerun per hypothesis: The dual PDE4B/D mechani |
| red_teamer | revise | a3_profile:h1 | Prior failure of anti-IL5RA (benralizumab) in eosinophilic COPD (fx:pm-galathea-2019) and  |
| orchestrator | override | a6_redteam:h1 | revise cap reached (one round): The product profile's must-have depends on a serum IL33/sS |

Workbook: `person3/langgraph-person3/runs/75a887be-a20b-4dcc-9d57-b90aa238c6b5/analysis.xlsx`

_Next (other persons): A7 citation gate over every surviving claim, then A8 memo._