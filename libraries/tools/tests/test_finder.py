from dataclasses import replace

import pandas as pd
import pytest

from mascope_tools.composition.exceptions import CompositionFinderException
from mascope_tools.composition.finder import (
    _other_candidate_formulas,
    assign_compositions,
    find_compositions,
    neutral_mass_bounds,
    process_isotopes,
    replace_atom_with_isotope,
)
from mascope_tools.composition.grid import build_neutral_grid
from mascope_tools.composition.models import CompositionSearchConfig
from mascope_tools.composition.utils import (
    combine_formula_and_ionization,
    parse_atom_count_ranges,
    parse_ionization,
    to_hill_order,
)


# Protonated glucose, C6H12O6 + H+ (approximate; the exact prediction comes back
# on the search result, so nothing here depends on this literal's last digits).
PROTONATED_GLUCOSE_MZ = 181.070664


def test_replace_atom_with_isotope():
    """Test the replace_atom_with_isotope function with various cases."""
    # ion, isotope label, expected output
    test_cases = [
        ("C6H12O6+", "13C", "[13C]C5H12O6+"),  # single carbon replacement
        ("C6H12O6+", "13C3", "[13C]3C3H12O6+"),  # multiple carbon replacement
        (
            "C6H12O6+",
            "13C3+2H",
            "[13C]3[2H]C3H11O6+",
        ),  # carbon and hydrogen replacement
        (
            "C6H12O6+",
            "13C3+2H2",
            "[13C]3[2H]2C3H10O6+",
        ),  # carbon and multiple hydrogen replacement
    ]
    for ion, isotope_label, expected in test_cases:
        result = replace_atom_with_isotope(ion, isotope_label)
        assert result == expected, (
            f"replace_atom_with_isotope({ion}, {isotope_label}) = {result}, expected {expected}"
        )

    # Cases that should raise ValueError
    error_cases = [
        ("C6H12O6+", "15N"),  # no nitrogen in formula
        ("C6H12O6+", "Ultra"),  # invalid isotope label
    ]
    for ion, isotope_label in error_cases:
        with pytest.raises(ValueError):
            replace_atom_with_isotope(ion, isotope_label)


def test_parse_atom_count_ranges_accepts_bracket_first_isotopes():
    atoms = parse_atom_count_ranges("C0-50 H0-100 [15N]0-1 [13C]0-2")
    symbols = [atom.symbol for atom in atoms]

    assert symbols == ["C", "H", "[15N]", "[13C]"]
    assert atoms[2].min_count == 0
    assert atoms[2].max_count == 1
    assert atoms[3].min_count == 0
    assert atoms[3].max_count == 2


def test_parse_atom_count_ranges_rejects_legacy_element_first_isotopes():
    with pytest.raises(CompositionFinderException, match="Invalid isotope format"):
        parse_atom_count_ranges("C0-50 H0-100 N[15]0-1")


def test_parse_atom_count_ranges_rejects_malformed_tokens():
    with pytest.raises(CompositionFinderException, match="Invalid element count range"):
        parse_atom_count_ranges("C0-50 [15]0-1")


def test_assign_compositions_no_matches():
    """assign_compositions should not raise when no peaks match any composition."""
    # Use m/z values far outside what C0-2 H0-2 can produce with H+ ionization
    peaks = pd.DataFrame({"mz": [9999.0, 9998.0], "intensity": [100.0, 100.0]})
    config = CompositionSearchConfig(
        ionizations="H+",
        element_count_ranges="C0-2 H0-2",
        mass_range_ppm=5.0,
    )
    matches, log_messages = assign_compositions(peaks, config)

    assert isinstance(matches, pd.DataFrame)
    assert len(matches) == 2
    assert "mz" in matches.columns
    assert "formula" in matches.columns
    assert "ion" in matches.columns
    assert "isotope_label" in matches.columns
    assert (matches["formula"] == "---").all()
    assert (matches["ion"] == "---").all()


def test_to_hill_order_places_isotopes_first():
    formula = to_hill_order({"O": 3, "[15N]": 1})
    assert formula == "[15N]O3"


def test_to_hill_order_places_isotope_before_same_plain_element():
    formula = to_hill_order({"C": 5, "[13C]": 1, "H": 12, "O": 6})
    assert formula == "[13C]C5H12O6"


def test_to_hill_order_normalizes_element_first_isotope_keys():
    formula = to_hill_order({"O": 3, "N[15]": 1})
    assert formula == "[15N]O3"


def test_to_hill_order_keeps_standard_hill_order_for_regular_elements():
    formula = to_hill_order({"H": 12, "O": 6, "C": 6})
    assert formula == "C6H12O6"


def test_combine_formula_and_ionization_accepts_isotope_formula():
    ionization_mechanism = parse_ionization("+")
    ion_formula = combine_formula_and_ionization("[15N]O3", ionization_mechanism)
    assert ion_formula == "[15N]O3+"


def _protonated_glucose_search(shift_ppm: float):
    """Search a peak sitting `shift_ppm` off protonated glucose's exact m/z."""
    config = CompositionSearchConfig(
        ionizations="+H+",
        element_count_ranges="C0-10 H0-20 O0-10",
        mass_range_ppm=5.0,
    )
    target_mz = PROTONATED_GLUCOSE_MZ * (1 + shift_ppm * 1e-6)
    results = [
        r for r in find_compositions(target_mz, config) if r["formula"] == "C6H12O6"
    ]
    assert len(results) == 1
    predicted_mz = results[0]["neutral_mass"] + parse_ionization("+H+").mass
    return target_mz, predicted_mz, results[0]


def test_composition_error_ppm_is_signed_observed_minus_predicted():
    # A peak 3 ppm BELOW the composition it matches must report -3 ppm. The
    # engine persists this as PeakAssignment.mz_error_ppm whenever a row has no
    # isotope-envelope error, next to the targeted stage's signed match_mz_error,
    # and the UI both shows it and recovers the predicted m/z from it.
    _, _, low = _protonated_glucose_search(-3.0)
    assert low["composition_error_ppm"] == pytest.approx(-3.0, abs=1e-2)

    _, _, high = _protonated_glucose_search(3.0)
    assert high["composition_error_ppm"] == pytest.approx(3.0, abs=1e-2)


def test_predicted_mz_is_recoverable_from_composition_error_ppm():
    # The chart recovers theoretical_mz = observed_mz / (1 + mz_error_ppm/1e6);
    # that is exact only because the ppm error is signed AND relative to the
    # prediction rather than to the observation.
    for shift_ppm in (-3.0, 3.0):
        target_mz, predicted_mz, result = _protonated_glucose_search(shift_ppm)
        recovered = target_mz / (1 + result["composition_error_ppm"] / 1e6)
        assert recovered == pytest.approx(predicted_mz, rel=1e-12)


def test_a_shared_grid_answers_a_peak_the_same_as_its_own_window_does():
    # `assign_compositions` enumerates one grid for the whole spectrum and hands
    # it to every peak; a peak searched alone builds a grid over its own window.
    # Those are the same search over the same box, and the second is the fallback
    # for a box too wide to hold - so a disagreement between them would be a
    # spectrum answered differently for a reason that is not chemistry.
    config = CompositionSearchConfig(
        ionizations="+H+,+Na+",
        element_count_ranges="C0-20 H0-40 N0-3 O0-10",
        mass_range_ppm=10.0,
    )
    mechanisms = [parse_ionization(name) for name in ("+H+", "+Na+")]
    targets = [181.0707, 203.0526, 301.1414, 365.1054]
    shared = build_neutral_grid(
        config, *neutral_mass_bounds(targets, mechanisms, config.mass_range_ppm)
    )
    assert shared is not None

    for target in targets:
        with_shared = find_compositions(target, config, grid=shared)
        alone = find_compositions(target, config)
        assert [r["ion"] for r in with_shared] == [r["ion"] for r in alone]
        assert with_shared == alone


def test_the_row_cap_keeps_the_closest_readings():
    # A window can hold more compositions than a caller will look at, and the cap
    # decides which survive. It has to be the closest ones: everything downstream
    # ranks on mass error, so a cap that kept an arbitrary slice would hand the
    # ranking a set the ranking cannot repair.
    config = CompositionSearchConfig(
        ionizations="-H+",
        element_count_ranges="C1-40 H0-80 N0-3 O0-18 S0-1 Cl0-2 Br0-2",
        mass_range_ppm=10.0,
        max_result_rows=25,
    )
    capped = find_compositions(464.991, config)
    assert len(capped) == 25

    uncapped = find_compositions(464.991, replace(config, max_result_rows=10**9))
    assert len(uncapped) > 25
    closest = sorted(abs(r["composition_error_ppm"]) for r in uncapped)[:25]
    assert sorted(abs(r["composition_error_ppm"]) for r in capped) == closest


def test_composition_results_are_ranked_by_error_magnitude():
    # find_compositions returns best-first, and "best" is the smallest deviation
    # in either direction - not the most negative one.
    config = CompositionSearchConfig(
        ionizations="+H+",
        element_count_ranges="C0-40 H0-80 N0-10 O0-20",
        mass_range_ppm=20.0,
    )
    results = find_compositions(PROTONATED_GLUCOSE_MZ, config)
    magnitudes = [abs(r["composition_error_ppm"]) for r in results]
    assert magnitudes == sorted(magnitudes)
    assert any(r["composition_error_ppm"] < 0 for r in results), (
        "expected at least one candidate below the observed m/z, so the ordering "
        "is actually exercised on signed values"
    )


# `other_candidates` is the shortlist shown beside a committed assignment, so the
# composition that won the peak must never be in it. It cannot be taken
# positionally: `find_compositions` ranks by mass error, while the winner is
# whatever survives `apply_heuristic_rules` and then ranks first on
# `match_isotopic_pattern`'s isotope-pattern score.
def test_other_candidates_excludes_the_chosen_composition():
    comp_results = [
        {"formula": "C4H8N2O"},
        {"formula": "C5H10O2"},
        {"formula": "C6H14N"},
    ]

    # The isotope pattern promoted the second-closest composition.
    assert _other_candidate_formulas(comp_results, "C5H10O2") == "C4H8N2O, C6H14N"


def test_other_candidates_keeps_the_mass_closest_composition_when_it_loses():
    # Dropping index 0 also hid the mass-closest formula, which is exactly the
    # runner-up worth seeing when the isotope pattern demoted it.
    comp_results = [{"formula": "C4H8N2O"}, {"formula": "C5H10O2"}]

    assert _other_candidate_formulas(comp_results, "C5H10O2") == "C4H8N2O"


def test_other_candidates_keeps_every_composition_when_none_was_chosen():
    comp_results = [{"formula": "C4H8N2O"}, {"formula": "C5H10O2"}]

    assert _other_candidate_formulas(comp_results) == "C4H8N2O, C5H10O2"


def test_other_candidates_is_empty_when_the_winner_stood_alone():
    assert _other_candidate_formulas([{"formula": "C5H10O2"}], "C5H10O2") == ""
    assert _other_candidate_formulas([]) == ""


def test_assign_compositions_enumerates_only_the_targets(monkeypatch):
    """With ``targets`` given, compositions are enumerated for those peaks alone
    while every peak still gets a result row - the rest as unmatched."""
    from mascope_tools.composition import finder

    enumerated = []

    def fake_find_compositions(target_mz, config, grid=None):
        enumerated.append(target_mz)
        return []

    monkeypatch.setattr(finder, "find_compositions", fake_find_compositions)
    peaks = pd.DataFrame(
        {"mz": [100.0, 200.0, 300.0], "intensity": [100.0, 100.0, 100.0]}
    )
    config = CompositionSearchConfig(
        ionizations="H+", element_count_ranges="C0-2 H0-2", mass_range_ppm=5.0
    )

    matches, _ = finder.assign_compositions(peaks, config, targets=[200.0])

    assert enumerated == [200.0]
    assert len(matches) == 3
    assert (matches["formula"] == "---").all()


# IsoSpec orders a pattern's configurations by abundance, not by mass, so the
# row at index 0 is the most abundant isotopologue and only coincidentally the
# monoisotopic one. These are the real arrays for Br3-, whose monoisotopic peak
# is third: 236.755 (13%) against a 238.753 (38%) base peak.
_BROMINE_PATTERN = {
    "masses": [238.7530, 240.7509, 236.7550, 242.7489],
    "labels": ["81Br", "81Br2", "M0", "81Br3"],
    "predicted_masses": [238.7529, 240.7508, 236.7549, 242.7488],
    "predicted_intensities": [1.0, 0.973, 0.343, 0.315],
    "mass_errors_ppm": [0.4, 0.4, 0.4, 0.4],
    "intensity_errors": [0.0, 0.01, 0.01, 0.01],
}


def _rows_of(pattern: dict) -> list[dict]:
    """The rows `process_isotopes` builds from one matched pattern."""
    rows, _ = process_isotopes(
        {"neutral_mass": 238.7530, "formula": "Br3"}, [pattern], set()
    )
    return rows


def test_only_the_monoisotopic_row_is_labelled_m0():
    """M0 is the ion's monoisotopic isotopologue, wherever it falls in the
    pattern - one row, and not necessarily the base peak.

    The base peak carries its own configuration's label, so a bromine-rich ion
    is assigned on the lightest peak of its cluster with the tallest as a
    satellite. Labelling index 0 `M0` unconditionally would put the label on
    two rows at once, since the monoisotopic row already carries it.
    """
    rows = _rows_of(_BROMINE_PATTERN)
    labels = [row["isotope_label"] for row in rows]

    assert labels.count("M0") == 1
    assert sorted(labels) == ["81Br", "81Br2", "81Br3", "M0"]
    by_label = {row["isotope_label"]: row["mz"] for row in rows}
    assert by_label["M0"] == pytest.approx(236.7550)
    # The base peak is a satellite here, labelled by its own substitution.
    assert by_label["81Br"] == pytest.approx(238.7530)


def test_the_base_peak_is_m0_when_it_is_also_the_monoisotopic_one():
    """The ordinary case is unchanged: for an ion with no heavy-isotope-rich
    element the most abundant configuration is the monoisotopic one, and
    IsoSpec puts it first."""
    glucose = {
        "masses": [180.0634, 181.0667, 182.0676],
        "labels": ["M0", "13C", "18O"],
        "predicted_masses": [180.0634, 181.0667, 182.0676],
        "predicted_intensities": [1.0, 0.065, 0.012],
        "mass_errors_ppm": [0.1, 0.1, 0.1],
        "intensity_errors": [0.0, 0.01, 0.01],
    }

    by_label = {row["isotope_label"]: row["mz"] for row in _rows_of(glucose)}

    assert by_label["M0"] == pytest.approx(180.0634)


def _pattern(masses, labels, errors):
    """A matched isotope pattern, in the shape match_isotopic_pattern returns."""
    return {
        "masses": list(masses),
        "labels": list(labels),
        "predicted_masses": list(masses),
        "predicted_intensities": [1.0] + [0.3] * (len(masses) - 1),
        "mass_errors_ppm": list(errors),
        "intensity_errors": [0.0] * len(masses),
    }


def test_a_monoisotopic_row_outranks_another_candidates_satellite(monkeypatch):
    """One peak, two candidates: the row that IS somebody's monoisotopic line
    survives, even when the other candidate's satellite fits the mass better.

    A candidate is a whole envelope. Drop its monoisotopic row here and the
    satellites it left behind belong to nothing. Mass error alone cannot see
    that, because it compares two rows without asking what each row's loss
    costs the rest of its envelope.

    The fixture builds a collision the finder itself does not produce: a
    candidate whose monoisotopic line is not the peak it was enumerated for.
    That is deliberate. The rule is a guard - the loop claims each m/z as it
    emits it, so real input does not reach the tie - and a guard can only be
    tested by constructing the case it guards against.
    """
    from mascope_tools.composition import finder

    shared_mz = 101.0034
    patterns = {
        # Enumerated first, and its 13C line lands on the shared peak with the
        # smaller mass error - which used to be the whole contest.
        100.0: _pattern([100.0, shared_mz], ["M0", "13C"], [0.1, 3.0]),
        # A chlorine-rich ion, whose most abundant isotopologue is not its
        # monoisotopic one: the finder reports the base first, and here the
        # monoisotopic line is the shared peak.
        105.0: _pattern([shared_mz, 105.0, 106.0], ["M0", "37Cl", "37Cl2"], [5.0] * 3),
    }
    monkeypatch.setattr(
        finder,
        "find_compositions",
        lambda target_mz, config, grid=None: [{"formula": f"F{int(target_mz)}"}],
    )
    monkeypatch.setattr(
        finder,
        "apply_heuristic_rules",
        lambda comp_results, heuristics_config=None: (
            [
                dict(
                    comp_results[0],
                    neutral_mass=100.0,
                    ion=f"{comp_results[0]['formula']}H+",
                    # A scored pattern; the finder commits nothing on a zero.
                    isotopic_pattern_score=0.9,
                )
            ],
            {},
        ),
    )
    monkeypatch.setattr(
        finder,
        "match_isotopic_pattern",
        lambda candidates, peaks: (
            candidates,
            [patterns[float(candidates[0]["formula"][1:])]],
        ),
    )
    peaks = pd.DataFrame(
        {
            "mz": [100.0, shared_mz, 105.0, 106.0],
            "intensity": [1000.0, 300.0, 800.0, 240.0],
        }
    )
    config = CompositionSearchConfig(
        ionizations="H+", element_count_ranges="C0-2 H0-2", mass_range_ppm=5.0
    )

    matches, _ = assign_compositions(peaks, config, targets=[100.0, 105.0])

    at_shared = matches[matches["mz"] == shared_mz]
    assert len(at_shared) == 1
    assert at_shared.iloc[0]["isotope_label"] == "M0"
    # ...and the losing candidate keeps its own monoisotopic row, so neither
    # envelope is left with satellites that own nothing.
    assert set(matches[matches["formula"] == "F100"]["mz"]) == {100.0}
    assert set(matches[matches["formula"] == "F105"]["mz"]) == {
        shared_mz,
        105.0,
        106.0,
    }


def test_a_peak_whose_best_reading_has_no_envelope_is_left_alone(monkeypatch):
    """A candidate whose pattern scored zero is not committed.

    Zero is not a weak match: `score_pattern` returns it only when a line the
    prediction requires is absent. Candidates are ranked by that score, so a
    zero at the top means no reading of this peak has an envelope - and the row
    can always be written anyway, because the candidate's monoisotopic line IS
    the peak. Only the score says it should not be, and the finder has to read
    it. On a bromide grid this is what keeps a `+Br2-` reading whose 79Br81Br
    line is missing from taking the peak it used to swallow.
    """
    from mascope_tools.composition import finder

    monkeypatch.setattr(
        finder,
        "find_compositions",
        lambda target_mz, config, grid=None: [{"formula": "C2H2"}],
    )
    monkeypatch.setattr(
        finder,
        "apply_heuristic_rules",
        lambda comp_results, heuristics_config=None: (
            [dict(comp_results[0], neutral_mass=26.0, ion="C2H3+")],
            {},
        ),
    )
    scored = {"isotopic_pattern_score": 0.0}
    monkeypatch.setattr(
        finder,
        "match_isotopic_pattern",
        lambda candidates, peaks: (
            [dict(candidates[0], **scored)],
            [_pattern([100.0], ["M0"], [0.2])],
        ),
    )
    peaks = pd.DataFrame({"mz": [100.0], "intensity": [1000.0]})
    config = CompositionSearchConfig(
        ionizations="H+", element_count_ranges="C0-2 H0-2", mass_range_ppm=5.0
    )

    matches, _ = assign_compositions(peaks, config, targets=[100.0])

    assert list(matches["formula"]) == ["---"]
    # ...and the runner-up formulas stay visible for an inspector.
    assert matches.iloc[0]["other_candidates"] == "C2H2"
