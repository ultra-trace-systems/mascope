"""The axis fingerprint of a raw file (``mascope_thermo.axis_fingerprint``).

No committed raw file comes from an instrument with frequency bands, and CI
holds no other, so a scripted acquisition stands in for OpenTFRaw's
``RawFile`` behind the real :class:`OpenTFRawBackend`. Its profile scans are
made as the instrument stores them: runs of points on the FFT bins of a
2^-12 kHz step, converted with ``m = B/f^2 + C/f^4``, each run carrying one
m/z correction, a ratio the test sets per frequency band. Inverted with the
law, the points of a run are spaced off the bin by
``s = ((1 + r 1e-6)^-1.5 - 1) 1e6``, which is what the fingerprint measures.

The real-file check runs outside CI: the reference documents the algorithm
came with, against what this module computes from the same raw files.
"""

import json
import logging
import re

import jsonschema
import numpy as np
import pytest

import mascope_thermo.axis_fingerprint as m_fingerprint
from mascope_thermo.axis_fingerprint import (
    LOCK_KEYS,
    PARAMS,
    compute_axis_fingerprint,
    describe_scans,
    reference,
    validate_document,
)
from mascope_thermo.backend import _SCAN_INDEX_UNSET, OpenTFRawBackend
from mascope_thermo.streams import scan_streams
from mascope_thermo.thermo import NoScansFoundError


# A Q Exactive's conversion parameters, and the frequency bands of its axis.
B = 67844557.975
C = -339908079.8472
STEP_KHZ = 2.0**-12
PERIOD_KHZ = 16.2782
PHASE_KHZ = 8.1431
# The bands the scripted profiles cover, whole: m/z 57 to 690 or so.
FIRST_BAND, LAST_BAND = 19, 66
# Runs of stored points per band, centred in it, and their spacing.
RUNS_PER_BAND = 40
RUN_SPACING_KHZ = 0.4
POINTS_PER_RUN = 8

LOCK = 61.98837
SECOND_LOCK = 124.98401
REFERENCE_BIT = 1 << 21

FULL_MS = "FTMS - p NSI Full ms [50.0000-750.0000]"
FULL_MS_CENTROID = "FTMS - c NSI Full ms [50.0000-750.0000]"
MS2 = "FTMS - p NSI Full ms2 200.0000@hcd30.00 [50.0000-210.0000]"
SIM = "FTMS - p NSI SIM ms [100.0000-200.0000]"
MODE = "-|R280000|50.0000-750.0000"
STREAM = "FTMS - p NSI Full ms [50.0000-750.0000] R=280000"

UNAVAILABLE = {
    "schema": "mascope.axis_fingerprint",
    "version": 1,
    "status": "unavailable",
}


def law(f_khz):
    """m/z at a frequency [kHz]."""
    return B / f_khz**2 + C / f_khz**4


def s_of_ratio(ratio_ppm):
    """The spacing deviation [ppm] of a run whose m/z carry ``ratio_ppm``."""
    return ((1 + ratio_ppm * 1e-6) ** -1.5 - 1) * 1e6


def stepped_ratio(band, run):
    """A correction that steps between every two adjacent bands, constant
    within each: -4 to +4 ppm."""
    return ((band * 7) % 11) * 0.8 - 4.0


def scattered_ratio(band, run):
    """No steps between bands, and the runs of a band at one of three
    corrections, as on an instrument without frequency bands."""
    return 1.0 + (-0.5, 0.0, 0.5)[run % 3]


def band_profile(ratio=stepped_ratio):
    """A profile scan over the bands FIRST_BAND..LAST_BAND: ``(mz, intensity)``.

    Each run is POINTS_PER_RUN consecutive FFT bins, the m/z of every point
    the law's plus the run's correction, ``ratio(band, run)`` ppm of the
    law's m/z at the run's centre. The runs of a band are spread over its
    middle, away from its edges.
    """
    shape = np.exp(-0.5 * ((np.arange(POINTS_PER_RUN) - 3.5) / 1.5) ** 2)
    mzs, intensities = [], []
    for band in range(FIRST_BAND, LAST_BAND + 1):
        middle = PHASE_KHZ + (band + 0.5) * PERIOD_KHZ
        offsets = (np.arange(RUNS_PER_BAND) - (RUNS_PER_BAND - 1) / 2) * RUN_SPACING_KHZ
        for run, centre in enumerate(middle + offsets):
            first = np.round(centre / STEP_KHZ) * STEP_KHZ
            f = first - np.arange(POINTS_PER_RUN) * STEP_KHZ
            mzs.append(law(f) + ratio(band, run) * 1e-6 * law(centre))
            intensities.append(1e5 * shape)
    return np.concatenate(mzs), np.concatenate(intensities)


def ms1_trailer(locks=(LOCK, SECOND_LOCK), found=1, resolution=280000):
    """An MS1 scan's trailer, its values typed as OpenTFRaw reads them."""
    trailer = {
        "FT Resolution:": resolution,
        "Micro Scan Count:": 1,
        "Conversion Parameter B:": B,
        "Conversion Parameter C:": C,
        "Number of LM Found:": found,
        "LM m/z-Correction (ppm):": 0.5,
        "Lock Mass #3 (m/z):": 0.0,
    }
    for i, mz in enumerate(locks, start=1):
        trailer[f"Lock Mass #{i} (m/z):"] = mz
    return trailer


class Scan:
    """One scripted scan: its filter, trailer, centroids and profile.

    ``centroids`` are ``(mz, reference)`` pairs, ``reference`` whether the
    centroid is flagged as the lock mass the scan was corrected with.
    """

    def __init__(
        self,
        filter_string=FULL_MS,
        trailer=None,
        centroids=((LOCK, True), (200.0, False), (300.0, False)),
        profile=None,
        event=_SCAN_INDEX_UNSET,
    ):
        self.filter_string = filter_string
        self.trailer = ms1_trailer() if trailer is None else trailer
        self.centroids = centroids
        self.profile = profile
        self.event = event


class ScriptedRawFile:
    """The slice of ``opentfraw.RawFile`` the fingerprint reads, with a record
    of what was read."""

    instrument_model = "Q Exactive Plus"

    def __init__(self, scans, start_time=0.01, end_time=None, first_scan=1):
        self.scans = list(scans)
        self.first_scan = first_scan
        self.start_time = start_time
        self.end_time = len(self.scans) * 0.2 if end_time is None else end_time
        self.calls = []

    def _scan(self, number):
        return self.scans[number - self.first_scan]

    def scan_table(self):
        self.calls.append(("scan_table",))
        n = len(self.scans)
        return {
            "scan_number": list(range(self.first_scan, self.first_scan + n)),
            "scan_event": [scan.event for scan in self.scans],
            "scan_segment": [
                _SCAN_INDEX_UNSET if scan.event == _SCAN_INDEX_UNSET else 0
                for scan in self.scans
            ],
            "data_size": [0] * n,
            "ms_level": [2 if " ms2 " in s.filter_string else 1 for s in self.scans],
            "polarity": ["+" if " + " in s.filter_string else "-" for s in self.scans],
            "retention_time": [self.start_time + 0.2 * i for i in range(n)],
            "filter_string": [scan.filter_string for scan in self.scans],
            "total_ion_current": [1e6] * n,
            "base_peak_mz": [200.0] * n,
            "base_peak_intensity": [1e5] * n,
            "low_mz": [50.0] * n,
            "high_mz": [750.0] * n,
        }

    def scan_parameters(self, number):
        return dict(self._scan(number).trailer)

    def centroid_labels(self, number):
        self.calls.append(("centroid_labels", number))
        centroids = self._scan(number).centroids
        n = len(centroids)
        return {
            "mz": np.asarray([mz for mz, _ in centroids], dtype=np.float64),
            "intensity": np.full(n, 1e5, dtype=np.float32),
            "resolution": np.full(n, 140000.0, dtype=np.float32),
            "noise": np.full(n, 10.0, dtype=np.float32),
            "baseline": np.full(n, 1.0, dtype=np.float32),
            "signal_to_noise": np.full(n, 50.0, dtype=np.float32),
            "descriptor": np.asarray(
                [
                    i | (REFERENCE_BIT if ref else 0)
                    for i, (_, ref) in enumerate(centroids)
                ],
                dtype=np.uint32,
            ),
        }

    def profile(self, number):
        self.calls.append(("profile", number))
        profile = self._scan(number).profile
        if profile is None:
            return np.zeros(0), np.zeros(0, dtype=np.float32)
        return profile

    def controllers(self):
        return [
            {"index": 0, "is_ms_controller": False, "start_time": 0.0, "end_time": 0.0},
            {
                "index": 1,
                "is_ms_controller": True,
                "start_time": self.start_time,
                "end_time": self.end_time,
            },
        ]

    def read(self, what):
        """The scans read for ``what``, in order: ``"profile"``, ``"centroid_labels"``."""
        return [call[1] for call in self.calls if call[0] == what]


@pytest.fixture
def raw_path(tmp_path):
    """A file on disk for the document's source: its name, size and digest."""
    path = tmp_path / "scripted.raw"
    path.write_bytes(bytes(range(256)) * 16)
    return path


def fingerprint(raw_path, scans, **raw):
    """The document of a scripted acquisition, and the scripted file read."""
    backend = OpenTFRawBackend(str(raw_path))
    backend._raw = ScriptedRawFile(scans, **raw)
    return compute_axis_fingerprint(backend, reader_name="opentfraw"), backend._raw


def profile_scans(n=4, ratio=stepped_ratio, **kwargs):
    """``n`` profile-mode MS1 scans on one calibration."""
    profile = band_profile(ratio)
    return [Scan(profile=profile, **kwargs) for _ in range(n)]


@pytest.fixture(scope="module")
def stepped(tmp_path_factory):
    """The document of four MS1 scans whose axis steps between bands."""
    path = tmp_path_factory.mktemp("stepped") / "stepped.raw"
    path.write_bytes(b"\0" * 4096)
    document, _ = fingerprint(path, profile_scans())
    return document


def edge(band):
    """The lower edge of a band [kHz]."""
    return PHASE_KHZ + band * PERIOD_KHZ


def assert_valid(document):
    """Valid against the schema, and storable as JSON as it is."""
    validate_document(document)
    assert json.loads(json.dumps(document, allow_nan=False)) == document


# ---- what the bands give back ------------------------------------------------


def test_the_band_corrections_come_back_as_their_spacing_deviation(stepped):
    assert stepped["status"] == "ok"
    (mode,) = stepped["modes"]
    rows = [
        dict(zip(mode["segments"]["columns"], row)) for row in mode["segments"]["rows"]
    ]
    assert [row["k"] for row in rows] == list(range(FIRST_BAND, LAST_BAND + 1))
    assert mode["k_range_measured"] == [FIRST_BAND, LAST_BAND]
    for row in rows:
        assert not row["interp"]
        expected = s_of_ratio(stepped_ratio(row["k"], 0))
        assert abs(row["s_ppm"] - expected) < 1e-3, row


def test_the_band_grid_is_measured_at_its_period_and_edges(stepped):
    (mode,) = stepped["modes"]
    grid = mode["grid"]
    assert grid["source"] == "measured"
    assert grid["significant"] is True
    assert grid["period_hypothesis"] == "fixed_frequency"
    assert abs(grid["period_khz"] / PERIOD_KHZ - 1) < 1e-4
    for band in range(FIRST_BAND, LAST_BAND + 2):
        fitted = grid["phase_khz"] + band * grid["period_khz"]
        assert abs(fitted - edge(band)) < 0.1, band
    assert "grid_unmeasured" not in mode["flags"]
    assert "not_stepwise" not in mode["flags"]


def test_a_bracket_holds_each_band_edge_and_its_step(stepped):
    (mode,) = stepped["modes"]
    brackets = mode["brackets"]["rows"]
    # Every edge between two covered bands, from the highest frequency down
    assert len(brackets) == LAST_BAND - FIRST_BAND
    for (f_lo, f_hi, step), band in zip(brackets, range(LAST_BAND, FIRST_BAND, -1)):
        assert f_lo < edge(band) < f_hi
        assert f_hi - f_lo < 1.0
        below = s_of_ratio(stepped_ratio(band - 1, 0))
        above = s_of_ratio(stepped_ratio(band, 0))
        assert abs(step - (below - above)) < 1e-3


def test_the_nominal_fft_step_is_the_bin(stepped):
    (mode,) = stepped["modes"]
    assert mode["profile"]["nominal_fft_step_hz"] == STEP_KHZ * 1e3
    assert "fft_step_not_power_of_two" not in mode["flags"]


def test_the_lock_summary_reads_the_lock_band(stepped):
    (mode,) = stepped["modes"]
    lock = mode["lock"]
    assert lock["method"] == "reference_flag"
    assert lock["used_share"] == {"61.9884": 1.0}
    # Read off the lock's label, at its four decimals, as the reference does
    assert lock["m_lock_eff"] == 61.9884
    lock_band = int(np.floor((reference.law_khz(LOCK, B, C) - PHASE_KHZ) / PERIOD_KHZ))
    assert lock["k_lock"] == {"61.9884": lock_band}
    assert abs(lock["s_lock_eff_ppm"] - s_of_ratio(stepped_ratio(lock_band, 0))) < 1e-3


def test_a_flat_table_measures_no_grid_and_no_steps(raw_path):
    document, _ = fingerprint(raw_path, profile_scans(ratio=scattered_ratio))
    assert_valid(document)
    (mode,) = document["modes"]
    assert {"grid_unmeasured", "not_stepwise"} <= set(mode["flags"])
    assert mode["grid"]["source"] == "seed"
    assert mode["brackets"]["rows"] == []


# ---- the census ----------------------------------------------------------------


def test_the_document_records_the_census_and_the_file(raw_path):
    # Scans numbered from 11, and start times the scan table rounds
    document, _ = fingerprint(
        raw_path, profile_scans(), start_time=0.0123456789, end_time=9.5, first_scan=11
    )
    assert_valid(document)
    assert document["source"] == {
        "raw_name": "scripted.raw",
        "raw_size_bytes": 4096,
        "raw_sha1_first_mib": _sha1(raw_path),
        "first_scan": 11,
        "last_scan": 14,
        "n_scans": 4,
        "start_time_min": 0.01235,
        "end_time_min": 9.5,
        "instrument_model": "Q Exactive Plus",
    }
    assert document["scan_table"]["rows"][1] == [
        12,
        0.21235,
        MODE,
        1,
        B,
        C,
        1,
        0.5,
        "61.9884",
    ]
    assert document["extractor"]["name"] == "mascope_thermo.axis_fingerprint"
    assert document["extractor"]["reader"] == f"opentfraw {_opentfraw_version()}"
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", document["extractor"]["created_utc"]
    )
    assert set(document["cost_seconds"]) == {
        "census",
        "locks",
        "profile_read",
        "compute",
    }
    assert document["extractor"]["params"] == PARAMS
    assert document["dominant_mode"] == MODE
    assert document["flags"] == []


def test_a_reader_that_names_no_model_records_none(raw_path):
    backend = OpenTFRawBackend(str(raw_path))
    backend._raw = ScriptedRawFile(profile_scans())
    backend._raw.instrument_model = None
    document = compute_axis_fingerprint(backend, reader_name="opentfraw")
    assert_valid(document)
    assert "instrument_model" not in document["source"]


def _sha1(path):
    import hashlib

    return hashlib.sha1(path.read_bytes()[: 1 << 20]).hexdigest()


def _opentfraw_version():
    import opentfraw

    return opentfraw.__version__


def test_ms2_and_sim_scans_belong_to_no_mode(raw_path):
    scans = profile_scans(4)
    scans.insert(2, Scan(MS2, profile=scans[0].profile))
    scans.insert(4, Scan(SIM, profile=scans[0].profile))
    document, raw = fingerprint(raw_path, scans)
    assert_valid(document)
    modes = {row[0]: row[2] for row in document["scan_table"]["rows"]}
    assert modes == {1: MODE, 2: MODE, 3: None, 4: MODE, 5: None, 6: MODE}
    assert document["flags"] == ["scans_outside_modes"]
    (mode,) = document["modes"]
    assert mode["n_scans"] == 4
    assert 3 not in raw.read("profile") and 5 not in raw.read("profile")


def test_a_centroid_only_file_has_no_fingerprint(raw_path):
    scans = [Scan(FULL_MS_CENTROID) for _ in range(4)]
    document, raw = fingerprint(raw_path, scans)
    assert_valid(document)
    assert document["status"] == "no_fingerprint"
    (mode,) = document["modes"]
    assert mode["flags"] == ["centroid_only"]
    assert mode["segments"] is None
    assert raw.read("profile") == []


def test_modes_are_the_resolution_and_scan_range_most_scans_first(raw_path):
    profile = band_profile()
    low = ms1_trailer(resolution=70000)
    scans = [
        Scan(profile=profile, trailer=low),
        Scan(profile=profile),
        Scan(profile=profile),
        Scan(profile=profile, trailer=low),
        Scan(profile=profile),
    ]
    document, _ = fingerprint(raw_path, scans)
    assert_valid(document)
    assert [m["mode_id"] for m in document["modes"]] == [
        MODE,
        "-|R70000|50.0000-750.0000",
    ]
    assert document["dominant_mode"] == MODE
    assert [m["scan_share"] for m in document["modes"]] == [0.6, 0.4]
    assert document["flags"] == ["mixed_resolution"]
    # The file's first scan is the one no pick takes, not each mode's first
    assert [m["profile"]["scans_used"] for m in document["modes"]] == [[2, 3, 5], [4]]


def test_modes_of_both_polarities_mark_the_file(raw_path):
    profile = band_profile()
    positive = "FTMS + p NSI Full ms [50.0000-750.0000]"
    scans = [Scan(profile=profile) for _ in range(9)]
    scans.append(Scan(positive, profile=profile))
    document, _ = fingerprint(raw_path, scans)
    assert_valid(document)
    assert [m["mode_id"] for m in document["modes"]] == [
        MODE,
        "+|R280000|50.0000-750.0000",
    ]
    assert document["flags"] == ["mixed_polarity"]


def test_a_file_of_no_ms1_full_scans_has_no_modes(raw_path):
    document, raw = fingerprint(raw_path, [Scan(MS2), Scan(SIM)])
    assert_valid(document)
    assert document["status"] == "no_fingerprint"
    assert document["modes"] == []
    assert document["dominant_mode"] is None
    assert document["flags"] == ["scans_outside_modes"]
    assert raw.read("profile") == []


@pytest.mark.parametrize(
    "label",
    ["FT Resolution:", "Conversion Parameter B:", "Conversion Parameter C:"],
)
def test_a_scan_without_resolution_or_calibration_belongs_to_no_mode(raw_path, label):
    scans = profile_scans(4)
    trailer = ms1_trailer()
    del trailer[label]
    scans.append(Scan(trailer=trailer, profile=scans[0].profile))
    document, _ = fingerprint(raw_path, scans)
    assert document["scan_table"]["rows"][4][2] is None
    assert document["flags"] == ["scans_outside_modes"]


def test_a_mode_names_the_streams_of_its_scans(raw_path):
    document, _ = fingerprint(raw_path, profile_scans())
    (mode,) = document["modes"]
    assert mode["stream_keys"] == [STREAM]


def test_a_mode_names_every_stream_its_scans_come_from(raw_path):
    # One filter from two experiments of the method, alternating
    profile = band_profile()
    scans = [Scan(profile=profile, event=i % 2) for i in range(6)]
    backend = OpenTFRawBackend(str(raw_path))
    backend._raw = ScriptedRawFile(scans)
    document = compute_axis_fingerprint(backend, reader_name="opentfraw")
    (mode,) = document["modes"]
    assert mode["stream_keys"] == [f"{STREAM} event=1", f"{STREAM} event=2"]
    assert mode["stream_keys"] == [s["key"] for s in scan_streams(backend)]


def test_the_scan_pick_reads_at_most_eight_profiles_per_mode(raw_path):
    document, raw = fingerprint(raw_path, profile_scans(20))
    (mode,) = document["modes"]
    # The file's first scan is left out, and the other 19 spread evenly
    assert mode["profile"]["scans_used"] == [2, 5, 7, 10, 12, 15, 17, 20]
    assert raw.read("profile") == mode["profile"]["scans_used"]


def test_a_scan_that_found_no_lock_is_picked_only_without_others(raw_path):
    profile = band_profile()
    scans = [
        Scan(profile=profile),
        Scan(profile=profile, trailer=ms1_trailer(found=0)),
        Scan(profile=profile),
        Scan(profile=profile),
    ]
    document, _ = fingerprint(raw_path, scans)
    (mode,) = document["modes"]
    assert mode["profile"]["scans_used"] == [3, 4]


# ---- the lock mass used per scan -------------------------------------------


@pytest.mark.parametrize(
    ("centroids", "used"),
    [
        (((LOCK, True), (200.0, False)), "61.9884"),
        (((SECOND_LOCK, True), (200.0, False)), "124.9840"),
        (((LOCK, True), (SECOND_LOCK, True)), "both"),
        # The flag decides, not the m/z: a centroid at the lock that is not
        # flagged was not corrected with, and one flagged 10 ppm off was.
        (((LOCK, False), (SECOND_LOCK, False)), "none"),
        (((LOCK * (1 + 10e-6), True),), "61.9884"),
        # Flagged, but at no configured lock
        (((LOCK * (1 + 30e-6), True),), "none"),
    ],
    ids=["first", "second", "both", "unflagged", "flagged-off-mass", "no-lock-near"],
)
def test_the_lock_used_is_the_centroid_flagged_reference(raw_path, centroids, used):
    scans = profile_scans(4)
    scans[1] = Scan(centroids=centroids, profile=scans[0].profile)
    document, _ = fingerprint(raw_path, scans)
    assert_valid(document)
    assert document["scan_table"]["rows"][1][8] == used


def test_a_scan_that_names_no_lock_mass_used_none_unread(raw_path):
    scans = profile_scans(4)
    scans[2] = Scan(trailer=ms1_trailer(locks=()), profile=scans[0].profile)
    document, raw = fingerprint(raw_path, scans)
    assert document["scan_table"]["rows"][2][8] == "none"
    assert 3 not in raw.read("centroid_labels")
    assert raw.read("centroid_labels") == [1, 1, 2, 2, 4, 4]


def test_the_configured_locks_are_every_scans(raw_path):
    profile = band_profile()
    scans = [
        Scan(profile=profile, trailer=ms1_trailer(locks=(SECOND_LOCK,))),
        Scan(profile=profile, trailer=ms1_trailer(locks=(LOCK,))),
        Scan(profile=profile, trailer=ms1_trailer(locks=(SECOND_LOCK,))),
    ]
    document, _ = fingerprint(raw_path, scans)
    (mode,) = document["modes"]
    assert mode["lock"]["configured_mz"] == [LOCK, SECOND_LOCK]


# ---- the scans of a sample ---------------------------------------------------

#: The centroids of a scan corrected with each lock, both or neither.
CORRECTED_WITH = {
    "first": ((LOCK, True), (200.0, False)),
    "second": ((SECOND_LOCK, True), (200.0, False)),
    "both": ((LOCK, True), (SECOND_LOCK, True)),
    "none": ((LOCK, False), (SECOND_LOCK, False)),
}

NO_LOCK = {
    "used_share": {},
    "used_count": {},
    "m_lock_eff": None,
    "s_lock_eff_ppm": None,
}


def lock_band(lock):
    """The frequency band a lock mass sits in."""
    return int(np.floor((reference.law_khz(lock, B, C) - PHASE_KHZ) / PERIOD_KHZ))


@pytest.fixture(scope="module")
def alternating(tmp_path_factory):
    """The document of eight scans corrected with either lock, both or none:
    scans 2, 5 and 8 with the second."""
    path = tmp_path_factory.mktemp("alternating") / "alternating.raw"
    path.write_bytes(b"\0" * 4096)
    profile = band_profile()
    used = ["first", "second", "first", "both", "second", "none", "first", "second"]
    document, _ = fingerprint(
        path, [Scan(centroids=CORRECTED_WITH[name], profile=profile) for name in used]
    )
    return document


@pytest.fixture(scope="module")
def two_resolutions(tmp_path_factory):
    """The document of five scans in two modes: scans 2, 3 and 5 at 280k,
    1 and 4 at 70k, each its own stream."""
    path = tmp_path_factory.mktemp("two_resolutions") / "two_resolutions.raw"
    path.write_bytes(b"\0" * 4096)
    profile = band_profile()
    low = ms1_trailer(resolution=70000)
    document, _ = fingerprint(
        path,
        [
            Scan(profile=profile, trailer=low),
            Scan(profile=profile),
            Scan(profile=profile),
            Scan(profile=profile, trailer=low),
            Scan(profile=profile),
        ],
    )
    return document


def test_every_scan_of_a_mode_has_the_modes_lock(alternating):
    (mode,) = alternating["modes"]
    # Every share, and an effective lock weighed over both locks
    assert set(mode["lock"]["used_share"]) == {"61.9884", "124.9840", "both", "none"}
    assert mode["lock"]["s_lock_eff_ppm"] is not None

    every = [row[0] for row in alternating["scan_table"]["rows"]]
    described = describe_scans(alternating, every, polarity="-", stream_keys=[STREAM])

    assert described["mode_id"] == MODE
    assert described["lock"] == {key: mode["lock"][key] for key in LOCK_KEYS}
    assert described["modes"] == alternating["modes"]


def test_some_scans_weigh_the_locks_they_used(alternating):
    described = describe_scans(alternating, [2, 5, 8])

    assert described["mode_id"] == MODE
    assert described["lock"]["used_share"] == {"124.9840": 1.0}
    assert described["lock"]["used_count"] == {"124.9840": 3}
    assert described["lock"]["m_lock_eff"] == 124.984
    expected = s_of_ratio(stepped_ratio(lock_band(SECOND_LOCK), 0))
    assert abs(described["lock"]["s_lock_eff_ppm"] - expected) < 1e-3
    # The other lock sits in another band, at another deviation
    assert lock_band(LOCK) != lock_band(SECOND_LOCK)
    assert abs(described["lock"]["s_lock_eff_ppm"] - expected) < abs(
        s_of_ratio(stepped_ratio(lock_band(LOCK), 0)) - expected
    )


def test_a_scan_of_each_lock_shares_them(alternating):
    lock = describe_scans(alternating, [1, 2, 4, 6])["lock"]

    assert lock["used_share"] == {
        "124.9840": 0.25,
        "61.9884": 0.25,
        "both": 0.25,
        "none": 0.25,
    }
    assert lock["used_count"] == {"124.9840": 1, "61.9884": 1, "both": 1, "none": 1}
    assert LOCK < lock["m_lock_eff"] < SECOND_LOCK


def test_a_scan_of_both_locks_weighs_the_configured_ones_half_each(alternating):
    # Scan 1 used the first lock and scan 4 both: the first weighs one and
    # a half, the second a half, as the mode's configured locks name them
    lock = describe_scans(alternating, [1, 4])["lock"]

    assert lock["used_count"] == {"61.9884": 1, "both": 1}
    assert lock["m_lock_eff"] == pytest.approx(0.75 * LOCK + 0.25 * SECOND_LOCK)
    expected = 0.75 * s_of_ratio(stepped_ratio(lock_band(LOCK), 0))
    expected += 0.25 * s_of_ratio(stepped_ratio(lock_band(SECOND_LOCK), 0))
    assert abs(lock["s_lock_eff_ppm"] - expected) < 1e-3


def test_scans_of_no_mode_have_no_lock(raw_path):
    scans = profile_scans(4)
    scans.insert(2, Scan(MS2, profile=scans[0].profile))
    scans.insert(4, Scan(SIM, profile=scans[0].profile))
    document, _ = fingerprint(raw_path, scans)

    # The MS2 and the SIM scan, and a scan the file does not hold
    for some in ([3, 5], [99], []):
        described = describe_scans(document, some)
        assert described["mode_id"] is None
        assert described["lock"] == NO_LOCK
        assert described["modes"] == document["modes"]
    # Beside a scan of the mode, they add nothing
    lock = describe_scans(document, [3, 4, 5])["lock"]
    assert lock["used_count"] == {"61.9884": 1}


def test_the_mode_most_of_the_scans_belong_to_is_named_and_first(two_resolutions):
    low = "-|R70000|50.0000-750.0000"
    assert [m["mode_id"] for m in two_resolutions["modes"]] == [MODE, low]

    described = describe_scans(two_resolutions, [1, 4, 5])

    assert described["mode_id"] == low
    assert [m["mode_id"] for m in described["modes"]] == [low, MODE]
    # Over the scans of that mode alone
    assert described["lock"]["used_count"] == {"61.9884": 2}
    # The document's own order is left as it was
    assert [m["mode_id"] for m in two_resolutions["modes"]] == [MODE, low]


def test_a_tie_names_the_mode_listed_first(two_resolutions):
    described = describe_scans(two_resolutions, [1, 2])

    assert described["mode_id"] == MODE
    assert described["lock"]["used_count"] == {"61.9884": 1}


def test_the_modes_of_the_streams_come_before_the_rest(two_resolutions):
    low = "-|R70000|50.0000-750.0000"
    (low_stream,) = two_resolutions["modes"][1]["stream_keys"]

    described = describe_scans(two_resolutions, [], stream_keys=[low_stream])
    assert described["mode_id"] is None
    assert [m["mode_id"] for m in described["modes"]] == [low, MODE]

    # A stream of none of them reorders nothing
    described = describe_scans(two_resolutions, [], stream_keys=["no such stream"])
    assert [m["mode_id"] for m in described["modes"]] == [MODE, low]


def test_the_modes_of_the_polarity_come_before_the_rest(raw_path):
    profile = band_profile()
    positive = "FTMS + p NSI Full ms [50.0000-750.0000]"
    plus = "+|R280000|50.0000-750.0000"
    scans = [Scan(profile=profile) for _ in range(4)]
    scans.append(Scan(positive, profile=profile))
    document, _ = fingerprint(raw_path, scans)
    assert [m["mode_id"] for m in document["modes"]] == [MODE, plus]

    for some in ([], [5]):
        described = describe_scans(document, some, polarity="+")
        assert [m["mode_id"] for m in described["modes"]] == [plus, MODE]
    assert describe_scans(document, [5], polarity="+")["mode_id"] == plus
    assert describe_scans(document, [], polarity="+")["mode_id"] is None
    for polarity in ("-", None):
        described = describe_scans(document, [], polarity=polarity)
        assert [m["mode_id"] for m in described["modes"]] == [MODE, plus]


# ---- the reader --------------------------------------------------------------


@pytest.mark.parametrize("version", ["1.4.0", "1.3.9", "0.9"])
def test_a_reader_older_than_1_4_1_gives_the_unavailable_document(
    raw_path, monkeypatch, version
):
    import opentfraw

    monkeypatch.setattr(opentfraw, "__version__", version)
    document, raw = fingerprint(raw_path, profile_scans())
    assert document == UNAVAILABLE
    assert raw.calls == []
    assert_valid(document)


def test_a_reader_of_1_4_1_computes(raw_path, monkeypatch):
    import opentfraw

    monkeypatch.setattr(opentfraw, "__version__", "1.4.1")
    document, _ = fingerprint(raw_path, profile_scans())
    assert document["status"] == "ok"
    assert document["extractor"]["reader"] == "opentfraw 1.4.1"


def test_another_reader_backend_gives_the_unavailable_document(raw_path):
    backend = OpenTFRawBackend(str(raw_path))
    backend._raw = ScriptedRawFile(profile_scans())
    assert compute_axis_fingerprint(backend, reader_name="thermo") == UNAVAILABLE
    assert backend._raw.calls == []


def test_a_failure_gives_the_error_document(raw_path):
    class Failing(ScriptedRawFile):
        def profile(self, number):
            raise RuntimeError("unexpected end of file")

    backend = OpenTFRawBackend(str(raw_path))
    backend._raw = Failing(profile_scans())
    document = compute_axis_fingerprint(backend, reader_name="opentfraw")
    assert document == {
        "schema": "mascope.axis_fingerprint",
        "version": 1,
        "status": "error",
        "error": "RuntimeError: unexpected end of file",
    }
    assert_valid(document)


def test_a_profile_the_reader_gives_no_m_z_for_is_an_error(raw_path):
    scans = profile_scans(4)
    mz, intensity = scans[0].profile
    scans[2] = Scan(profile=(np.where(np.arange(mz.size) == 5, np.nan, mz), intensity))
    document, _ = fingerprint(raw_path, scans)
    assert document["status"] == "error"
    assert document["error"].startswith("UnconvertedProfileError: ")


def test_centroid_flags_out_of_step_with_the_centroids_are_an_error(
    raw_path, monkeypatch
):
    monkeypatch.setattr(
        OpenTFRawBackend, "centroid_masses", lambda self, scan: np.zeros(1)
    )
    document, _ = fingerprint(raw_path, profile_scans())
    assert document["status"] == "error"
    assert document["error"].startswith("ValueError: Scan 1 has 1 centroids")


def test_a_document_that_breaks_the_schema_is_an_error(raw_path, monkeypatch):
    real = m_fingerprint._assemble

    def assemble(*args):
        body = real(*args)
        body["dominant_mode"] = 7
        return body

    monkeypatch.setattr(m_fingerprint, "_assemble", assemble)
    document, _ = fingerprint(raw_path, profile_scans())
    assert document["status"] == "error"
    assert document["error"].startswith("ValidationError: ")


def test_a_file_without_scans_is_refused_as_empty(raw_path):
    with pytest.raises(NoScansFoundError):
        fingerprint(raw_path, [])


# ---- the two scan-filter parsers -------------------------------------------


@pytest.mark.parametrize(
    "other",
    [
        MS2,
        SIM,
        "ITMS - c NSI Full ms [50.0000-750.0000]",
        "FTMS p NSI Full ms [50.0000-750.0000]",
        "FTMS - p NSI Full ms [50.0000-300.0000, 280.0000-750.0000]",
        "FTMS {1,1}  - p NSI Full lock ms [50.0000-750.0000]",
    ],
    ids=["ms2", "sim", "ion-trap", "no-polarity", "two-ranges", "thermo-tokens"],
)
def test_scans_the_filter_parsers_read_alike_log_nothing(raw_path, caplog, other):
    scans = profile_scans(4)
    scans.append(Scan(other, profile=scans[0].profile))
    with caplog.at_level(logging.WARNING, logger=m_fingerprint.__name__):
        fingerprint(raw_path, scans)
    assert caplog.records == []


def test_a_filter_the_parsers_read_otherwise_is_logged(raw_path, caplog):
    # The algorithm finds the scan range anywhere, Mascope's parser only at
    # the end of the filter.
    odd = f"{FULL_MS} x"
    scans = profile_scans(4)
    scans[3] = Scan(odd, profile=scans[0].profile)
    with caplog.at_level(logging.WARNING, logger=m_fingerprint.__name__):
        document, _ = fingerprint(raw_path, scans)
    (record,) = caplog.records
    assert "1 of 4 scan filters" in record.getMessage()
    assert repr(odd) in record.getMessage()
    # The document is the algorithm's all the same
    assert document["scan_table"]["rows"][3][2] == MODE


# ---- the schema ----------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        reference.minimal_document("unavailable"),
        reference.minimal_document("missing"),
        reference.minimal_document("error", "OSError: truncated"),
    ],
    ids=["unavailable", "missing", "error"],
)
def test_the_minimal_documents_are_valid(document):
    validate_document(document)


def test_the_stepped_document_is_valid(stepped):
    assert_valid(stepped)


@pytest.mark.parametrize(
    "broken",
    [
        {"schema": "mascope.axis_fingerprint", "version": 2, "status": "ok"},
        {**UNAVAILABLE, "modes": []},
        {"schema": "mascope.axis_fingerprint", "version": 1, "status": "error"},
    ],
    ids=["version", "unavailable-with-more", "error-without-message"],
)
def test_a_document_that_breaks_the_schema_is_refused(broken):
    with pytest.raises(jsonschema.ValidationError):
        validate_document(broken)
