"""
Unit tests for the cheminfo service functions.
"""

import pytest

from mascope_backend.api.new.cheminfo import service as cheminfo_service
from mascope_backend.api.new.cheminfo.service import (
    _annotate_assignment_scores,
    _annotate_with_reference,
    _instrument_sigma_ppm,
    retrieve_compositions_by_mz,
)
from mascope_match.params import OrbiMatchParams, TofMatchParams


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
        # Basic test cases
        (62.99564, "HNO3", ""),
        (90.03169, "C3H6O3", ""),
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
