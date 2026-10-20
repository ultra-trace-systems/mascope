"""
The TOF calibration audit: the node's handler on stored data, writing nothing.

The audit is only a yardstick if its refit is the node's fit. So the central
tests run the node's own ``TofCalibrationHandler`` and the audit on the same
file and ask for the same parameters, through each of the node's selection
paths: the exact subset search for five matches or fewer, and the one-pass
residual rule above that. The rest pins what the audit swaps out - every read
that would write, and the database - and the yardsticks it reports with.
"""

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import tof_audit_support as support

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TofCalibrationHandler,
)
from mascope_backend.db import IonizationMechanism
from mascope_tools.composition.utils import composition_mass, parse_composition


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    # The instrument type is remembered by name, and every test's file has
    # the same name
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


async def _node_fit(filename, isotopes, params):
    """What the node's own handler fits the file to."""
    handler = TofCalibrationHandler(filename, params)
    with (
        patch.object(
            TofCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=isotopes.drop(columns="ion_label")),
        ),
        patch(
            f"{NODE}.read_instrument_functions",
            AsyncMock(return_value=({}, support.resolution)),
        ),
    ):
        await handler.fit()
    return handler


async def _audit_fit(filename, isotopes, params, model=0):
    stored = audit.read_stored_peaks(filename)
    return await audit.refit(stored, params, isotopes, support.resolution, model)


# --- The refit is the node's fit ---


@pytest.mark.asyncio
async def test_a_refit_of_five_lines_is_the_node_fit(filestore):
    """Five lines, one of them 40 ppm off: the exact subset search drops it."""
    filename = support.write_file(filestore, support.calibrant_peaks(off={188: 40.0}))
    isotopes = support.isotopes()
    params = support.params()

    node = await _node_fit(filename, isotopes, params)
    refit = await _audit_fit(filename, isotopes, params)

    assert node.fit_result is not None
    assert refit.fit["par"] == node.fit_result["par"]
    # The subset search ran: the bad line is out of both
    assert len(refit.retained) == 4
    assert not any(label.startswith("H2N3O9-") for label in refit.retained)
    # And the fit landed on the axis the peaks were placed by
    good = refit.readings[refit.readings.ion_label.isin(refit.retained)]
    assert np.all(
        np.abs(audit.ppm_error(good.sample_peak_tof, good.mz, refit.fit)) < 1e-6
    )


@pytest.mark.asyncio
async def test_a_refit_of_more_than_five_lines_is_the_node_fit(filestore):
    """Seven lines, one 40 ppm off: the one-pass 15 ppm rule drops it."""
    formulas = support.CALIBRANTS + support.MORE_CALIBRANTS
    filename = support.write_file(
        filestore, support.calibrant_peaks(formulas, off={125: 40.0})
    )
    isotopes = support.isotopes(formulas)
    params = support.params()

    node = await _node_fit(filename, isotopes, params)
    refit = await _audit_fit(filename, isotopes, params)

    assert refit.fit["par"] == node.fit_result["par"]
    assert len(refit.readings) == 7
    assert len(refit.retained) == 6


@pytest.mark.asyncio
async def test_the_strongest_peak_in_the_window_is_the_match_in_both(filestore):
    """A stronger peak 50 ppm from a calibrant takes the match, node and audit
    alike, and the fit then rejects it."""
    peaks = support.calibrant_peaks()
    peaks.append({"mz": 124.984 * (1 + 50e-6), "area": 1000.0})
    filename = support.write_file(filestore, peaks)
    isotopes = support.isotopes()
    params = support.params()

    node = await _node_fit(filename, isotopes, params)
    refit = await _audit_fit(filename, isotopes, params)

    assert refit.fit["par"] == node.fit_result["par"]
    matched = refit.readings.set_index(refit.readings.ion_label.str.split().str[0])
    assert matched.loc["HN2O6-", "sample_peak_mz"] == pytest.approx(
        124.984 * (1 + 50e-6), rel=1e-5
    )


@pytest.mark.asyncio
async def test_the_record_tolerance_changes_the_fit_in_both(filestore):
    """A line 20 ppm off is kept at a 30 ppm tolerance and dropped at 15,
    which is what makes the record's own tolerance matter for reproducing a
    fit the node retried at a wider one."""
    filename = support.write_file(filestore, support.calibrant_peaks(off={125: 20.0}))
    isotopes = support.isotopes()

    narrow = await _audit_fit(filename, isotopes, support.params())
    wide = await _audit_fit(filename, isotopes, support.params(mz_error_tolerance=30))
    node_wide = await _node_fit(
        filename, isotopes, support.params(mz_error_tolerance=30)
    )

    assert len(narrow.retained) == 4
    assert len(wide.retained) == 5
    assert wide.fit["par"] == node_wide.fit_result["par"]


# --- What the audit reads, and what it does not ---


@pytest.mark.asyncio
async def test_weak_and_satellite_peaks_are_not_matched(filestore):
    """A load leaves them out; so does the audit."""
    peaks = support.calibrant_peaks()
    peaks.append({"mz": 61.9884 * (1 + 30e-6), "area": 1e6, "satellite": True})
    peaks.append({"mz": 78.9189 * (1 + 30e-6), "area": 1e6, "weak": True})
    filename = support.write_file(filestore, peaks)

    stored = audit.read_stored_peaks(filename)

    assert stored.peaks.mz.size == len(peaks) - 2
    refit = await audit.refit(
        stored, support.params(), support.isotopes(), support.resolution
    )
    assert len(refit.retained) == 5


@pytest.mark.asyncio
async def test_peaks_of_the_other_polarity_are_not_matched(filestore):
    peaks = support.calibrant_peaks()
    peaks.append({"mz": 61.9884 * (1 + 30e-6), "area": 1e6, "polarity": "+"})
    filename = support.write_file(filestore, peaks)

    refit = await _audit_fit(filename, support.isotopes(), support.params())

    assert len(refit.retained) == 5


@pytest.mark.asyncio
async def test_a_candidate_with_no_stored_timeseries_is_ranked_by_its_sum(
    filestore,
):
    """The node would compute and store its timeseries; the audit ranks it by
    the stored sum, and counts it."""
    peaks = support.calibrant_peaks()
    decoy = 124.984 * (1 + 50e-6)
    peaks.append({"mz": decoy, "area": 1000.0, "computed": False})
    filename = support.write_file(filestore, peaks)
    stored = audit.read_stored_peaks(filename)

    refit = await audit.refit(
        stored, support.params(), support.isotopes(), support.resolution
    )

    matched = refit.readings.set_index(refit.readings.ion_label.str.split().str[0])
    assert matched.loc["HN2O6-", "sample_peak_mz"] == pytest.approx(decoy, rel=1e-5)
    assert len(stored.estimated) == 1


@pytest.mark.asyncio
async def test_a_refit_writes_nothing(filestore):
    """On a store a load would add sparsity to, with a candidate whose
    timeseries a fill would compute and store: both of them writes the node
    makes, and neither may happen on a site."""
    peaks = support.calibrant_peaks()
    peaks.append({"mz": 124.984 * (1 + 50e-6), "area": 1.0, "computed": False})
    filename = support.write_file(filestore, peaks, sparsity=False)
    before = support.snapshot(filestore)

    stored = audit.read_stored_peaks(filename)
    refit = await audit.refit(
        stored, support.params(), support.isotopes(), support.resolution
    )

    assert refit.fit is not None
    assert support.snapshot(filestore) == before


@pytest.mark.asyncio
async def test_the_total_ion_current_is_not_read(filestore):
    """On a Tofwerk file it reads every scan, for a share nothing here uses."""
    filename = support.write_file(filestore, support.calibrant_peaks())
    with patch(f"{NODE}.m_compute.get_tic_per_scan", side_effect=AssertionError):
        refit = await _audit_fit(filename, support.isotopes(), support.params())
    assert refit.fit is not None


@pytest.mark.asyncio
async def test_the_overlap_filter_uses_the_resolution_it_is_given(filestore):
    """Two peaks within one FWHM are both dropped, by the function passed in
    rather than one read from the database."""
    peaks = support.calibrant_peaks()
    # 20 ppm is inside one FWHM at the support's resolving power
    peaks.append({"mz": 61.9884 * (1 + 20e-6), "area": 1.0})
    filename = support.write_file(filestore, peaks)

    with patch(f"{NODE}.read_instrument_functions", side_effect=AssertionError):
        refit = await _audit_fit(filename, support.isotopes(), support.params())

    assert not any(label.startswith("NO3-") for label in refit.readings.ion_label)


@pytest.mark.asyncio
async def test_a_file_with_no_peaks_is_not_fitted(filestore):
    filename = support.write_file(filestore, support.calibrant_peaks())
    stored = audit.read_stored_peaks(filename)
    stored.peaks = stored.peaks.isel(mz=[])

    refit = await audit.refit(
        stored, support.params(), support.isotopes(), support.resolution
    )

    assert refit.fit is None
    assert refit.warning == "The sample file has no peaks."


@pytest.mark.asyncio
async def test_a_set_with_no_isotopes_is_not_fitted(filestore):
    filename = support.write_file(filestore, support.calibrant_peaks())

    refit = await _audit_fit(filename, support.isotopes().iloc[0:0], support.params())

    assert refit.fit is None
    assert "no isotopes" in refit.warning


@pytest.mark.asyncio
async def test_a_refit_fits_the_model_it_is_asked_for(filestore, monkeypatch):
    """The mode-2 fit plugs in by its number; until then only mode 0 fits."""
    filename = support.write_file(filestore, support.calibrant_peaks())
    asked = []

    def model_9(tof, mz, exact):
        asked.append(len(tof))
        return audit.mz_calibrate(tof, mz, exact)

    monkeypatch.setitem(audit.MODELS, 9, model_9)
    refit = await _audit_fit(filename, support.isotopes(), support.params(), model=9)

    assert refit.fit is not None
    assert asked

    with pytest.raises(ValueError, match="cannot be fitted"):
        await _audit_fit(filename, support.isotopes(), support.params(), model=2)


@pytest.mark.asyncio
async def test_matching_alone_finds_the_series_by_the_node_rule(filestore):
    """A series member is matched like a calibrant: strongest in the window."""
    peaks = support.calibrant_peaks()
    peaks.append({"mz": 96.9601, "area": 50.0})
    filename = support.write_file(filestore, peaks)
    stored = audit.read_stored_peaks(filename)
    series = audit.series_isotopes(
        {"sulfate": {"core": "HSO4", "unit": "H2SO4", "n": 1, "charge": "-"}}, "-"
    )

    found = await audit.match_only(stored, support.params(), series, support.resolution)

    assert [label.split()[0] for label in found.ion_label] == ["HO4S-"]


# --- Evaluating fits ---


def test_an_error_is_the_fit_m_z_against_the_exact_one():
    mz = 200.0
    tof = support.tof_of(mz * (1 + 5e-6))
    assert audit.ppm_error([tof], [mz], support.TRUE_FIT)[0] == pytest.approx(
        5.0, abs=1e-6
    )


def test_two_fits_differ_by_their_largest_distance_over_the_span():
    tofs = (support.tof_of(60.0), support.tof_of(250.0))
    assert audit.axis_difference_ppm(support.TRUE_FIT, support.TRUE_FIT, tofs) == 0.0
    assert audit.axis_difference_ppm(
        support.STORED_FIT, support.TRUE_FIT, tofs
    ) == pytest.approx(6.0, rel=1e-3)


# --- Sets and series ---


def test_a_set_of_formulas_is_the_isotopes_a_collection_would_hold():
    isotopes = support.isotopes(["Br"])
    kept = isotopes[isotopes.relative_abundance >= 0.15]
    assert sorted(round(mz, 3) for mz in kept.mz) == [78.919, 80.917]
    assert all(label.startswith("Br- ") for label in isotopes.ion_label)
    assert set(isotopes.resolution) == {"LOW"}


def test_a_formula_no_mechanism_ionises_is_refused():
    plus = IonizationMechanism(
        ionization_mechanism_id="h",
        ionization_mechanism="[M-H]-",
        ionization_mechanism_polarity="-",
    )
    with pytest.raises(ValueError, match="makes no ion"):
        audit.isotopes_of(["Br"], [plus])


def test_a_collection_isotope_is_named_by_its_ion():
    isotopes = support.isotopes(["NO3"]).drop(columns="ion_label")
    ion_id = isotopes.target_ion_id.iloc[0]
    named = audit.label_isotopes(isotopes, {ion_id: "NO3-"})
    assert named.ion_label.iloc[0] == "NO3- 61.9884"


def test_a_series_is_the_core_and_its_units():
    assert audit.series_formulas("HSO4", "H2SO4", 2) == [
        ("HO4S", 0),
        ("H3O8S2", 1),
        ("H5O12S3", 2),
    ]


def test_a_series_is_made_of_the_polarity_asked_for():
    series = audit.series_isotopes(audit.DEFAULT_CLUSTER_SERIES, "-")
    main = series.loc[
        series.groupby(["series", "series_n"]).relative_abundance.idxmax()
    ]
    nitrate = main[main.series == "NO3-.(HNO3)n"].sort_values("series_n")
    assert [round(mz, 3) for mz in nitrate.mz] == [
        61.988,
        124.984,
        187.980,
        250.975,
        313.971,
    ]
    assert audit.series_isotopes(audit.DEFAULT_CLUSTER_SERIES, "+").empty


# --- Families ---


def _mechanism(text):
    return IonizationMechanism(
        ionization_mechanism_id=text,
        ionization_mechanism=text,
        ionization_mechanism_polarity=text[-1] if text[-1] in "+-" else text[-2],
    )


def _member(neutral: str, mechanism: str) -> float:
    """The m/z of a CHO neutral's ion under a one-adduct mechanism."""
    (shift, *_) = audit.mechanism_shifts(_mechanism(mechanism))
    return composition_mass(parse_composition(neutral)) + shift


def test_a_family_offset_is_the_distance_to_the_nearest_member():
    family = audit.family_of(_mechanism("[M+NO3]-"), audit.cho_neutral_masses(3))
    # C5H8O4.NO3-: a member, read 10 ppm high
    member = _member("C5H8O4", "[M+NO3]-")
    mz, offsets = family.offsets_ppm(np.array([member * (1 + 10e-6)]), 30.0)
    assert offsets == pytest.approx([10.0], abs=0.1)
    assert mz == pytest.approx([member * (1 + 10e-6)])


def test_a_peak_far_from_every_member_is_left_out():
    family = audit.family_of(_mechanism("[M+NO3]-"), audit.cho_neutral_masses(3))
    member = _member("C5H8O4", "[M+NO3]-")
    _, offsets = family.offsets_ppm(np.array([member * (1 + 50e-6)]), 30.0)
    assert offsets.size == 0


def test_both_bromine_lines_make_a_lattice():
    shifts = audit.mechanism_shifts(_mechanism("[M+Br]-"))
    assert [round(shift, 2) for shift in shifts] == [78.92, 80.92]


def test_a_shift_is_the_mechanism_adduct():
    assert audit.mechanism_shifts(_mechanism("[M+NO3]-")) == [
        pytest.approx(61.98837, abs=1e-5)
    ]
    assert audit.mechanism_shifts(_mechanism("[M-H]-")) == [
        pytest.approx(-1.00728, abs=1e-5)
    ]


def test_a_mechanism_of_two_neutrals_has_no_family():
    assert audit.mechanism_shifts(_mechanism("[2M+H]+")) is None


def test_the_lattice_is_spaced_by_o_against_ch4():
    """Up to three double bonds and rings, members of one nominal mass are
    36.4 mDa apart. Four allows three O-for-CH4 swaps and one C-for-H12 swap
    between two members, 15.3 mDa."""

    def closest_pair(masses):
        masses = masses[(masses > 290) & (masses < 310)]
        return np.min(np.diff(np.sort(masses)))

    assert closest_pair(
        audit.cho_neutral_masses(audit.DEFAULT_FAMILY_DBE_MAX)
    ) == pytest.approx(0.0364, abs=1e-4)
    assert closest_pair(audit.cho_neutral_masses(4)) == pytest.approx(0.0153, abs=1e-4)


# --- Bands and summaries ---


def test_bands_include_below_the_first_edge_and_above_the_last():
    edges = (40.0, 100.0, 500.0)
    assert audit.band_labels(edges) == ["<40", "40-100", "100-500", "500+"]
    assert audit.band_of(
        np.array([30.0, 40.0, 99.9, 100.0, 900.0]), edges
    ).tolist() == [
        0,
        1,
        1,
        2,
        3,
    ]


def test_a_spread_is_median_quartiles_rms_and_count():
    stats = audit.spread([1.0, 2.0, 3.0, 4.0, float("nan")])
    assert stats["median"] == 2.5
    assert stats["iqr"] == pytest.approx(1.5)
    assert stats["n"] == 4
    assert stats["rms"] == pytest.approx(np.sqrt(7.5))
    assert audit.spread([]) is None


def test_the_node_takes_calibrant_shares_of_the_total_ion_current():
    """What the audit leaves out: the sum of the file's TIC per scan."""
    handler = TofCalibrationHandler(support.FILENAME, support.params())
    with patch(
        f"{NODE}.m_compute.get_tic_per_scan",
        return_value=(np.arange(3), np.array([1.0, 2.0, 4.0])),
    ):
        assert handler._total_ion_current() == 7.0
