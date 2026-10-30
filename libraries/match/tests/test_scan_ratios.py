"""An Orbitrap line's ratio to its anchor, read over the scans both are in.

The series are synthetic (mz, time) arrays as ``load_peaks`` returns them: a
peak's per-scan heights, zero in a scan whose floor censored the centroid.
``compute_match_isotopes`` runs on them with the store read replaced, so the
instrument branch and the call site are covered, not only the helper.
"""

import asyncio

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import mascope_match.compute.isotopes as isotopes
from mascope_match.compute.isotopes import (
    calculate_match_stats,
    compute_match_isotopes,
    present_scan_ratios,
)


N_SCANS = 20
EVEN = np.arange(N_SCANS) % 2 == 0
FIRST_HALF = np.arange(N_SCANS) < N_SCANS // 2


def _peaks(rows: list[tuple[float, str, np.ndarray]]) -> xr.DataArray:
    """A series array: one (mz, peak_id, per-scan heights) per peak."""
    mzs = [mz for mz, _, _ in rows]
    return xr.DataArray(
        np.vstack([series for _, _, series in rows]).astype(float),
        dims=("mz", "time"),
        coords={
            "mz": mzs,
            "time": np.arange(N_SCANS, dtype=float),
            "peak_id": ("mz", [peak_id for _, peak_id, _ in rows]),
            "tof": ("mz", np.zeros(len(rows))),
        },
    )


def _targets(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """Target isotopes: one (ion id, m/z, relative abundance) per line."""
    return pd.DataFrame(
        {
            "target_ion_id": [ion for ion, _, _ in rows],
            "target_isotope_id": [f"iso-{i}" for i in range(len(rows))],
            "mz": [mz for _, mz, _ in rows],
            "relative_abundance": [ra for _, _, ra in rows],
        }
    )


def _compute(
    monkeypatch: pytest.MonkeyPatch,
    peaks: xr.DataArray,
    targets: pd.DataFrame,
    instrument_type: str = "orbi",
    existing_reference_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    loaded: dict = {}

    async def fake_load_peaks(filename, target_mzs, polarity=None):
        loaded["target_mzs"] = np.asarray(target_mzs)
        return peaks

    monkeypatch.setattr(isotopes, "load_peaks", fake_load_peaks)
    monkeypatch.setattr(isotopes, "get_instrument_type", lambda _: instrument_type)
    result = asyncio.run(
        compute_match_isotopes(
            "sample-file",
            targets,
            existing_reference_df=existing_reference_df,
        )
    )
    result.attrs["loaded_mzs"] = loaded["target_mzs"]
    return result.set_index("target_isotope_id")


# An anchor in every scan at 1000; its 13C line at a tenth of it, kept in only
# half the scans. A zero peak ahead of both on the axis, so the peaks the
# matcher chooses from are not numbered as the series' axis is.
ANCHOR = np.full(N_SCANS, 1000.0)
LINE_CENSORED = np.where(EVEN, 100.0, 0.0)
ZERO = np.zeros(N_SCANS)


def _censored_peaks() -> xr.DataArray:
    return _peaks(
        [
            (99.5, "p-zero", ZERO),
            (100.0, "p-anchor", ANCHOR),
            (101.0, "p-line", LINE_CENSORED),
        ]
    )


ION = [("ion", 100.0, 1.0), ("ion", 101.0, 0.1)]


class TestOrbitrapReadsOverPresentScans:
    def test_a_line_censored_in_half_the_scans_reads_its_full_ratio(self, monkeypatch):
        result = _compute(monkeypatch, _censored_peaks(), _targets(ION))

        # The stored intensity is still the mean over every scan
        assert result.loc["iso-1", "sample_peak_intensity"] == pytest.approx(50.0)
        # ...but the ratio is read over the ten scans that keep the line
        assert result.loc["iso-1", "sample_peak_intensity_relative"] == pytest.approx(
            0.1
        )
        assert result.loc["iso-1", "match_abundance_error"] == pytest.approx(0.0)
        assert result.loc["iso-1", "match_score"] == pytest.approx(1.0)
        assert result.loc["iso-1", "ratio_scan_count"] == 10
        # The anchor reads against itself, over every scan it is in
        assert result.loc["iso-0", "sample_peak_intensity_relative"] == pytest.approx(
            1.0
        )
        assert result.loc["iso-0", "ratio_scan_count"] == N_SCANS

    def test_a_plume_whose_anchor_is_absent_in_the_same_scans_reads_unchanged(
        self, monkeypatch
    ):
        # The compound is there for the first half of the file only: nothing is
        # censored, and the ratio is the one mean over mean gives.
        peaks = _peaks(
            [
                (100.0, "p-anchor", np.where(FIRST_HALF, 1000.0, 0.0)),
                (101.0, "p-line", np.where(FIRST_HALF, 100.0, 0.0)),
            ]
        )
        result = _compute(monkeypatch, peaks, _targets(ION))

        mean_over_mean = 50.0 / 500.0
        assert result.loc["iso-1", "sample_peak_intensity_relative"] == pytest.approx(
            mean_over_mean
        )
        assert result.loc["iso-1", "ratio_scan_count"] == N_SCANS // 2

    def test_a_line_present_without_its_anchor_counts_only_the_shared_scans(
        self, monkeypatch
    ):
        # The anchor is in the first half only, the line in every scan: the
        # scans where the anchor is absent add nothing to either side.
        peaks = _peaks(
            [
                (100.0, "p-anchor", np.where(FIRST_HALF, 1000.0, 0.0)),
                (101.0, "p-line", np.full(N_SCANS, 100.0)),
            ]
        )
        result = _compute(monkeypatch, peaks, _targets(ION))

        assert result.loc["iso-1", "sample_peak_intensity_relative"] == pytest.approx(
            0.1
        )
        assert result.loc["iso-1", "ratio_scan_count"] == N_SCANS // 2

    def test_a_line_never_present_with_its_anchor_reads_zero(self, monkeypatch):
        peaks = _peaks(
            [
                (100.0, "p-anchor", np.where(FIRST_HALF, 1000.0, 0.0)),
                (101.0, "p-line", np.where(FIRST_HALF, 0.0, 100.0)),
            ]
        )
        result = _compute(monkeypatch, peaks, _targets(ION))

        assert result.loc["iso-1", "sample_peak_intensity_relative"] == 0.0
        assert result.loc["iso-1", "ratio_scan_count"] == 0

    def test_reading_in_blocks_gives_the_same_ratios(self, monkeypatch):
        # Two ions, read one row per block: each row still meets its own anchor
        peaks = _peaks(
            [
                (100.0, "p-a", ANCHOR),
                (101.0, "p-a1", LINE_CENSORED),
                (200.0, "p-b", np.full(N_SCANS, 500.0)),
                (201.0, "p-b1", np.where(FIRST_HALF, 100.0, 0.0)),
            ]
        )
        targets = _targets(
            [("a", 100.0, 1.0), ("a", 101.0, 0.1), ("b", 200.0, 1.0), ("b", 201.0, 0.2)]
        )
        monkeypatch.setattr(isotopes, "SCAN_RATIO_BLOCK_ROWS", 1)
        result = _compute(monkeypatch, peaks, targets)

        np.testing.assert_allclose(
            result["sample_peak_intensity_relative"].to_numpy(), [1.0, 0.1, 1.0, 0.2]
        )
        np.testing.assert_array_equal(
            result["ratio_scan_count"].to_numpy(), [20, 10, 20, 10]
        )


class TestTofIsUntouched:
    def test_a_tof_series_keeps_the_mean_over_mean(self, monkeypatch):
        result = _compute(
            monkeypatch, _censored_peaks(), _targets(ION), instrument_type="tof"
        )

        assert result.loc["iso-1", "sample_peak_intensity_relative"] == pytest.approx(
            0.05
        )
        assert result["ratio_scan_count"].isna().all()


class TestStoredAnchor:
    """An ion matched in an earlier run reads against the stored anchor."""

    def _reference(self, **extra) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "target_ion_id": ["ion"],
                "sample_peak_intensity": [1000.0],
                "relative_abundance": [1.0],
                **extra,
            }
        )

    def test_the_stored_anchor_is_loaded_and_read_scan_by_scan(self, monkeypatch):
        # Only the 13C line is a target now; its anchor is the stored peak
        result = _compute(
            monkeypatch,
            _censored_peaks(),
            _targets([("ion", 101.0, 0.1)]),
            existing_reference_df=self._reference(
                sample_peak_id=["p-anchor"], sample_peak_mz=[100.0]
            ),
        )

        assert 100.0 in result.attrs["loaded_mzs"]
        assert result.loc["iso-0", "sample_peak_intensity_relative"] == pytest.approx(
            0.1
        )
        assert result.loc["iso-0", "ratio_scan_count"] == 10
        # The reference's own columns do not leak into the row's
        assert result.loc["iso-0", "sample_peak_mz"] == 101.0
        assert result.loc["iso-0", "sample_peak_id"] == "p-line"

    def test_a_stored_anchor_not_on_the_axis_falls_back_to_its_intensity(
        self, monkeypatch
    ):
        result = _compute(
            monkeypatch,
            _censored_peaks(),
            _targets([("ion", 101.0, 0.1)]),
            existing_reference_df=self._reference(
                sample_peak_id=["p-gone"], sample_peak_mz=[100.0]
            ),
        )

        assert result.loc["iso-0", "sample_peak_intensity_relative"] == pytest.approx(
            50.0 / 1000.0
        )
        assert np.isnan(result.loc["iso-0", "ratio_scan_count"])

    def test_a_reference_without_its_peak_falls_back_to_its_intensity(
        self, monkeypatch
    ):
        result = _compute(
            monkeypatch,
            _censored_peaks(),
            _targets([("ion", 101.0, 0.1)]),
            existing_reference_df=self._reference(),
        )

        assert result.loc["iso-0", "sample_peak_intensity_relative"] == pytest.approx(
            0.05
        )
        assert np.isnan(result.loc["iso-0", "ratio_scan_count"])


class TestPresentScanRatios:
    def test_an_anchor_present_in_no_scan_is_not_read(self):
        peaks = _peaks([(100.0, "p-anchor", ZERO), (101.0, "p-line", LINE_CENSORED)])
        frame = pd.DataFrame(
            {
                "target_ion_id": ["ion"],
                "relative_abundance": [0.1],
                "matched_peak_idx": pd.array([0], dtype="Int64"),
            }
        )
        reference = pd.DataFrame(
            {"target_ion_id": ["ion"], "sample_peak_id": ["p-anchor"]}
        )

        result = present_scan_ratios(frame, peaks, np.array([1]), reference)

        assert result.isna().all().all()

    def test_unmatched_rows_and_ions_are_not_read(self):
        peaks = _censored_peaks()
        frame = pd.DataFrame(
            {
                "target_ion_id": ["ion", "ion", "lone"],
                "relative_abundance": [1.0, 0.1, 1.0],
                # The ion's anchor is unmatched; so is the lone ion's only line
                "matched_peak_idx": pd.array([pd.NA, 1, pd.NA], dtype="Int64"),
            },
            index=[7, 8, 9],
        )

        result = present_scan_ratios(frame, peaks, np.array([1, 2]))

        assert list(result.index) == [7, 8, 9]
        assert result.isna().all().all()


def test_calculate_match_stats_without_scan_ratios_leaves_the_count_alone():
    frame = pd.DataFrame(
        {
            "target_ion_id": ["ion", "ion"],
            "relative_abundance": [1.0, 0.5],
            "sample_peak_intensity": [20.0, 10.0],
            "mz": [100.0, 101.0],
            "sample_peak_mz": [100.0, 101.0],
        }
    )

    result = calculate_match_stats(frame)

    assert "ratio_scan_count" not in result
