import json
import os

import pytest

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
    group_target_isotopes,
)
from mascope_backend.db import (
    IonizationMechanism,
    TargetCompound,
    TargetIon,
    TargetIsotope,
)


HERE = os.path.dirname(__file__)
with open(os.path.join(HERE, "ions.json"), "r") as f:
    EXPECTED_IONS = json.load(f)
with open(os.path.join(HERE, "isotopes.json"), "r") as f:
    EXPECTED_ISOTOPES = json.load(f)


def summarize_isotopes(target_isotopes: list[TargetIsotope]) -> dict[str, dict]:
    """Create a summary per ion: monoisotopic m/z, relative abundance, and count."""

    grouped: dict[str, list[TargetIsotope]] = {}
    for isotope in target_isotopes:
        grouped.setdefault(isotope.target_ion_id, []).append(isotope)

    summary: dict[str, dict] = {}
    for ion_id, isotopes in grouped.items():
        M0 = max(isotopes, key=lambda iso: iso.relative_abundance)
        summary[ion_id] = {
            "mz_M0": M0.mz,
            "ra_M0": M0.relative_abundance,
            "num_isotopes": len(isotopes),
        }

    return summary


def assert_isotope_links(
    target_ions: list[TargetIon], target_isotopes: list[TargetIsotope]
) -> None:
    """Ensure isotopes and ions reference each other correctly."""

    ion_ids = {ion.target_ion_id for ion in target_ions}
    assert all(isotope.target_ion_id in ion_ids for isotope in target_isotopes)
    isotope_ids = {isotope.target_ion_id for isotope in target_isotopes}
    assert all(ion.target_ion_id in isotope_ids for ion in target_ions)


@pytest.mark.asyncio
async def test_generate_target_ions_from_composition(
    test_target_compounds_by_composition: list[TargetCompound],
    test_ionization_mechanisms: list[IonizationMechanism],
) -> None:
    """Test generating target ions from target compounds defined by composition.

    :param test_target_compounds_by_composition: List of target compounds defined by composition.
    :type test_target_compounds_by_composition: list[TargetCompound]
    :param test_ionization_mechanisms: List of ionization mechanisms to use for generating target ions.
    :type test_ionization_mechanisms: list[IonizationMechanism]

    :return: None
    """
    for target_compound in test_target_compounds_by_composition:
        target_ions, target_isotopes = generate_target_ions_from_composition(
            target_compound, test_ionization_mechanisms
        )

        # Validate ion formulas against pre-computed expectations
        expected_ions = EXPECTED_IONS[target_compound.target_compound_formula]
        actual_ions = [ion.target_ion_formula for ion in target_ions]
        assert sorted(actual_ions) == sorted(expected_ions)

        # Map ion ids to formulas for isotope checks
        ion_id_to_formula = {
            ion.target_ion_id: ion.target_ion_formula for ion in target_ions
        }

        # Build isotope summaries keyed by ion formula
        isotope_summary_by_formula: dict[str, dict] = {}
        for ion_id, summary in summarize_isotopes(target_isotopes).items():
            isotope_summary_by_formula[ion_id_to_formula[ion_id]] = summary

        expected_isotopes = {
            ion_formula: EXPECTED_ISOTOPES[ion_formula] for ion_formula in expected_ions
        }

        # Validate isotope formulas match expectations
        assert set(isotope_summary_by_formula.keys()) == set(expected_isotopes.keys())

        # Validate isotope summaries
        for ion_formula, actual in isotope_summary_by_formula.items():
            expected = expected_isotopes[ion_formula]
            assert actual["num_isotopes"] == expected["num_isotopes"]
            assert actual["mz_M0"] == pytest.approx(
                expected["mz_M0"], rel=1e-6, abs=1e-6
            )
            assert actual["ra_M0"] == pytest.approx(
                expected["ra_M0"], rel=1e-6, abs=1e-6
            )

        # Link integrity
        assert_isotope_links(target_ions, target_isotopes)


@pytest.mark.parametrize("compound_formula", ["()", "H2O"])
def test_empty_modification_mechanism_yields_no_atomless_ions(compound_formula):
    """An empty-modification mechanism ("++") must not produce atomless ions.

    The pydantic validator rejects "++" at the API, but rows created before
    that guard (or built directly) still reach ion generation. An atomless ion
    used to reach group_target_isotopes with m/z ~ -0.00055 (electron mass
    correction of an empty pattern), whose non-positive bin width sent the
    grouping loop spinning forever, hanging the whole worker.
    """
    compound = TargetCompound(
        target_compound_id="unit-empty-mech",
        target_compound_formula=compound_formula,
    )
    mechanism = IonizationMechanism(
        ionization_mechanism_id="unit-mech-plusplus",
        ionization_mechanism_polarity="+",
        ionization_mechanism="++",
    )

    target_ions, target_isotopes = generate_target_ions_from_composition(
        compound, [mechanism]
    )

    if compound_formula == "()":
        # "++" adds nothing to an empty compound: no atoms, no ion
        assert target_ions == []
        assert target_isotopes == []
    else:
        # For a real compound "++" degenerates to electron abstraction and
        # must still terminate and yield a well-formed ion
        assert [ion.target_ion_formula for ion in target_ions] == ["H2O+"]
        assert target_isotopes


def test_group_target_isotopes_terminates_on_nonpositive_mz():
    """Grouping must terminate even for m/z <= 0 (degenerate input)."""
    masses, probs, formulae = group_target_isotopes(
        [-0.000549, 0.0, 18.0106], [0.5, 0.2, 0.3], ["a", "b", "c"], 1e4
    )
    assert len(masses) == len(probs) == len(formulae) == 3


@pytest.mark.parametrize("bad_formula", ["xyz", "136.1252", "Zz", "^C"])
def test_invalid_compound_formula_yields_no_ions(bad_formula):
    """An invalid compound formula must produce no ions (and never raise).

    parse_composition silently drops unrecognised characters, so without the
    up-front validity check these would either create bogus adduct-only ions or
    make IsoSpecPy raise. Both are guarded: the compound is skipped.
    """
    compound = TargetCompound(
        target_compound_id="unit-invalid",
        target_compound_formula=bad_formula,
    )
    mechanism = IonizationMechanism(
        ionization_mechanism_id="unit-mech",
        ionization_mechanism_polarity="+",
        ionization_mechanism="+H+",
    )

    target_ions, target_isotopes = generate_target_ions_from_composition(
        compound, [mechanism]
    )

    assert target_ions == []
    assert target_isotopes == []


@pytest.mark.parametrize(
    "formula, notation, ion_formula, mz_m0, lines",
    [
        # D4: the 29Si and 30Si lines are what attach a column-bleed siloxane's
        # envelope to its parent.
        (
            "C8H24O4Si4",
            "+H+",
            "C8H25O4Si4+",
            297.08244,
            {"[29Si]C8H25O4Si3+": 298.08201, "[30Si]C8H25O4Si3+": 299.07929},
        ),
        # Triethyl phosphate: phosphorus is monoisotopic, so only carbon's line.
        ("C6H15O4P", "+H+", "C6H16O4P+", 183.07807, {"[13C]C5H16O4P+": 184.08143}),
        # Chlorpyrifos: three chlorines and a sulfur.
        (
            "C9H11Cl3NO3PS",
            "+H+",
            "C9H12Cl3NO3PS+",
            349.93356,
            {"[37Cl]C9H12Cl2NO3PS+": 351.93061, "[34S]C9H12Cl3NO3P+": 351.92936},
        ),
        # Trifluoroacetic acid through the bromide channel.
        ("C2HF3O2", "+Br-", "C2HBrF3O2-", 192.91175, {"[81Br]C2HF3O2-": 194.90970}),
        # Iodic acid: iodine is monoisotopic too.
        ("HIO3", "-H+", "IO3-", 174.88977, {}),
    ],
    ids=["D4", "triethyl-phosphate", "chlorpyrifos", "TFA", "iodic-acid"],
)
def test_the_silicon_phosphorus_and_halogen_families_generate_their_lines(
    formula, notation, ion_formula, mz_m0, lines
):
    """The reference lists bring these elements into Stage A, and the known set
    is built through this function, so each family's ion and its isotopologue
    lines have to come out right."""
    compound = TargetCompound(
        target_compound_id="unit-families", target_compound_formula=formula
    )
    mechanism = IonizationMechanism(
        ionization_mechanism_id="unit-mech",
        ionization_mechanism_polarity=notation[-1],
        ionization_mechanism=notation,
    )

    target_ions, target_isotopes = generate_target_ions_from_composition(
        compound, [mechanism]
    )

    assert [ion.target_ion_formula for ion in target_ions] == [ion_formula]
    high = {
        isotope.target_isotope_formula: isotope.mz
        for isotope in target_isotopes
        if isotope.resolution == "HIGH"
    }
    assert high[ion_formula] == pytest.approx(mz_m0, abs=1e-4)
    for line, mz in lines.items():
        assert high[line] == pytest.approx(mz, abs=1e-4)
