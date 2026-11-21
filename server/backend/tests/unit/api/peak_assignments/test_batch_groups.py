"""Unit tests for the batch's groups (MAS-1066): the rows a reading spans.

No DB: :func:`batch_series.batch_groups` over rows as the ledger serves them,
:func:`batch_series.series_step`, :func:`batch_series.series_lattice_key`,
:func:`batch_series.group_row` and
:func:`batch_peaks.continuity_reading`. The reads that serve them are
``integration/api/peak_assignments/test_batch_groups_api.py``.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments import batch_series
from mascope_backend.api.new.peak_assignments.batch_peaks import continuity_reading
from mascope_backend.api.new.peak_assignments.batch_series import (
    batch_groups,
    group_row,
    groups_of,
    series_lattice_key,
    series_step,
)
from mascope_tools.composition.utils import parse_formula_tokens


def _row(batch_peak_id, formula, ion, mechanism="m1", mz=100.0, **extra):
    return {
        "batch_peak_id": batch_peak_id,
        "mz": mz,
        "consensus_formula": formula,
        "consensus_ion_formula": ion,
        "ionization_mechanism_id": mechanism,
        "consensus_tier": "assigned",
        "consensus_role": None,
        "isotopologue_of": None,
        "series": None,
        "co_variation": None,
        "batch_checks": None,
        **extra,
    }


def _series(root_id, root_formula="C5H8O4", unit="CH2", mechanism="m1"):
    return {
        "formula": "x",
        "ionization_mechanism_id": mechanism,
        "unit": unit,
        "root_batch_peak_id": root_id,
        "root_formula": root_formula,
    }


def _ids(group):
    return [member["batch_peak_id"] for member in group["members"]]


@pytest.mark.parametrize(
    "formula, step",
    [
        ("C5H8O4", 0),
        ("C6H10O4", 1),
        ("C9H16O4", 4),
        ("C4H6O4", -1),
        ("C3H4O4", -2),
        ("C6H11O4", None),
        ("C6H10O5", None),
        ("C7H10O4", None),
    ],
)
def test_a_formula_is_a_whole_number_of_units_from_the_root(formula, step):
    root = parse_formula_tokens("C5H8O4")
    assert (
        series_step(root, parse_formula_tokens("CH2"), parse_formula_tokens(formula))
        == step
    )


def test_a_step_of_a_unit_of_one_element_and_an_empty_unit():
    root = parse_formula_tokens("C5H8O4")
    assert series_step(root, {"O": 1}, parse_formula_tokens("C5H8O6")) == 2
    assert series_step(root, {"O": 1}, parse_formula_tokens("C5H8O3")) == -1
    assert series_step(root, {}, root) is None


def test_a_unit_with_a_negative_count_still_steps_whole_units():
    root = parse_formula_tokens("C5H8O4")
    # Loss of H2 per step, written with a negative count.
    unit = {"H": -2}
    assert series_step(root, unit, parse_formula_tokens("C5H4O4")) == 2
    assert series_step(root, unit, parse_formula_tokens("C5H10O4")) == -1
    assert series_step(root, unit, parse_formula_tokens("C5H5O4")) is None


def test_whole_units_apart_is_the_same_lattice_residual():
    unit = parse_formula_tokens("CH2")
    keys = {
        formula: series_lattice_key(unit, parse_formula_tokens(formula))
        for formula in ("C5H8O4", "C9H16O4", "C2H2O4", "C5H9O4")
    }
    assert keys["C5H8O4"][0] == keys["C9H16O4"][0] == keys["C2H2O4"][0]
    assert keys["C5H9O4"][0] != keys["C5H8O4"][0]
    assert keys["C9H16O4"][1] - keys["C5H8O4"][1] == 4
    assert series_lattice_key({}, parse_formula_tokens("C5H8O4")) is None


def test_the_series_parse_each_formula_once_whatever_the_number_of_series(
    monkeypatch,
):
    calls = []
    real = batch_series.parse_formula_tokens

    def counting(formula):
        calls.append(formula)
        return real(formula)

    monkeypatch.setattr(batch_series, "parse_formula_tokens", counting)
    roots = 40
    rows = []
    for n in range(roots):
        root = f"C{n + 1}H2O{n + 1}"
        rows.append(_row(f"r{n}", root, f"r{n}-", mz=100.0 + n))
        rows.append(
            _row(
                f"s{n}",
                f"C{n + 2}H4O{n + 1}",
                f"s{n}-",
                mz=200.0 + n,
                series=_series(f"r{n}", root),
            )
        )
    groups = batch_groups(rows, {})
    assert len([g for g in groups if g["kind"] == "series"]) == roots
    # One parse per row, and per series its unit and root: linear, where a
    # walk of every row per series would be roots * rows.
    assert len(calls) <= len(rows) + 3 * roots


def test_a_series_lists_the_root_and_every_whole_step_through_its_mechanism():
    rows = [
        _row("root", "C5H8O4", "a-", mz=194.0),
        _row("up", "C6H10O4", "b-", mz=208.0, series=_series("root")),
        _row("three", "C8H14O4", "c-", mz=236.0),
        _row("down", "C4H6O4", "d-", mz=180.0),
        _row("other", "C7H12O4", "e-", mechanism="m2", mz=159.0),
        _row("claimed", "C7H12O4", "f-", mz=222.0, consensus_role="reagent"),
        _row("line", "C7H12O4", "[13C]g-", mz=223.0),
        _row("owned_line", "C7H12O4", "h-", mz=224.0, isotopologue_of="up"),
        _row("off", "C6H11O4", "i-", mz=209.0),
        # The root's own formula on another batch peak is no step of it.
        _row("twin_root", "C5H8O4", "a-", mz=194.1),
    ]
    groups = batch_groups(rows, {"m1": "[M+NO3]-"})
    # The batch peaks of one neutral through two ions are also its channels.
    assert [group["kind"] for group in groups] == ["series", "channels"]
    series = groups[0]
    assert series["group_id"] == "series:root:CH2:m1"
    assert _ids(series) == ["down", "root", "up", "three"]
    assert [m["step"] for m in series["members"]] == [-1, 0, 1, 3]
    assert series["root_batch_peak_id"] == "root"
    assert series["unit"] == "CH2"
    assert series["members"][0]["mechanism"] == "[M+NO3]-"
    assert series["members"][0]["sample_peak_id"] is None


def test_two_units_through_one_mechanism_are_two_series_of_their_own_steps():
    # Each unit has its own lattice of the mechanism's batch peaks: a step of
    # one unit is no member of the other unit's series.
    rows = [
        _row("root", "C5H8O4", "a-", mz=194.0),
        _row("up", "C6H10O4", "b-", mz=208.0, series=_series("root")),
        _row("up2", "C7H12O4", "c-", mz=222.0),
        _row("ox", "C5H8O5", "d-", mz=210.0, series=_series("root", unit="O")),
        _row("ox2", "C5H8O6", "e-", mz=226.0),
    ]
    groups = batch_groups(rows, {})
    assert [group["group_id"] for group in groups] == [
        "series:root:CH2:m1",
        "series:root:O:m1",
    ]
    assert _ids(groups[0]) == ["root", "up", "up2"]
    assert [m["step"] for m in groups[0]["members"]] == [0, 1, 2]
    assert _ids(groups[1]) == ["root", "ox", "ox2"]
    assert [m["step"] for m in groups[1]["members"]] == [0, 1, 2]


def test_a_series_whose_root_went_still_lists_its_steps():
    rows = [
        _row("up", "C6H10O4", "b-", series=_series("gone")),
        _row("three", "C8H14O4", "c-"),
    ]
    [series] = batch_groups(rows, {})
    assert _ids(series) == ["up", "three"]


def test_a_series_of_one_member_is_no_group():
    rows = [_row("up", "C6H10O4", "b-", series=_series("gone"))]
    assert batch_groups(rows, {}) == []


def test_two_roots_of_one_lattice_are_one_group():
    rows = [
        _row("a", "C5H8O4", "a-", mz=194.0),
        _row("b", "C7H12O4", "b-", mz=222.0),
        _row("up", "C6H10O4", "c-", mz=208.0, series=_series("a")),
        _row("up2", "C8H14O4", "d-", mz=236.0, series=_series("b", "C7H12O4")),
    ]
    [series] = batch_groups(rows, {})
    assert series["root_batch_peak_id"] == "a"
    assert _ids(series) == ["a", "up", "b", "up2"]


def test_channels_are_one_neutral_through_more_than_one_ion():
    rows = [
        _row("a", "C6H10O4", "x-", mz=208.0),
        _row("b", "C6H10O4", "y-", mechanism="m2", mz=145.0),
        _row("line", "C6H10O4", "[13C]x-", mz=209.0),
        _row("owned", "C6H10O4", "z-", mz=210.0, isotopologue_of="a"),
        _row("alone", "C7H10O4", "w-", mz=300.0),
        _row("twice", "C9H10O4", "v-", mz=400.0),
        _row("twice_again", "C9H10O4", "v-", mz=401.0),
    ]
    [channels] = batch_groups(rows, {"m2": "[M-H]-"})
    assert channels["kind"] == "channels"
    assert channels["formula"] == "C6H10O4"
    assert _ids(channels) == ["b", "a"]
    assert channels["members"][0]["mechanism"] == "[M-H]-"


def test_a_co_variation_pair_both_sides_record_is_one_group():
    def record(partner):
        return {"partners": [{"batch_peak_id": partner}, {"batch_peak_id": "gone"}]}

    rows = [
        _row("a", "C6H10O4", "x-", mz=208.0, co_variation=record("b")),
        _row("b", "C6H10O4", "y-", mz=145.0, co_variation=record("a")),
    ]
    groups = [g for g in batch_groups(rows, {}) if g["kind"] == "co_variation"]
    assert len(groups) == 1
    assert set(_ids(groups[0])) == {"a", "b"}


def test_a_check_pair_per_fact_naming_another_batch_peak():
    rows = [
        _row(
            "a",
            "C5H8O4",
            "a-",
            mz=194.0,
            batch_checks={
                "label_twin": {"batch_peak_id": "twin"},
                "halogen_lock": {"batch_peak_id": "gone"},
                "neutral_pair": {"partner_batch_peak_id": "p"},
            },
        ),
        _row("twin", "C5H9NO4", "t-", mechanism="m2", mz=195.0),
        _row(
            "p",
            "C5H9NO4",
            "u-",
            mechanism="m3",
            mz=196.0,
            batch_checks={"neutral_pair": {"partner_batch_peak_id": "a"}},
        ),
    ]
    rows[2]["batch_checks"]["label_twin"] = {"batch_peak_id": "p"}
    pairs = [g for g in batch_groups(rows, {}) if g["kind"] == "check_pair"]
    assert [(g["check"], _ids(g)) for g in pairs] == [
        ("label_twin", ["a", "twin"]),
        ("neutral_pair", ["a", "p"]),
    ]
    assert groups_of(pairs, "twin") == [pairs[0]]
    assert groups_of(pairs, "nobody") == []


def test_a_live_anchor_is_read_through_its_standing_records():
    anchor = SimpleNamespace(
        batch_peak_id="a",
        mz=208.0,
        consensus_formula="C6H10O4",
        consensus_ion_formula="x-",
        ionization_mechanism_id="m1",
        consensus_tier="assigned",
        consensus_role=None,
        isotopologue_of=None,
        provenance={
            # Read for another formula: it stands beside no reading of this one.
            "series": {"formula": "C7H12O4", "unit": "CH2"},
            "co_variation": {
                "formula": "C6H10O4",
                "ionization_mechanism_id": "m1",
                "stale": True,
            },
        },
    )
    row = group_row(anchor)
    assert row["series"] is None
    assert row["co_variation"] is None
    anchor.provenance["series"]["formula"] = "C6H10O4"
    assert group_row(anchor)["series"]["formula"] == "C6H10O4"


def test_the_continuity_reading():
    anchor = SimpleNamespace(
        continuity_grade="drift",
        continuity={
            "formula": "C6H10O4",
            "ionization_mechanism_id": "m1",
            "n_present_at_compute": 5,
            "warnings": ["partial"],
        },
        consensus_formula="C6H10O4",
        ionization_mechanism_id="m1",
        n_present=5,
    )
    reading = continuity_reading(anchor)
    assert reading["grade"] == "drift"
    assert reading["partial"] is True
    assert reading["stale"] is False
    anchor.n_present = 6
    anchor.continuity["warnings"] = []
    reading = continuity_reading(anchor)
    assert reading["stale"] is True
    assert reading["partial"] is False
    anchor.continuity_grade, anchor.continuity = None, None
    assert continuity_reading(anchor) is None
    assert continuity_reading(None) is None
