"""Characterization tests for the metadata classes on the Thermo backend.

These pin the *contract* (shape + internal consistency) of ``RawFileMetadata``
and ``RawFileMetadataLegacy`` so the reader backend can't silently change it.
They run against the committed KORBI file so they work
on a fresh clone, and assert structural invariants rather than values tied to a
specific acquisition.
"""

import json
import math

import numpy as np
import pandas as pd
import pytest
from thermo_test_support import POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import (
    MS_SCAN_DETECTOR_STATS,
    OPENTFRAW_UNAVAILABLE_SCAN_STATS,
    SCAN_STAT_FIELDS,
)


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
            "ScanEventNumber",
            "ScanType",
            "IsCentroidScan",
        ):
            assert sample[key] is not None
        # Neither backend reads a non-MS controller, so the detector fields hold
        # the values ScanStats gives every MS scan, types included.
        for row in stats.values():
            for name, value in MS_SCAN_DETECTOR_STATS.items():
                assert row[name] == value and type(row[name]) is type(value), name
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

    def test_to_dict_is_strict_json(self):
        # The API serves to_dict() through json.dumps(allow_nan=False), where a
        # single NaN or infinity anywhere fails the whole response.
        json.dumps(self.leg.to_dict(), allow_nan=False)

    def test_to_dict_keeps_what_the_reader_reports(self):
        # Every value the reader reported arrives unchanged, type included. A
        # value it reports as None (OpenTFRaw's for a trailer section heading,
        # "=== Mass Calibration: ===:") stays None instead of turning into NaN.
        td = self.leg.to_dict()
        stats = self.leg.scan_statistics
        acq = self.leg.scan_acquisition_settings
        assert set(td["stats_per_scan"]) == set(stats)
        for scan, scan_stats in stats.items():
            row = td["stats_per_scan"][scan]
            trailer = dict(zip(acq["header_labels"], acq["settings"][scan]))
            assert set(row) == set(scan_stats) | set(trailer)
            for key, value in {**scan_stats, **trailer}.items():
                assert row[key] == value and type(row[key]) is type(value), key
        details = self.leg.instrument_details
        assert set(td["stats_per_file"]["Value"]) == set(details)
        for key, value in details.items():
            reported = td["stats_per_file"]["Value"][key]
            assert reported == value and type(reported) is type(value), key
        assert td["centroids_meta"] == self.leg.centroids_meta

    def test_to_dict_nulls_non_finite_floats_and_keeps_integers(self):
        # Crafted reader output, no file read: a NaN or infinite float becomes
        # None wherever it sits, while an integer next to a gap stays an
        # integer and text stays text. A DataFrame round trip would give the
        # charge state as 1.0 and the gap as NaN.
        class CraftedMetadata(m_thermo.RawFileMetadataLegacy):
            num_of_scans = 2
            scan_statistics = {
                1: {"TIC": math.inf, "StartTime": 0.5, "MsType": "Ms"},
                2: {"TIC": 10.0, "StartTime": math.nan, "MsType": "Ms"},
            }
            # Scan 1's trailer holds only numbers and gaps, the case pandas
            # infers as float; scan 2's holds text, which pandas keeps as is.
            scan_acquisition_settings = {
                "header_labels": [
                    "Charge State:",
                    "Monoisotopic M/Z:",
                    "Scan Description:",
                ],
                "settings": {1: [1, None, None], 2: [None, 101.5, "NaN"]},
            }
            instrument_details = {"Model": "X", "SerialNumber": None, "IsValid": True}
            centroids_meta = {
                "time": [30.0, -math.inf],
                "data": [
                    {
                        "intensities": [5.0],
                        "mzs": [100.0],
                        "resolutions": [60000.0],
                        "noises": [math.nan],
                    },
                    {"intensities": [], "mzs": [], "resolutions": [], "noises": []},
                ],
            }

        td = CraftedMetadata(POS_ORBI_FILE_PATH).to_dict()
        json.dumps(td, allow_nan=False)
        assert td["stats_per_scan"] == {
            1: {
                "TIC": None,
                "StartTime": 0.5,
                "MsType": "Ms",
                "Charge State:": 1,
                "Monoisotopic M/Z:": None,
                "Scan Description:": None,
            },
            2: {
                "TIC": 10.0,
                "StartTime": None,
                "MsType": "Ms",
                "Charge State:": None,
                "Monoisotopic M/Z:": 101.5,
                "Scan Description:": "NaN",
            },
        }
        assert type(td["stats_per_scan"][1]["Charge State:"]) is int
        assert td["stats_per_file"] == {
            "Value": {"Model": "X", "SerialNumber": None, "IsValid": True}
        }
        assert td["centroids_meta"]["time"] == [30.0, None]
        assert td["centroids_meta"]["data"][0] == {
            "intensities": [5.0],
            "mzs": [100.0],
            "resolutions": [60000.0],
            "noises": [None],
        }
