"""
Unit tests for the cheminfo service functions.
"""

import pytest

from mascope_backend.api.new.cheminfo import service as cheminfo_service
from mascope_backend.api.new.cheminfo.service import (
    _annotate_assignment_scores,
    _annotate_with_reference,
    _instrument_sigma_ppm,
    _line_fields,
    _one_reading_per_ion,
    retrieve_compositions_by_mz,
)
from mascope_backend.api.new.cheminfo.utils import explicit_isotope_line
from mascope_match.params import OrbiMatchParams, TofMatchParams
from mascope_tools.composition.heuristic_filter import predict_isotopes


def assert_cheminfo_query_result_format(result: dict):
    """Assert that the cheminfo query result has the expected format and is not empty."""
    assert isinstance(result, dict)
    assert "message" in result
    assert "results" in result
    assert "total" in result
    assert "data" in result

    assert result["results"] > 0
    assert result["total"] > 0


def assert_cheminfo_result_row_format(result: dict):
    """Assert that a single row of the cheminfo result has the expected format."""
    assert isinstance(result, dict)
    assert "target_compound_formula" in result
    assert "target_compound_unsaturation" in result
    assert "ionization_mechanism" in result
    assert "target_isotope_mz" in result
    assert "target_isotope_mz_error_ppm" in result


@pytest.mark.parametrize(
    "mz, expected_formula, formula_ranges_addition",
    [
        # An m/z here is an ION mass, not the neutral monoisotopic mass: the
        # electron is 8.7 ppm at m/z 63, so a neutral mass written here is a
        # test that passes only while the default tolerance is loose enough to
        # swallow the difference. The first two were exactly that until the
        # default came down to 3 ppm.
        # Basic test cases
        (62.99619, "HNO3", ""),
        (90.03224, "C3H6O3", ""),
        (124.9244, "CH2O2", ""),
        (168.9506, "C3H6O3", ""),
        # Extended test cases, covering non-default parameters
        (78.9189, "Br", "Br0-1"),
        (80.9168, "Br", "[81Br]0-1"),
        (62.9854, "O3^N", "^N0-1"),
    ],
)
@pytest.mark.asyncio
async def test_retrieve_compositions_by_mz(
    mz, expected_formula, formula_ranges_addition, cheminfo_query_data: dict
):
    """Test retrieving composition data by m/z values for basic list.

    This test checks that the composition search service can retrieve data for a list of m/z values,
    validates the result format, and ensures that the expected formulae are present in the results.
    """
    cheminfo_query_data["mz"] = mz
    cheminfo_query_data["formula_ranges"] = (
        f"{cheminfo_query_data['formula_ranges']} {formula_ranges_addition}"
    )
    result = await retrieve_compositions_by_mz(**cheminfo_query_data)
    assert_cheminfo_query_result_format(result)

    result_formulae = []

    for data_row in result["data"]:
        assert_cheminfo_result_row_format(data_row)
        result_formulae.append(data_row["target_compound_formula"])

    # Check if the expected formulae were found in the results
    assert expected_formula in result_formulae


def _candidate(mz_errors, formula="C3H6O3"):
    """One matched search result: a two-isotopologue envelope (M0 + M+1).

    Mirrors what `match_compositions_by_mz` builds per candidate - the match ion
    with its `children` isotope rows as `compute_match_isotopes` produces them.
    """
    return {
        "cheminfo": {"target_compound_formula": formula},
        "children": [
            {
                "relative_abundance": abundance,
                "match_mz_error": error,
                "sample_peak_intensity": intensity,
                "signal_to_noise": snr,
            }
            for abundance, error, intensity, snr in zip(
                [1.0, 0.11], mz_errors, [1000.0, 110.0], [500.0, 55.0]
            )
        ],
    }


def test_instrument_sigma_from_match_params():
    """Sigma follows the instrument's tolerance, not the search window."""
    orbi = _instrument_sigma_ppm(OrbiMatchParams())
    tof = _instrument_sigma_ppm(TofMatchParams())
    # The instrument bands score_pattern_v2 documents: ~0.5-2 ppm Orbitrap, ~5-10 TOF.
    assert 0.5 <= orbi <= 2.0
    assert 5.0 <= tof <= 10.0
    assert tof > orbi


def test_instrument_sigma_none_without_usable_tolerance():
    """No resolvable instrument -> no sigma, so the caller reports no fit score."""
    assert _instrument_sigma_ppm(None) is None
    assert _instrument_sigma_ppm(OrbiMatchParams(mz_tolerance=0)) is None


def test_assignment_scores_discriminate_on_mass():
    """The mass term must separate candidates inside the search window.

    The regression this pins: a sigma fitted from the pooled candidates measures the
    +/- mz_precision window they are spread over by construction, which flattens the
    mass term until every candidate scores alike.
    """
    results = [_candidate([0.2, 0.3]), _candidate([12.0, 12.5], formula="C2H2O4")]
    _annotate_assignment_scores(results, OrbiMatchParams())

    assert results[0]["fit_score"] > results[1]["fit_score"]
    assert results[0]["plausibility"] is not None
    assert results[0]["tier"] is not None


def test_assignment_scores_skipped_without_instrument_sigma():
    """No sigma -> no fit score at all, rather than one from an Orbitrap-only default."""
    results = [_candidate([0.2, 0.3])]
    _annotate_assignment_scores(results, None)

    assert "fit_score" not in results[0]
    assert "tier" not in results[0]


@pytest.mark.asyncio
async def test_reference_annotation_skipped_when_not_in_play(monkeypatch):
    """Opted-out deployment, no `known_only`: develop's response shape, no query."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")

    async def _fail(*args, **kwargs):
        raise AssertionError("the reference mirror must not be queried")

    monkeypatch.setattr(cheminfo_service.reference_service, "annotate_formulas", _fail)

    results = [{"target_compound_formula": "C3H6O3"}]
    annotated = await _annotate_with_reference(results, known_only=False)

    assert annotated == results
    assert "known_compounds" not in annotated[0]


@pytest.mark.asyncio
async def test_reference_annotation_runs_on_explicit_opt_in(monkeypatch):
    """`known_only` is the per-call opt-in, so it consults the mirror even when off."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")

    async def _annotate(formulas, *args, **kwargs):
        return {"C3H6O3": [{"name": "lactic acid"}]}

    monkeypatch.setattr(
        cheminfo_service.reference_service, "annotate_formulas", _annotate
    )

    results = [
        {"target_compound_formula": "C3H6O3"},
        {"target_compound_formula": "C2H2O4"},
    ]
    annotated = await _annotate_with_reference(results, known_only=True)

    assert [r["target_compound_formula"] for r in annotated] == ["C3H6O3"]
    assert annotated[0]["known_compounds"] == [{"name": "lactic acid"}]


# --- Which line of a candidate's ion the searched m/z is ----------------------
#
# The search reads the m/z as the monoisotopic line of every candidate's ion
# unless asked otherwise. A labelled reagent's adduct is read at its labelled
# line, and with `isotopologues` the m/z may be any line of the ion bright
# enough to show - its brightest one among them.


def _lines(ion: str, charge: int) -> dict[str, float]:
    """An ion's predicted lines by label: its m/z at each."""
    mzs, _, labels = predict_isotopes(ion, charge, threshold=1e-4)
    return {label: float(mz) for mz, label in zip(mzs, labels)}


def _ids(mechanisms: list, *notations: str) -> list[str]:
    return [
        m.ionization_mechanism_id
        for m in mechanisms
        if m.ionization_mechanism in notations
    ]


def _rows_of(formula: str, result: dict) -> list[dict]:
    return [r for r in result["data"] if r["target_compound_formula"] == formula]


# The ion of C10H16O7 with the 15N-nitrate reagent: its labelled line, where the
# reagent puts 98% of it, and the unlabelled remainder one unit below.
NITRATE_ADDUCT = _lines("C10H16O10^N", -1)


@pytest.mark.asyncio
async def test_a_labelled_adduct_is_found_at_its_labelled_line(
    test_ionization_mechanisms: list,
):
    """The peak a 15N-nitrate adduct shows most of finds its compound.

    The labelled atom used to be massed as the unlabelled one, 0.997 Da light,
    so the compound was found one unit below its own peak, at the 2% remainder.
    """
    ids = _ids(test_ionization_mechanisms, "[M+^NO3]-")
    ranges = "C0-20 H0-40 O0-15"

    at_label = await retrieve_compositions_by_mz(
        mz=NITRATE_ADDUCT["M0"], ionization_mechanism_ids=ids, formula_ranges=ranges
    )
    assert _rows_of("C10H16O7", at_label)

    at_remainder = await retrieve_compositions_by_mz(
        mz=NITRATE_ADDUCT["14N"], ionization_mechanism_ids=ids, formula_ranges=ranges
    )
    assert not _rows_of("C10H16O7", at_remainder)


@pytest.mark.asyncio
async def test_isotopologues_find_the_unlabelled_remainder(
    test_ionization_mechanisms: list,
):
    result = await retrieve_compositions_by_mz(
        mz=NITRATE_ADDUCT["14N"],
        ionization_mechanism_ids=_ids(test_ionization_mechanisms, "[M+^NO3]-"),
        formula_ranges="C0-20 H0-40 O0-15",
        isotopologues=True,
    )
    (row,) = _rows_of("C10H16O7", result)
    assert row["target_isotope_label"] == "14N"
    assert row["target_isotope_offset"] == -1
    assert row["target_isotope_abundance"] == pytest.approx(0.02 / 0.98, rel=1e-6)
    assert row["target_isotope_mz"] == pytest.approx(NITRATE_ADDUCT["14N"], abs=1e-6)
    assert abs(row["target_isotope_mz_error_ppm"]) < 0.01


@pytest.mark.asyncio
async def test_isotopologues_find_a_dibromide_at_its_brightest_line(
    test_ionization_mechanisms: list,
):
    """A dibromide's 79Br81Br line is twice its monoisotopic one."""
    target = _lines("C6H3Br2O", -1)["81Br"]
    query = {
        "mz": target,
        "ionization_mechanism_ids": _ids(test_ionization_mechanisms, "[M-H]-"),
        "formula_ranges": "C0-12 H0-20 O0-4 Br0-2",
    }

    assert not _rows_of("C6H4Br2O", await retrieve_compositions_by_mz(**query))

    (row,) = _rows_of(
        "C6H4Br2O",
        await retrieve_compositions_by_mz(**query, isotopologues=True),
    )
    assert row["target_isotope_label"] == "81Br"
    assert row["target_isotope_offset"] == 2
    assert row["target_isotope_abundance"] == pytest.approx(1.0)
    assert row["target_isotope_mz"] == pytest.approx(target, abs=1e-6)


@pytest.mark.asyncio
async def test_a_monoisotopic_search_keeps_its_response_shape(
    test_ionization_mechanisms: list,
):
    result = await retrieve_compositions_by_mz(
        mz=NITRATE_ADDUCT["M0"],
        ionization_mechanism_ids=_ids(test_ionization_mechanisms, "[M+^NO3]-"),
        formula_ranges="C0-20 H0-40 O0-15",
    )
    assert result["data"]
    for row in result["data"]:
        assert (
            not {
                "target_isotope_label",
                "target_isotope_offset",
                "target_isotope_abundance",
            }
            & row.keys()
        )


@pytest.mark.asyncio
async def test_a_compound_read_twice_at_one_line_is_one_result(
    test_ionization_mechanisms: list,
):
    """A formula range holding [13C] finds the labelled glucose, which is
    reported as glucose at its 13C line - the line every isotopologue already
    reads glucose at. One ion, one row."""
    target = _lines("C6H13O6", 1)["13C"]
    result = await retrieve_compositions_by_mz(
        mz=target,
        ionization_mechanism_ids=_ids(test_ionization_mechanisms, "[M+H]+"),
        formula_ranges="C0-12 H0-24 O0-8 [13C]0-1",
        isotopologues=True,
    )
    (row,) = _rows_of("C6H12O6", result)
    assert row["target_isotope_label"] == "13C"
    assert row["target_isotope_offset"] == 1


def test_the_line_comes_from_the_finder_where_it_read_one():
    raw = {
        "formula": "C6H12O6",
        "isotope_label": "13C",
        "isotope_offset": 1,
        "isotope_abundance": 0.065,
    }
    assert _line_fields(raw, "C6H12O6") == {
        "target_isotope_label": "13C",
        "target_isotope_offset": 1,
        "target_isotope_abundance": 0.065,
    }


def test_a_bracketed_isotope_names_the_line_it_reads():
    assert _line_fields({"formula": "[13C]C5H12O6"}, "[13C]C5H12O6") == {
        "target_isotope_label": "13C",
        "target_isotope_offset": 1,
        "target_isotope_abundance": None,
    }
    assert _line_fields({"formula": "C6H12O6"}, "C6H12O6")["target_isotope_label"] == (
        "M0"
    )


@pytest.mark.parametrize(
    ("formula", "line"),
    [
        ("[13C]C5H12O6", ("13C", 1)),
        ("[13C]2[18O]C4H12O5", ("13C2+18O", 4)),
        ("[81Br]C6H4BrO", ("81Br", 2)),
        ("C6H12O6", None),
        # A labelled reagent's atom is a compound's own, not a line of another.
        ("C10H16O10^N", None),
    ],
)
def test_explicit_isotope_line(formula, line):
    assert explicit_isotope_line(formula) == line


def test_one_reading_per_ion_keeps_the_closest_and_then_the_measured_line():
    def row(formula, mechanism, error, abundance=None):
        return {
            "target_compound_formula": formula,
            "ionization_mechanism": {"ionization_mechanism_id": mechanism},
            "target_isotope_mz_error_ppm": error,
            "target_isotope_abundance": abundance,
        }

    closer = row("C6H12O6", "m1", -0.5)
    further = row("C6H12O6", "m1", 1.5)
    other_mechanism = row("C6H12O6", "m2", 2.0)
    measured = row("C3H6O3", "m1", 0.2, abundance=0.03)
    unmeasured = row("C3H6O3", "m1", -0.2)

    kept = _one_reading_per_ion(
        [further, closer, other_mechanism, unmeasured, measured]
    )

    assert kept == [closer, other_mechanism, measured]
