from datetime import date

import pytest

from frontier.agents.competitor.common import (
    MAX_EVIDENCE,
    AliasIndex,
    Candidate,
    Mechanism,
    collect_assets,
    evidence,
    highest_stage,
    interventions,
    last_update,
    match_replies,
    mechanism_tokens,
    months_before,
    owner_type,
    parse_date,
    placement_weights,
    same_mechanism,
    shown,
    trial_stage,
)
from frontier.core.retrieval import Record

BACKTEST = date(2021, 1, 1)


@pytest.mark.parametrize(
    ("phase", "stage"),
    [
        ("PHASE3", "phase3"),
        ("EARLY_PHASE1", "phase1"),
        ("PHASE1/PHASE2", "phase1"),  # combined phases count as the earlier one
        (["PHASE2", "PHASE3"], "phase2"),
        ("PHASE4", "approved"),
        ("NA", None),
        (None, None),
    ],
)
def test_trial_stage(trial, phase, stage):
    assert trial_stage(trial(phase=phase)) == stage


def test_highest_stage_ignores_unknown():
    assert highest_stage(["phase1", None, "phase3", "phase2"]) == "phase3"
    assert highest_stage([None]) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2027-03-15", date(2027, 3, 15)),
        ("2027-03", date(2027, 3, 31)),  # the last day it could mean, as person 1 ingests
        ("2027", date(2027, 12, 31)),
        (date(2020, 1, 2), date(2020, 1, 2)),
        ("soon", None),
        (None, None),
    ],
)
def test_parse_date(value, expected):
    assert parse_date(value) == expected


def test_months_before_crosses_years_and_caps_the_day():
    assert months_before(BACKTEST, 18) == date(2019, 7, 1)
    assert months_before(date(2024, 3, 31), 1) == date(2024, 2, 28)


def test_last_update_never_reads_past_as_of(trial):
    record = trial(source_date=date(2018, 5, 1), last_update_posted="2022-06-01")
    assert last_update(record, as_of=date(2026, 11, 7)) == date(2022, 6, 1)
    assert last_update(record, as_of=BACKTEST) == date(2018, 5, 1)
    assert last_update(trial(source_date=date(2018, 5, 1)), as_of=BACKTEST) == date(2018, 5, 1)


def test_alias_index_merges_spellings_into_one_entity():
    drug = Record(
        id="chembl:FIXTURE-0101",
        source="chembl",
        source_date=date(2020, 1, 1),
        fields={"name": "Fixtafilast", "synonyms": ["FIX-101", "FIX 101A"]},
    )
    index = AliasIndex([drug])
    assert index.resolve("fix101") == index.resolve("Fixtafilast") == "chembl:FIXTURE-0101"
    assert index.resolve("FIX-999") == "name:fix999"


@pytest.mark.parametrize(
    ("sponsor", "sponsor_class", "forms", "expected"),
    [
        ("AstraZeneca AB", "INDUSTRY", [], "big_pharma"),
        ("Genentech, Inc.", "INDUSTRY", [], "big_pharma"),
        ("University of Fixture", "OTHER", [], "academic"),
        ("National Heart, Lung, and Blood Institute (NHLBI)", "NIH", [], "academic"),
        ("Fixture Bio Inc", "INDUSTRY", ["10-K", "8-K"], "mid_size"),
        ("Fixture Bio Inc", "INDUSTRY", ["D"], "startup"),
        ("Fixture Bio Inc", None, [], "startup"),
    ],
)
def test_owner_type(sponsor, sponsor_class, forms, expected):
    filings = [
        Record(id="sec:FIXTURE", source="edgar", source_date=BACKTEST, fields={"form": form})
        for form in forms
    ]
    assert owner_type(sponsor, sponsor_class, filings) == expected


def chembl(mechanism: str, targets: list[str] | None = None, **fields) -> Candidate:
    fields = {"name": "x", "mechanism": mechanism, "targets": targets or [], **fields}
    return Candidate.of(
        Record(id="chembl:FIXTURE-X", source="chembl", source_date=BACKTEST, fields=fields)
    )


@pytest.mark.parametrize(
    ("candidate", "placement"),
    [
        (chembl("PDE4 inhibitor", ["hgnc:FIXTURE-PDE4B"]), "on"),  # shared target
        (chembl("PDE4 inhibitor"), "on"),  # shared mechanism word
        (chembl("IL-33 antagonist", ["hgnc:FIXTURE-IL33"]), "off"),  # disjoint known targets
        (chembl("Phosphodiesterase 4 inhibitor"), "open"),  # synonym, no target: the model decides
        (Candidate("name:fix606", "FIX-606", None), "open"),  # not in ChEMBL
    ],
)
def test_mechanism_placement(candidate, placement):
    pde = Mechanism.of("PDE3/PDE4 inhibition", ["hgnc:FIXTURE-PDE4B"])
    assert pde.place(candidate) == placement


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Phosphodiesterase 4 inhibitor", "Phosphodiesterase type 4 inhibitor", True),
        ("Phosphodiesterase 4 inhibitor", "Phosphodiesterase 3 inhibitor", False),
        ("Beta-2 adrenergic receptor agonist", "Beta-1 adrenergic receptor antagonist", False),
        (
            "Muscarinic acetylcholine receptor M3 antagonist",
            "Muscarinic receptor M2 agonist",
            False,
        ),
        ("Muscarinic antagonist", "Muscarinic acetylcholine receptor M3 antagonist", True),
        ("Glucocorticoid receptor agonist", "Inhaled glucocorticoid", True),
        ("PDE3/PDE4 inhibitor", "PDE3 inhibitor", True),
        ("Inhibitor", "Inhibitor", False),  # no distinctive words
    ],
)
def test_same_mechanism_needs_a_subtype_or_all_words(a, b, same):
    assert same_mechanism(mechanism_tokens(a), mechanism_tokens(b)) is same


def test_family_word_does_not_overrule_disjoint_targets():
    laba = Mechanism.of("Beta-2 adrenergic receptor agonist", ["hgnc:FIXTURE-ADRB2"])
    blocker = chembl("Beta-1 adrenergic receptor antagonist", ["hgnc:FIXTURE-ADRB1"])
    assert laba.place(blocker) == "off"


def test_drugs_without_a_mechanism_are_not_one_mechanism():
    # Both display as "Unknown"; pooled, they would pose as the disease's busiest mechanism.
    first, second = chembl(""), chembl("")
    assert first.mechanism == "Unknown"
    assert not Mechanism.of(first.mechanism_text, first.targets).covers(second)


def ctgov(*items) -> Record:
    return Record(
        id="nct:FIXTURE-T9", source="ctgov", source_date=BACKTEST, fields={"interventions": items}
    )


def test_interventions_read_person_1s_shape_and_bare_names():
    trial = ctgov(
        {"type": "DRUG", "name": "BX-9 tablet", "other_names": ["FIX-9"]},
        {"type": "DRUG", "name": "Placebo"},
        {"type": "DEVICE", "name": "Spacer"},
        {"type": "BIOLOGICAL", "name": "FIX-10", "other_names": None},
        "FIX-11",
    )
    assert interventions(trial) == [["BX-9 tablet", "FIX-9"], ["FIX-10"], ["FIX-11"]]


def test_collect_assets_resolves_through_other_names():
    drug = Record(
        id="chembl:FIXTURE-9", source="chembl", source_date=BACKTEST, fields={"name": "fixonine"}
    )
    trial = ctgov({"type": "DRUG", "name": "BX-9 tablet", "other_names": ["Fixonine"]})
    [asset] = collect_assets([trial], [], [drug])
    assert (asset.entity_id, asset.name, asset.trials) == ("chembl:FIXTURE-9", "fixonine", [trial])


def test_evidence_is_capped_and_trimmed():
    big = [
        Record(
            id=f"nct:FIXTURE-{i}",
            source="ctgov",
            source_date=BACKTEST,
            body="x" * 5000,
            fields={"phase": "PHASE2", "sections": {"warnings": "y" * 5000}},
        )
        for i in range(10)
    ]
    picked = shown([big[3], None, big[3], *big])  # first come first, no repeats or gaps
    assert picked == [big[3], *[r for r in big if r is not big[3]]][:MAX_EVIDENCE]
    [brief] = evidence(big[:1])
    assert len(brief["text"]) < 1000 and brief["fields"] == {"phase": "PHASE2"}


@pytest.mark.parametrize("bad", [{"code": 1.0}, {"code": 1.0, "model": 1.5}, {"model": 0.5}])
def test_placement_weights_are_checked(bad):
    with pytest.raises(ValueError):
        placement_weights(bad)


def test_match_replies_tries_keys_in_order_and_reports_leftovers():
    keys = {
        "a": ["chembl:FIXTURE-1", "fixtafilast", "FIX-101", "nct:FIXTURE-C1"],
        "b": ["chembl:FIXTURE-2", "fixomilast", "nct:FIXTURE-C1"],  # shares a trial with a
    }
    replies = [
        ("by synonym", ["FIX 101"]),
        ("by trial shared with a taken asset", ["nct:FIXTURE-C1"]),
        ("invented", ["FIX-999", None]),
    ]
    matched, unmatched = match_replies(keys, replies)
    assert matched == {"a": "by synonym", "b": "by trial shared with a taken asset"}
    assert unmatched == ["invented"]


def test_ambiguous_key_matches_nothing():
    matched, unmatched = match_replies({"a": ["nct:FIXTURE-C1"], "b": ["nct:FIXTURE-C1"]},
                                       [("r", ["nct:FIXTURE-C1"])])  # fmt: skip
    assert (matched, unmatched) == ({}, ["r"])
