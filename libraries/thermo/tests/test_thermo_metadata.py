"""Characterization tests for the metadata classes on the Thermo backend.

These pin the *contract* (shape + internal consistency) of ``RawFileMetadata``
and ``RawFileMetadataLegacy`` so the reader backend can't silently change it.
They run against the committed KORBI file so they work
on a fresh clone, and assert structural invariants rather than values tied to a
specific acquisition.
"""

import numpy as np
import pandas as pd
import pytest
from thermo_test_support import POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import OPENTFRAW_UNAVAILABLE_SCAN_STATS, SCAN_STAT_FIELDS


# Run every test under each reader backend.
# setup_method here only *constructs* metadata objects (no backend call), so the
# backend-reading property accesses happen in the test bodies, after the env var
# is set by the `backend` fixture.
pytestmark = pytest.mark.usefixtures("backend")


INSTRUMENT_KEYS = {
    "Name",
    "Model",
    "SerialNumber",
    "SoftwareVersion",
    "HardwareVersion",
    "Flags",
    "AxisLabelX",
    "AxisLabelY",
    "IsValid",
    "HasAccurateMassPrecursors",
}


class TestRawFileMetadata:
    """Base ``RawFileMetadata`` accessors (default ``scan_type="Ms"``)."""

    def setup_method(self):
        self.md = m_thermo.RawFileMetadata(POS_ORBI_FILE_PATH)

    def test_instrument_details(self, backend):
        details = self.md.instrument_details
        assert set(details) == INSTRUMENT_KEYS
        assert isinstance(details["Model"], str) and details["Model"]
        if backend == "thermo":
            # OpenTFRaw detects only the model; it does not parse the InstID
            # block, so serial / software / hardware are absent (None).
            assert details["SerialNumber"]  # non-empty

    def test_scan_acquisition_settings_aligned(self):
        acq = self.md.scan_acquisition_settings
        assert set(acq) == {"header_labels", "settings"}
        assert acq["header_labels"]  # non-empty label table
        assert acq["settings"]  # at least one scan
        # Every settings row aligns 1:1 with the label table.
        n_labels = len(acq["header_labels"])
        for values in acq["settings"].values():
            assert len(values) == n_labels

    def test_scan_statistics_shape(self, backend):
        stats = self.md.scan_statistics
        assert stats
        # Every backend returns every field; the ones it cannot read are None.
        for row in stats.values():
            assert list(row) == [*SCAN_STAT_FIELDS, "MsType"]
        sample = next(iter(stats.values()))
        for key in (
            "TIC",
            "StartTime",
            "BasePeakMass",
            "BasePeakIntensity",
            "LowMass",
            "HighMass",
            "ScanNumber",
            "ScanType",
            "IsCentroidScan",
        ):
            assert sample[key] is not None
        if backend == "opentfraw":
            unavailable = {key for key, value in sample.items() if value is None}
            assert unavailable == set(OPENTFRAW_UNAVAILABLE_SCAN_STATS)
        # Default scan_type="Ms" selects MS1 scans only.
        assert all(row["MsType"] == "Ms" for row in stats.values())

    def test_settings_and_statistics_cover_same_scans(self):
        # Both accessors select the same scan set (default scan_type="Ms").
        assert len(self.md.scan_acquisition_settings["settings"]) == len(
            self.md.scan_statistics
        )


class TestRawFileMetadataLegacy:
    """``RawFileMetadataLegacy`` adds DataFrame views, ``centroids_meta`` and
    ``to_dict``.
    """

    def setup_method(self):
        self.leg = m_thermo.RawFileMetadataLegacy(POS_ORBI_FILE_PATH)

    def test_num_of_scans_positive(self):
        assert self.leg.num_of_scans > 0

    def test_instrument_dataframe(self):
        df = self.leg.instrument
        assert isinstance(df, pd.DataFrame)
        assert "Model" in df.index
        assert df.loc["Model", "Value"]  # non-empty

    def test_trailer_and_statistics_dataframes(self):
        assert isinstance(self.leg.trailer, pd.DataFrame)
        assert isinstance(self.leg.statistics, pd.DataFrame)
        # Statistics has one column per selected (MS1) scan.
        assert self.leg.statistics.shape[1] == len(self.leg.scan_statistics)

    def test_centroids_meta_covers_all_scans(self):
        cm = self.leg.centroids_meta
        assert set(cm) == {"time", "data"}
        # centroids_meta scans all spectra (ms_type=None).
        assert len(cm["time"]) == self.leg.num_of_scans
        assert len(cm["data"]) == self.leg.num_of_scans

    def test_centroids_meta_peak_fields_aligned_and_finite(self):
        cm = self.leg.centroids_meta
        nonempty = [d for d in cm["data"] if d["mzs"]]
        assert nonempty, "expected at least one scan with centroids"
        for d in nonempty:
            assert set(d) == {"intensities", "mzs", "resolutions", "noises"}
            n = len(d["mzs"])
            assert len(d["intensities"]) == n
            assert len(d["resolutions"]) == n
            assert len(d["noises"]) == n
            # centroids_meta filters to finite, positive resolution & intensity.
            res = np.asarray(d["resolutions"])
            inten = np.asarray(d["intensities"])
            assert np.all(np.isfinite(res)) and np.all(res > 0)
            assert np.all(np.isfinite(inten)) and np.all(inten > 0)

    def test_to_dict_bundle(self):
        td = self.leg.to_dict()
        assert set(td) == {
            "num_of_scans",
            "stats_per_scan",
            "stats_per_file",
            "centroids_meta",
        }
        assert td["num_of_scans"] == self.leg.num_of_scans
        assert len(td["centroids_meta"]["time"]) == self.leg.num_of_scans
