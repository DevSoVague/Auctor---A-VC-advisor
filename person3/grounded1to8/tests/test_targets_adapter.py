"""Open Targets rows from the store reach agent 2 with real genetics and drug-type flags.

Before this, the wrapper passed the overall association score as the genetics score and an empty
tractability, so A2 rejected every real target ("no tractable modality"). Rows below are shaped
like the store's targets table (Open Targets 24.09).
"""

from __future__ import annotations

import asyncio
from datetime import date

from agents.analysis.a2_mechanism import grade
from agents.analysis.db_tools import EvidenceStoreRetrieval, _target_record, _tractability
from agents.analysis._common import EvidencePack

D = date(2024, 9, 18)
NR3C1 = {"target_id": "ENSG00000113580", "symbol": "NR3C1", "name": "nuclear receptor subfamily 3",
         "association_score": 0.69, "genetic_score": 0.51, "max_phase": 4,
         "tractability": ["SM: Approved Drug", "SM: High-Quality Ligand", "AB: UniProt loc high conf"],
         "evidence_types": ["genetic_association"], "as_of_date": D}
ADRB2 = {**NR3C1, "target_id": "ENSG00000169252", "symbol": "ADRB2", "association_score": 0.63,
         "genetic_score": None, "evidence_types": [], "tractability": ["SM: Approved Drug"]}
NOTHING = {**NR3C1, "target_id": "ENSG1", "symbol": "ZZZ", "association_score": 0.2, "max_phase": None,
           "genetic_score": None, "tractability": []}


# tractable but no genetics and no COPD drug past phase 1: not worth A2's attention
WEAK = {**NOTHING, "target_id": "ENSG2", "symbol": "WEAK", "association_score": 0.5, "max_phase": 1,
        "tractability": ["SM: Approved Drug"]}


def test_tractability_labels_become_modality_flags():
    assert _tractability(["SM: Approved Drug", "AB: UniProt loc high conf"]) == {
        "small_molecule": True, "antibody": True, "other": False}
    assert _tractability(["PR: Predicted Tractable"]) == {
        "small_molecule": False, "antibody": False, "other": True}
    assert not any(_tractability(None).values()) and not any(_tractability([]).values())


def test_record_uses_the_genetic_score_not_the_overall_score():
    rec = _target_record(NR3C1, D)
    assert rec["genetic_score"] == 0.51 and rec["association_score"] == 0.69
    assert rec["known_drug_max_phase_in_disease"] == 4.0
    assert rec["tractability"]["small_molecule"] and rec["source_date"] == "2024-09-18"
    assert _target_record(ADRB2, D)["genetic_score"] == 0.0  # no genetic evidence is 0, not the overall score
    assert _target_record(NOTHING, D)["known_drug_max_phase_in_disease"] is None


def test_a_real_target_passes_a2s_gate_and_one_with_no_modality_does_not():
    class Card:
        target_ids = ["ENSG00000113580"]

    by_id = {NR3C1["target_id"]: _target_record(NR3C1, D)}
    ok = grade(Card(), by_id, [], EvidencePack(D))
    assert ok["reject"] is None and ok["tier"] == 1  # genetics 0.51 is above the strong bar

    Card.target_ids = ["ENSG1"]
    by_id = {NOTHING["target_id"]: _target_record(NOTHING, D)}
    assert grade(Card(), by_id, [], EvidencePack(D))["reject"] == "no tractable modality for any target"


def test_store_targets_are_filtered_ranked_and_limited(monkeypatch):
    import agents.analysis.db_tools as tools

    class FakeStore:
        async def _a(self, sql, params):
            return [NOTHING, ADRB2, NR3C1, WEAK]

    monkeypatch.setattr(tools, "TARGET_LIMIT", 2)
    client = EvidenceStoreRetrieval.__new__(EvidenceStoreRetrieval)
    client.store, client.as_of = FakeStore(), date(2026, 1, 1)
    out = asyncio.run(client.targets("EFO_0000341"))
    assert [t["symbol"] for t in out] == ["NR3C1", "ADRB2"]
