import json
import os

import pytest

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    TARGET_ISOTOPE_FORMULA_LENGTH,
    charge_string,
    fit_isotope_names,
    generate_target_ions_from_composition,
    group_target_isotopes,
)
from mascope_backend.db import (
    IonizationMechanism,
    TargetCompound,
    TargetIon,
    TargetIsotope,
)
from mascope_tools.composition import heuristic_filter
from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.mechanism_notation import parse_mechanism
from mascope_tools.composition.utils import calculate_mass


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

    # "++" is a mechanism in neither notation, so it yields no ion for any
    # compound - and generation still terminates.
    assert target_ions == []
    assert target_isotopes == []


@pytest.mark.parametrize(
    ("legacy", "standard"),
    [("+H+", "[M+H]+"), ("-H+", "[M-H]-"), ("-H-", "[M-H]+"), ("+", "[M]+.")],
)
def test_a_row_still_holding_the_legacy_spelling_makes_the_same_ions(legacy, standard):
    """A row the migration has not rewritten yet is the same mechanism."""
    compound = TargetCompound(
        target_compound_id="unit-legacy", target_compound_formula="C7H8"
    )

    def ions(notation):
        mechanism = IonizationMechanism(
            ionization_mechanism_id="unit-mech",
            ionization_mechanism_polarity=parse_mechanism(standard).polarity,
            ionization_mechanism=notation,
        )
        target_ions, target_isotopes = generate_target_ions_from_composition(
            compound, [mechanism]
        )
        return (
            [ion.target_ion_formula for ion in target_ions],
            sorted(isotope.mz for isotope in target_isotopes),
        )

    assert ions(legacy) == ions(standard)
    assert ions(standard)[0]


def test_group_target_isotopes_terminates_on_nonpositive_mz():
    """Grouping must terminate even for m/z <= 0 (degenerate input)."""
    masses, probs, formulae = group_target_isotopes(
        [-0.000549, 0.0, 18.0106], [0.5, 0.2, 0.3], ["a", "b", "c"], 1e4
    )
    assert len(masses) == len(probs) == len(formulae) == 3
    # Each isotope is a line of its own, none lost or emptied.
    assert masses == [-0.000549, 0.0, 18.0106]
    assert probs == [0.5, 0.2, 0.3]
    assert formulae == ["a", "b", "c"]


def test_group_target_isotopes_joins_the_formulae_of_a_line():
    """Isotopologues in one bin share a line, labelled by all their names."""
    _, _, formulae = group_target_isotopes(
        [100.0, 100.001, 200.0], [0.5, 0.2, 0.3], ["a", "b", "c"], 1e4
    )
    assert formulae == ["a/b", "c"]


def test_group_target_isotopes_starts_a_line_at_its_most_abundant_isotope():
    """A dim isotope under the bright one does not start the line and leave
    the dim one over it behind: from the bright isotope, half a FWHM (5 mDa
    at m/z 100) either side is one line."""
    masses, probs, formulae = group_target_isotopes(
        [100.0, 100.0045, 100.008], [0.05, 0.9, 0.05], ["a", "b", "c"], 1e4
    )

    assert formulae == ["a/b/c"]
    assert probs == [pytest.approx(1.0)]
    assert masses == [pytest.approx(0.05 * 100.0 + 0.9 * 100.0045 + 0.05 * 100.008)]


def test_group_target_isotopes_returns_its_lines_in_mz_order():
    """The lines come in m/z order, each naming its isotopes in m/z order,
    whichever is the most abundant."""
    masses, probs, formulae = group_target_isotopes(
        [201.0, 200.0, 200.001, 199.0], [0.1, 0.2, 1.0, 0.3], ["d", "b", "c", "a"], 1e4
    )

    assert formulae == ["a", "b/c", "d"]
    assert masses == sorted(masses)
    assert probs == [pytest.approx(0.3), pytest.approx(1.2), pytest.approx(0.1)]


def test_a_tof_line_holds_the_whole_m1_cluster_of_a_light_nitrogen_ion():
    """The TOF line at the 13C position of C4H10NO5- holds the 15N, 17O and
    2H isotopes as well (mascope#2587). Binned from the lightest, the 15N
    isotope 6 mDa under the 13C one, the 2H isotope 3 mDa over the 13C one
    fell out of the 7.6 mDa bin and became a line of its own."""
    compound = TargetCompound(
        target_compound_id="unit-m1", target_compound_formula="C4H11NO5"
    )
    mechanism = IonizationMechanism(
        ionization_mechanism_id="unit-mech-h",
        ionization_mechanism_polarity="-",
        ionization_mechanism="[M-H]-",
    )
    _, target_isotopes = generate_target_ions_from_composition(compound, [mechanism])
    high = {
        i.target_isotope_formula: i for i in target_isotopes if i.resolution == "HIGH"
    }
    low = [i for i in target_isotopes if i.resolution == "LOW"]
    members = [
        "[15N]C4H10O5-",
        "[13C]C3H10NO5-",
        "[17O]C4H10NO4-",
        "[2H]C4H9NO5-",
    ]

    (m1,) = [i for i in low if "[13C]C3H10NO5-" in i.target_isotope_formula.split("/")]

    assert m1.target_isotope_formula.split("/") == members
    assert m1.relative_abundance == pytest.approx(
        sum(high[name].relative_abundance for name in members)
    )
    # No other line holds a name of the cluster.
    others = [i.target_isotope_formula.split("/") for i in low if i is not m1]
    assert not any(name in names for names in others for name in members)


def test_group_target_isotopes_folds_a_dim_line_it_cannot_separate():
    """A line under 1 % of the most abundant, between half and one FWHM (10
    mDa at m/z 100) from a line over it, is added to that line, as the
    envelope match folds it. A dim line a FWHM or more away, and a line over
    the floor however near, stay lines of their own."""
    masses, probs, formulae = group_target_isotopes(
        [100.0, 99.993, 101.0, 101.0102, 102.0, 102.007],
        [1.0, 0.005, 0.1, 0.005, 0.05, 0.02],
        ["a", "b", "c", "d", "e", "f"],
        1e4,
    )

    # The names stay in m/z order, the dim line's first.
    assert formulae == ["b/a", "c", "d", "e", "f"]
    assert probs == pytest.approx([1.005, 0.1, 0.005, 0.05, 0.02])
    assert masses[0] == pytest.approx((100.0 + 0.005 * 99.993) / 1.005)
    assert masses[1:] == pytest.approx([101.0, 101.0102, 102.0, 102.007])


def test_group_target_isotopes_reads_the_floor_off_the_most_abundant_line():
    """The floor is 1 % of the most abundant line as grouped, not of any
    single isotope: a line at 0.9 % of one isotope but over 1 % of nothing
    brighter is kept."""
    _, probs, formulae = group_target_isotopes(
        [100.0, 100.007], [0.6, 0.0099], ["a", "b"], 1e4
    )

    assert formulae == ["a", "b"]
    assert probs == pytest.approx([0.6, 0.0099])


def test_a_tof_line_folds_the_15n_line_of_a_light_nitrogen_ion():
    """Under about m/z 126 the 15N line of the M+1 cluster sits more than
    half a FWHM from the 13C line, so it is a group of its own: 6.4 mDa under
    it for C3H8NO+, where half a FWHM is 3.8 mDa. It never matches a peak
    there, and the 13C line alone read the whole peak, about 7 % low. Folded
    in, the TOF line reads what the envelope match predicts at the same depth
    (predict_resolved_isotopes, mascope#2591)."""
    compound = TargetCompound(
        target_compound_id="unit-m1-light", target_compound_formula="C3H7NO"
    )
    mechanism = IonizationMechanism(
        ionization_mechanism_id="unit-mech-h-plus",
        ionization_mechanism_polarity="+",
        ionization_mechanism="[M+H]+",
    )
    _, target_isotopes = generate_target_ions_from_composition(compound, [mechanism])
    low = sorted(
        (i for i in target_isotopes if i.resolution == "LOW"), key=lambda i: i.mz
    )
    m0 = max(i.relative_abundance for i in low)

    (m1,) = [i for i in low if "[13C]C2H8NO+" in i.target_isotope_formula.split("/")]
    assert "[15N]C3H8O+" in m1.target_isotope_formula.split("/")
    assert not any(
        "[15N]C3H8O+" in i.target_isotope_formula.split("/") for i in low if i is not m1
    )

    # The library keeps lines to 0.001 % of the parent, the depth the envelope
    # match predicts members to at its 1 % floor (mascope#3024), so the two
    # read the same M+1 line, its 2H and 17O lines included.
    mzs, intensities, labels = heuristic_filter.predict_resolved_isotopes(
        "C3H8NO", 1, 0.01, lambda mz: 1e4
    )
    envelope = dict(zip(labels, zip(mzs, intensities / intensities.max())))
    assert m1.relative_abundance / m0 == pytest.approx(envelope["13C"][1], rel=1e-3)
    assert m1.mz == pytest.approx(envelope["13C"][0], abs=1e-4)


def test_group_target_isotopes_keeps_the_most_abundant_names_that_fit():
    """A line merging more names than the column holds keeps the most abundant
    whole names, in m/z order, rather than failing the insert of the ion."""
    name = "x" * 99
    count = TARGET_ISOTOPE_FORMULA_LENGTH // 100 + 10
    masses = [100.0 + i * 1e-6 for i in range(count)]
    # The least abundant names come first by m/z, so keeping from the front
    # would keep the wrong ones.
    probs = [float(i + 1) for i in range(count)]
    formulae = [f"{i:03d}{name}"[:100] for i in range(count)]

    _, _, (label,) = group_target_isotopes(masses, probs, formulae, 1e4)

    assert len(label) <= TARGET_ISOTOPE_FORMULA_LENGTH
    kept = label.split("/")
    assert kept == sorted(kept)
    assert kept == formulae[count - len(kept) :]
    # Every name is 100 characters, plus a separator after all but the last.
    assert len(kept) == (TARGET_ISOTOPE_FORMULA_LENGTH + 1) // 101


def test_fit_isotope_names_stops_at_the_first_name_that_does_not_fit():
    """A shorter, less abundant name never takes the place of a more abundant
    one that did not fit: the label is the most abundant names, in order."""
    names = ["dddd", "aaaaaaaa", "bbbbbbbbbbbbbbb", "cc"]
    abundances = [0.1, 0.5, 0.3, 0.05]

    assert fit_isotope_names(names, 20, abundances) == "aaaaaaaa"
    # Without abundances the names are taken from the front.
    assert fit_isotope_names(names, 20) == "dddd/aaaaaaaa"
    assert fit_isotope_names(names, 100, abundances) == "/".join(names)


def test_fit_isotope_names_cuts_a_first_name_longer_than_the_limit():
    """A label is never left empty: a most abundant name that alone runs past
    the limit is cut to it."""
    assert fit_isotope_names(["x" * 30, "y" * 30], 20, [0.4, 0.6]) == "y" * 20
    assert fit_isotope_names(["x" * 30, "y" * 30], 20) == "x" * 20


def test_a_large_halogenated_ion_fits_its_isotope_formulae_to_the_column():
    """A bromide-adduct mechanism on a large chlorinated, sulfur-bearing
    compound merges enough isotopologues into a low resolution line to run
    past the column; the mechanism then could not be created at all."""
    compound = TargetCompound(
        target_compound_id="unit-large", target_compound_formula="C50H70Cl4N10O10S2"
    )
    mechanism = IonizationMechanism(
        ionization_mechanism_id="unit-mech",
        ionization_mechanism_polarity="-",
        ionization_mechanism="[M+Br2]-",
    )

    _, target_isotopes = generate_target_ions_from_composition(compound, [mechanism])

    low = [i.target_isotope_formula for i in target_isotopes if i.resolution == "LOW"]
    assert low
    assert max(len(formula) for formula in low) <= TARGET_ISOTOPE_FORMULA_LENGTH
    high = {i.target_isotope_formula for i in target_isotopes if i.resolution == "HIGH"}
    named = {name for formula in low for name in formula.split("/")}
    # Every high resolution isotopologue falls in exactly one line, so a label
    # set that names fewer of them than there are is the cap at work: this
    # compound does run past the column, and the test is not passing vacuously.
    assert named < high


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
        ionization_mechanism="[M+H]+",
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
            "[M+H]+",
            "C8H25O4Si4+",
            297.08244,
            {"[29Si]C8H25O4Si3+": 298.08201, "[30Si]C8H25O4Si3+": 299.07929},
        ),
        # Triethyl phosphate: phosphorus is monoisotopic, so only carbon's line.
        ("C6H15O4P", "[M+H]+", "C6H16O4P+", 183.07807, {"[13C]C5H16O4P+": 184.08143}),
        # Chlorpyrifos: three chlorines and a sulfur.
        (
            "C9H11Cl3NO3PS",
            "[M+H]+",
            "C9H12Cl3NO3PS+",
            349.93356,
            {"[37Cl]C9H12Cl2NO3PS+": 351.93061, "[34S]C9H12Cl3NO3P+": 351.92936},
        ),
        # Trifluoroacetic acid through the bromide channel.
        ("C2HF3O2", "[M+Br]-", "C2HBrF3O2-", 192.91175, {"[81Br]C2HF3O2-": 194.90970}),
        # Iodic acid: iodine is monoisotopic too.
        ("HIO3", "[M-H]-", "IO3-", 174.88977, {}),
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


def _ions_of(compound_formula: str, notation: str):
    compound = TargetCompound(
        target_compound_id="unit-shapes", target_compound_formula=compound_formula
    )
    mechanism = IonizationMechanism(
        ionization_mechanism_id="unit-mech",
        ionization_mechanism_polarity=parse_mechanism(notation).polarity,
        ionization_mechanism=notation,
    )
    return generate_target_ions_from_composition(compound, [mechanism])


_GLUCOSE = calculate_mass(formula="C6H12O6")
_H = calculate_mass(formula="H")


@pytest.mark.parametrize(
    ("notation", "ion_formula", "mz"),
    [
        # Of n molecules M, the ion holds n M and its m/z is its mass over
        # its charge.
        ("[2M+H]+", "C12H25O12+", 2 * _GLUCOSE + _H - ELECTRON_MASS),
        ("[2M-H]-", "C12H23O12-", 2 * _GLUCOSE - _H + ELECTRON_MASS),
        ("[M+2H]2+", "C6H14O6++", (_GLUCOSE + 2 * _H - 2 * ELECTRON_MASS) / 2),
        ("[M-2H]2-", "C6H10O6--", (_GLUCOSE - 2 * _H + 2 * ELECTRON_MASS) / 2),
        (
            "[M+H-H2O]+",
            "C6H11O5+",
            _GLUCOSE + _H - ELECTRON_MASS - calculate_mass(formula="H2O"),
        ),
        (
            "[2M+2H-H2O]2+",
            "C12H24O11++",
            (2 * _GLUCOSE + 2 * _H - 2 * ELECTRON_MASS - calculate_mass(formula="H2O"))
            / 2,
        ),
    ],
)
def test_the_new_shapes_make_their_ions(notation, ion_formula, mz):
    target_ions, target_isotopes = _ions_of("C6H12O6", notation)
    assert [ion.target_ion_formula for ion in target_ions] == [ion_formula]
    monoisotopic = min(
        (isotope for isotope in target_isotopes if isotope.resolution == "HIGH"),
        key=lambda isotope: isotope.mz,
    )
    assert monoisotopic.mz == pytest.approx(mz, abs=1e-6)
    assert monoisotopic.target_isotope_formula.endswith(ion_formula[-2:])
    assert_isotope_links(target_ions, target_isotopes)


def test_the_ion_formula_of_a_doubly_charged_ion_strips_back_to_its_atoms():
    # A count before the sign would read as the last element's: C6H14O62+.
    (ion,), _ = _ions_of("C6H12O6", "[M+2H]2+")
    assert ion.target_ion_formula.rstrip("+-") == "C6H14O6"
    assert charge_string(2) == "++"
    assert charge_string(-3) == "---"


@pytest.mark.parametrize(
    ("compound_formula", "notation"),
    [
        # A removal or a neutral loss the molecule has not the atoms for.
        ("H2O", "[M-CH3]+"),
        ("CO2", "[M-2H]2-"),
        ("C7H8", "[M+H-H2O]+"),
        ("CH4", "[M+Na-CO2]+"),
        # The empty formula is the reagent alone: no molecule to multiply
        # or to lose a neutral from.
        ("()", "[2M+H]+"),
        ("()", "[M+H-H2O]+"),
    ],
)
def test_a_shape_that_cannot_apply_makes_no_ion(compound_formula, notation):
    assert _ions_of(compound_formula, notation) == ([], [])


def test_a_neutral_loss_is_taken_from_the_ion_the_species_made():
    # The water of [M+H-H2O]+ needs one H more than the molecule holds,
    # which the proton brings: H2O from HO plus H.
    (ion,), _ = _ions_of("CO2H", "[M+H-H2O]+")
    assert ion.target_ion_formula == "CO+"
