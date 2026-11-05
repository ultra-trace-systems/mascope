"""
Unit tests for the cheminfo service functions.
"""

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.controllers.match.lib.match_score_v2 import ion_score_v2
from mascope_backend.api.new.cheminfo import service as cheminfo_service
from mascope_backend.api.new.cheminfo.service import (
    _annotate_assignment_scores,
    _annotate_with_reference,
    _fit_scores,
    _instrument_sigma_ppm,
    _one_reading_per_ion,
    retrieve_compositions_by_mz,
)
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


def _search_candidates(seed: int) -> list[dict]:
    """A search's candidates, as `match_compositions_by_mz` builds them: some
    with real SNR, some without the column at all, absent lines, satellites,
    and a tie at the top abundance."""
    rng = np.random.default_rng(seed)
    results = []
    for index in range(40):
        lines = int(rng.integers(1, 8))
        abundance = np.r_[1.0, rng.uniform(0.01, 0.7, lines - 1)]
        if index % 9 == 0 and lines > 1:
            abundance[1] = 1.0
        children = []
        for line in range(lines):
            child = {
                "relative_abundance": abundance[line],
                "match_mz_error": rng.normal(0, 3),
                "sample_peak_intensity": (
                    rng.uniform(1e2, 1e6) if rng.random() < 0.8 else 0.0
                ),
                "is_satellite": bool(rng.random() < 0.05),
            }
            if index % 4:
                child["signal_to_noise"] = (
                    rng.uniform(1, 500) if rng.random() < 0.8 else None
                )
            children.append(child)
        results.append(
            {"cheminfo": {"target_compound_formula": "C3H6O3"}, "children": children}
        )
    return results


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("sigma", [0.5, 5.0])
def test_every_candidate_at_once_scores_as_each_does_alone(seed, sigma):
    results = _search_candidates(seed)
    # A candidate with no isotopes, and one whose isotopes lack a column the
    # fit reads, have no fit; scored alone, the second raised.
    results[5]["children"] = []
    for child in results[7]["children"]:
        del child["match_mz_error"]

    fits = _fit_scores(results, sigma)

    assert fits[5] is None and fits[7] is None
    for index, entry in enumerate(results):
        if index in (5, 7):
            continue
        alone = ion_score_v2(pd.DataFrame(entry["children"]), sigma_ppm=sigma)
        assert fits[index] == pytest.approx(alone, abs=1e-12)


def test_candidates_are_scored_in_one_call(monkeypatch):
    calls = []
    scores_v2 = cheminfo_service.ion_scores_v2

    def counting(frame, by, **kwargs):
        calls.append(frame[by].nunique())
        return scores_v2(frame, by, **kwargs)

    monkeypatch.setattr(cheminfo_service, "ion_scores_v2", counting)
    monkeypatch.setattr(
        cheminfo_service,
        "ion_score_v2",
        lambda *a, **k: pytest.fail("a candidate was scored alone"),
    )

    _fit_scores(_search_candidates(0), 0.5)

    assert calls == [40]


def test_a_failing_call_falls_back_to_each_candidate_alone(monkeypatch):
    """Scoring never breaks the search: should the call over every candidate
    fail, each is scored alone, and one that fails alone loses only its fit."""
    results = _search_candidates(1)
    expected = [
        ion_score_v2(pd.DataFrame(entry["children"]), sigma_ppm=0.5)
        for entry in results
    ]

    def broken(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(cheminfo_service, "ion_scores_v2", broken)
    score_alone = cheminfo_service.ion_score_v2
    bad = results[3]["children"]

    def alone(frame, **kwargs):
        if frame.to_dict("records") == bad:
            raise ValueError("bad candidate")
        return score_alone(frame, **kwargs)

    monkeypatch.setattr(cheminfo_service, "ion_score_v2", alone)

    fits = _fit_scores(results, 0.5)

    assert fits[3] is None
    assert fits[:3] + fits[4:] == pytest.approx(expected[:3] + expected[4:], abs=1e-12)


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
    assert row["target_isotope_mz"] == pytest.approx(NITRATE_ADDUCT["14N"], abs=1e-5)
    assert abs(row["target_isotope_mz_error_ppm"]) < 0.05


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
    assert row["target_isotope_mz"] == pytest.approx(target, abs=1e-5)


@pytest.mark.asyncio
async def test_a_result_read_at_another_line_has_the_shape_of_any_other(
    test_ionization_mechanisms: list,
):
    # Which line a result was read at is its target_isotope_mz; the rows are
    # the rows the search has always answered with.
    query = {
        "ionization_mechanism_ids": _ids(test_ionization_mechanisms, "[M+^NO3]-"),
        "formula_ranges": "C0-20 H0-40 O0-15",
    }
    plain = await retrieve_compositions_by_mz(mz=NITRATE_ADDUCT["M0"], **query)
    lines = await retrieve_compositions_by_mz(
        mz=NITRATE_ADDUCT["14N"], isotopologues=True, **query
    )
    assert plain["data"] and lines["data"]
    shape = {frozenset(row) for row in plain["data"]}
    assert len(shape) == 1
    assert {frozenset(row) for row in lines["data"]} == shape


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
    assert row["target_isotope_mz"] == pytest.approx(target, abs=1e-5)
    # The finder's reading of glucose itself: the labelled formula's
    # unsaturation counts its [13C] as no carbon.
    assert row["target_compound_unsaturation"] == pytest.approx(1.0)


def test_one_reading_per_ion_keeps_the_closest_and_then_the_line_reading():
    def row(formula, mechanism, error):
        return {
            "target_compound_formula": formula,
            "ionization_mechanism": {"ionization_mechanism_id": mechanism},
            "target_isotope_mz_error_ppm": error,
        }

    closer = row("C6H12O6", "m1", -0.5)
    further = row("C6H12O6", "m1", 1.5)
    other_mechanism = row("C6H12O6", "m2", 2.0)
    at_a_line = row("C3H6O3", "m1", 0.2)
    labelled = row("C3H6O3", "m1", -0.2)

    kept = _one_reading_per_ion(
        [
            (further, False),
            (closer, False),
            (other_mechanism, False),
            (labelled, False),
            (at_a_line, True),
        ]
    )

    assert kept == [closer, other_mechanism, at_a_line]


@pytest.mark.asyncio
async def test_a_shape_the_finder_does_not_search_is_left_out_of_the_search(
    test_ionization_mechanisms: list, async_session_factory
):
    """A dimer among the asked mechanisms leaves the rest searched; alone, it
    is refused with the reason rather than searched as the monomer."""
    from sqlalchemy import delete

    from mascope_backend.db import IonizationMechanism

    dimer = IonizationMechanism(
        ionization_mechanism_id="unit-dimer-br",
        ionization_mechanism_polarity="-",
        ionization_mechanism="[2M+Br]-",
    )
    async with async_session_factory() as session:
        session.add(dimer)
        await session.commit()
    try:
        bromide = _ids(test_ionization_mechanisms, "[M+Br]-")
        both = await retrieve_compositions_by_mz(
            mz=168.9506,
            ionization_mechanism_ids=[*bromide, "unit-dimer-br"],
            formula_ranges="C0-10 H0-20 O0-10",
        )
        assert _rows_of("C3H6O3", both)
        assert {
            row["ionization_mechanism"]["ionization_mechanism"] for row in both["data"]
        } == {"[M+Br]-"}

        with pytest.raises(Exception, match="singly charged ions of one molecule"):
            await retrieve_compositions_by_mz(
                mz=168.9506,
                ionization_mechanism_ids=["unit-dimer-br"],
                formula_ranges="C0-10 H0-20 O0-10",
            )
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism_id == "unit-dimer-br"
                )
            )
            await session.commit()
