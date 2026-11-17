"""The finder searches a multimer, a charge above one and a neutral loss.

The notation reads three shapes the finder used to refuse: ``[2M+H]+``,
``[M+2H]2+`` and ``[M+H-H2O]+``. A candidate's neutral is
``(|z| * m/z - shift) / n`` and its ion the ``n`` molecules with the species
and less the loss, so each is searched at its own mass, its ion written as a
target ion's is and its envelope predicted at its own charge, where the
lines of a doubly charged ion sit half a unit apart.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mascope_tools.composition.finder import (
    _ion_charge,
    assign_compositions,
    find_compositions,
    grids_for_targets,
    neutral_mass_bounds,
    neutral_mass_windows,
)
from mascope_tools.composition.grid import build_neutral_grid
from mascope_tools.composition.heuristic_filter import predict_isotopes
from mascope_tools.composition.models import CompositionSearchConfig
from mascope_tools.composition.utils import (
    calculate_mass,
    parse_ionization,
)


GLUCOSE = "C6H12O6"
GLUCOSE_MASS = calculate_mass(formula=GLUCOSE)

#: Each shape, the ion it makes of glucose, and that ion's charge.
SHAPES = [
    ("[M+H]+", "C6H13O6+", 1),
    ("[2M+H]+", "C12H25O12+", 1),
    ("[M+2H]2+", "C6H14O6++", 2),
    ("[M+Ca]2+", "C6H12CaO6++", 2),
    ("[M+H-H2O]+", "C6H11O5+", 1),
    ("[2M+Na-H2O]+", "C12H22NaO11+", 1),
    ("[M-H-H2O]-", "C6H9O5-", -1),
    ("[M-CO2-H]-", "C5H11O4-", -1),
    ("[M-2H]2-", "C6H10O6--", -2),
]


def _config(ionizations: str, **kwargs) -> CompositionSearchConfig:
    return CompositionSearchConfig(
        ionizations=ionizations,
        element_count_ranges=kwargs.pop("element_count_ranges", "C0-14 H0-28 O0-12"),
        mass_range_ppm=kwargs.pop("mass_range_ppm", 2.0),
        **kwargs,
    )


def _glucose(results: list[dict]) -> list[dict]:
    return [r for r in results if r["formula"] == GLUCOSE]


@pytest.mark.parametrize(("notation", "ion", "charge"), SHAPES)
def test_each_shape_finds_the_molecule_at_its_own_mass(notation, ion, charge):
    mechanism = parse_ionization(notation)
    target = mechanism.ion_mz(GLUCOSE_MASS) * (1 + 1e-6)
    found = _glucose(find_compositions(target, _config(notation)))
    assert len(found) == 1
    (reading,) = found
    assert reading["ion"] == ion
    assert reading["ionization_mechanism"] == notation
    assert reading["neutral_mass"] == pytest.approx(GLUCOSE_MASS, abs=1e-6)
    # Signed against the ion's own m/z, as the targeted matcher signs it.
    assert reading["composition_error_ppm"] == pytest.approx(1.0, abs=1e-3)
    predicted = predict_isotopes(ion.rstrip("+-"), charge)[0][0]
    assert target / (1 + reading["composition_error_ppm"] * 1e-6) == pytest.approx(
        predicted, abs=1e-6
    )


def test_the_window_is_the_tolerance_in_the_ion_s_m_z():
    # 1.5 ppm off a doubly charged ion is 1.5 ppm in its m/z, which is 3 mDa
    # of a 2 kDa neutral; a window kept at the m/z's own width in neutral mass
    # would have lost it.
    mechanism = parse_ionization("[M+2H]2+")
    exact = mechanism.ion_mz(GLUCOSE_MASS)
    assert _glucose(find_compositions(exact * (1 + 1.9e-6), _config("[M+2H]2+")))
    assert not _glucose(find_compositions(exact * (1 + 2.1e-6), _config("[M+2H]2+")))


def test_a_neutral_that_cannot_lose_the_neutral_is_not_read():
    # A hydrocarbon has no water to lose, so it makes no [M+H-H2O]+ ion; read
    # anyway, its "ion" would be the formula with oxygen dropped.
    mechanism = parse_ionization("[M+H-H2O]+")
    target = mechanism.ion_mz(calculate_mass(formula="C10H16"))
    found = find_compositions(target, _config("[M+H-H2O]+", mass_range_ppm=5.0))
    assert "C10H16" not in {r["formula"] for r in found}
    assert all("O" in r["formula"] for r in found)


def test_the_reagent_ion_alone_is_read_only_through_one_molecule_that_loses_nothing():
    calcium = parse_ionization("[M+Ca]2+")
    (alone,) = find_compositions(calcium.ion_mz(0.0), _config("[M+Ca]2+"))
    assert (alone["formula"], alone["ion"]) == ("()", "Ca++")
    assert alone["composition_error_ppm"] == pytest.approx(0.0, abs=1e-6)
    # Two molecules of nothing, or nothing less a water, is no ion.
    for notation in ("[2M+Na]+", "[M+Na-H2O]+"):
        mechanism = parse_ionization(notation)
        found = find_compositions(mechanism.ion_mz(0.0), _config(notation))
        assert all(r["formula"] != "()" for r in found)


def test_a_shared_grid_answers_every_shape_as_its_own_window_does():
    # The banded grid holds each mechanism's own window in neutral mass: twice
    # the band's mass for a doubly charged channel, half of it for a dimer.
    notations = "[M+H]+,[M+2H]2+,[2M+H]+,[M+H-H2O]+"
    config = _config(notations, mass_range_ppm=5.0)
    mechanisms = [parse_ionization(name) for name in notations.split(",")]
    targets = [mechanism.ion_mz(GLUCOSE_MASS) for mechanism in mechanisms] + [301.1414]
    for low, high in neutral_mass_windows(targets, mechanisms, 5.0):
        assert low < high
    shared = build_neutral_grid(
        config, *neutral_mass_bounds(targets, mechanisms, config.mass_range_ppm)
    )
    banded = dict(grids_for_targets(np.array(sorted(targets)), config, mechanisms))
    for target in targets:
        alone = find_compositions(target, config)
        assert _glucose(alone) or target == 301.1414
        assert find_compositions(target, config, grid=shared) == alone
        assert find_compositions(target, config, grid=banded[target]) == alone


def test_the_bounds_span_every_mechanism_s_neutrals():
    mechanisms = [parse_ionization(name) for name in ("[M+2H]2+", "[2M+H]+")]
    low, high = neutral_mass_bounds([200.0, 300.0], mechanisms, 0.0)
    assert low == pytest.approx(mechanisms[1].neutral_mass(200.0))
    assert high == pytest.approx(mechanisms[0].neutral_mass(300.0))
    windows = neutral_mass_windows([200.0, 300.0], mechanisms, 0.0)
    assert windows[0] == pytest.approx(
        (mechanisms[0].neutral_mass(200.0), mechanisms[0].neutral_mass(300.0))
    )


def _envelope(ion: str, charge: int, scale: float = 1e7) -> pd.DataFrame:
    """A spectrum holding one ion's predicted lines."""
    mzs, probs, _ = predict_isotopes(ion, charge)
    probs = np.asarray(probs) / max(probs)
    keep = probs > 1e-3
    return pd.DataFrame(
        {
            "mz": np.asarray(mzs)[keep],
            "intensity": probs[keep] * scale,
            "signal_to_noise": probs[keep] * 1e4,
        }
    )


def test_a_doubly_charged_envelope_is_claimed_at_half_a_unit():
    peaks = _envelope("C6H14O6", 2)
    matches, _ = assign_compositions(peaks, _config("[M+2H]2+", mass_range_ppm=3.0))
    rows = matches[matches["formula"] == GLUCOSE].sort_values("mz")
    assert list(rows["isotope_label"])[:2] == ["M0", "13C"]
    monoisotopic, carbon13 = rows["mz"].iloc[0], rows["mz"].iloc[1]
    # The 13C line of a doubly charged ion is half a neutron's mass up.
    assert carbon13 - monoisotopic == pytest.approx(1.003355 / 2, abs=1e-4)
    assert rows["ion"].iloc[0] == "C6H14O6++"
    # In mass, a whole neutron's: the 13C line's neutral is 13C glucose.
    assert rows["neutral_mass"].iloc[0] == pytest.approx(GLUCOSE_MASS, abs=1e-6)
    assert rows["neutral_mass"].iloc[1] == pytest.approx(
        GLUCOSE_MASS + 1.003355, abs=1e-4
    )


def test_a_dimer_envelope_is_claimed_as_its_molecule():
    peaks = _envelope("C12H25O12", 1)
    matches, _ = assign_compositions(peaks, _config("[2M+H]+", mass_range_ppm=3.0))
    rows = matches[matches["formula"] == GLUCOSE].sort_values("mz")
    assert list(rows["isotope_label"])[:2] == ["M0", "13C"]
    assert rows["ion"].iloc[0] == "C12H25O12+"
    # The 13C line's heavy atom is in one of the two molecules: its neutral is
    # that 13C glucose, the gap taken whole rather than shared out.
    assert rows["neutral_mass"].iloc[0] == pytest.approx(GLUCOSE_MASS, abs=1e-6)
    assert rows["neutral_mass"].iloc[1] == pytest.approx(
        GLUCOSE_MASS + 1.003355, abs=1e-4
    )


def test_an_unreadable_mechanism_keeps_the_m_z_gap():
    assert _ion_charge("not a mechanism") == 1
    assert _ion_charge(None) == 1
    assert _ion_charge("[M+2H]2+") == 2
    assert _ion_charge("[M-2H]2-") == 2


def test_a_line_of_a_doubly_charged_ion_reads_as_that_line():
    # The composition search reads a peak as any line of a candidate's ion,
    # and a doubly charged ion's lines sit half a unit apart.
    mechanism = parse_ionization("[M+2H]2+")
    mzs, probs, labels = predict_isotopes("C6H14O6", 2)
    line = float(mzs[labels.index("13C")])
    found = [
        r
        for r in find_compositions(line, _config("[M+2H]2+"), isotopologue_floor=0.01)
        if r["formula"] == GLUCOSE
    ]
    assert len(found) == 1
    assert found[0]["isotope_mz"] == pytest.approx(line, abs=1e-6)
    assert found[0]["isotope_mz"] - mechanism.ion_mz(GLUCOSE_MASS) == pytest.approx(
        1.003355 / 2, abs=1e-4
    )


def test_a_dication_s_reagent_ion_is_read_within_the_window_of_its_m_z():
    # 1.5 ppm off the bare Ca2+ ion is 1.5 ppm of its m/z, though it is
    # 3 ppm of the neutral mass the window is cut in.
    calcium = parse_ionization("[M+Ca]2+")
    found = find_compositions(calcium.ion_mz(0.0) * (1 + 1.5e-6), _config("[M+Ca]2+"))
    assert [r["formula"] for r in found] == ["()"]


def test_a_dication_s_reagent_ion_is_read_at_another_of_its_lines():
    # 44Ca is four mass units up, two in the m/z of Ca2+.
    calcium = parse_ionization("[M+Ca]2+")
    mzs, _, labels = predict_isotopes("Ca", 2)
    line = float(mzs[labels.index("44Ca")])
    gap = calculate_mass(formula="Ca[44]") - calculate_mass(formula="Ca")
    assert line - calcium.ion_mz(0.0) == pytest.approx(gap / 2, abs=1e-6)
    found = find_compositions(line, _config("[M+Ca]2+"), isotopologue_floor=0.01)
    (alone,) = [r for r in found if r["formula"] == "()"]
    assert alone["isotope_mz"] == pytest.approx(line, abs=1e-6)


def test_a_line_is_predicted_of_the_ion_not_of_the_molecule():
    # Protonated and stripped of HCl, a chloride keeps no chlorine, so its ion
    # has no 37Cl line two units up for a peak there to be.
    mechanism = parse_ionization("[M+H-HCl]+")
    neutral = calculate_mass(formula="C4H7ClO2")
    line = mechanism.ion_mz(neutral) + (
        calculate_mass(formula="Cl[37]") - calculate_mass(formula="Cl")
    )
    config = _config("[M+H-HCl]+", element_count_ranges="C0-8 H0-16 Cl0-1 O0-4")
    found = find_compositions(line, config, isotopologue_floor=0.01)
    assert "C4H7ClO2" not in {r["formula"] for r in found}
    # Its 13C line is there.
    carbon = mechanism.ion_mz(neutral) + 1.003355
    found = find_compositions(carbon, config, isotopologue_floor=0.01)
    assert "C4H7ClO2" in {r["formula"] for r in found}


def test_the_atoms_an_ion_may_hold_are_its_molecules_and_its_mass_s():
    from mascope_tools.composition.isotopologues import _ion_count_ranges

    box = _config("[2M+H]+", element_count_ranges="C0-6 H0-12 O0-6")
    ranges = _ion_count_ranges(box, parse_ionization("[2M+H]+"), 400.0)
    assert ranges["C"] == (0, 12)
    assert ranges["H"] == (1, 25)
    # An ion of charge two weighs twice its m/z: at m/z 100, 17 carbons.
    wide = _config("[M+2H]2+", element_count_ranges="C0-30 H0-60 O0-6")
    ranges = _ion_count_ranges(wide, parse_ionization("[M+2H]2+"), 100.0)
    assert ranges["C"] == (0, 17)


def test_a_line_s_window_is_its_offset_over_the_charge():
    from mascope_tools.composition.isotopologues import isotopologue_windows

    mechanism = parse_ionization("[M+2H]2+")
    ((low, high),) = isotopologue_windows(
        100.0, mechanism, {1: (1.0, 1.0)}, tolerance_da=0.0
    )
    assert low == pytest.approx(high)
    assert low == pytest.approx(mechanism.neutral_mass(100.0 - 0.5))


def test_the_series_of_a_dication_steps_at_its_own_m_z():
    from mascope_tools.composition.series import ion_mz

    mechanism = parse_ionization("[M+2H]2+")
    assert ion_mz({"C": 6, "H": 12, "O": 6}, "[M+2H]2+") == pytest.approx(
        mechanism.ion_mz(GLUCOSE_MASS)
    )


def test_an_ion_s_labels_are_read_whatever_its_charge():
    from IsoSpecPy import IsoThreshold

    from mascope_tools.composition.heuristic_filter import extract_isotope_labels

    predicted = IsoThreshold(formula="C6H14O6", threshold=0.01, get_confs=True)
    assert extract_isotope_labels("C6H14O6++", predicted) == extract_isotope_labels(
        "C6H14O6", predicted
    )


def test_a_dimer_s_line_is_read_of_its_two_molecules():
    # Two bromomethanes hold two bromines: their dimer's 81Br2 line, four
    # units up, is one no single molecule of them has.
    mechanism = parse_ionization("[2M+H]+")
    neutral = calculate_mass(formula="CH3Br")
    line = mechanism.ion_mz(neutral) + 2 * (
        calculate_mass(formula="Br[81]") - calculate_mass(formula="Br")
    )
    config = _config("[2M+H]+", element_count_ranges="C0-4 H0-10 Br0-1")
    found = [
        r
        for r in find_compositions(line, config, isotopologue_floor=0.01)
        if r["formula"] == "CH3Br"
    ]
    assert len(found) == 1
    assert found[0]["isotope_mz"] == pytest.approx(line, abs=1e-6)
    assert found[0]["ion"] == "C2H7Br2+"
