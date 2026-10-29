"""Centroid flags and scan numbers in the per-scan centroids.

The instrument marks some centroids: the lock-mass peak a scan was corrected
with (Reference), weak side-lobe peaks (Exception), merged peaks (Merged) and
the two lobes of a split weak peak (Fragmented). Both reader backends report
them as the bit values of the vendor library's PeakOptions - OpenTFRaw from
the descriptor word it stores per centroid, the Thermo library from its own
flags - beside every centroid ``centroids_per_scan`` and
``iter_centroids_meta`` return, with the scan's number, and can leave out the
centroids carrying the flags a caller names.

No committed file holds a flagged centroid, so a scripted acquisition is
driven through both backends - OpenTFRaw over a scripted ``RawFile``, the
Thermo library over a scripted adapter, neither needing its real reader - and
every result is asserted on each. ``test_backend_parity`` holds the two real
readers to the same flags on whatever files it is given.
"""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from thermo_test_support import POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import (
    _SCAN_INDEX_UNSET,
    CENTROID_FLAGS,
    OpenTFRawBackend,
    ThermoBackend,
    centroid_flag_mask,
    centroid_flags_from_descriptor,
)


FILTER = "FTMS - p NSI Full ms [50.0000-750.0000]"

# Where OpenTFRaw stores each flag in a centroid's descriptor word.
EXCEPTION_BIT = 1 << 20
REFERENCE_BIT = 1 << 21
MERGED_BIT = 1 << 22
FRAGMENTED_BIT = 1 << 23

# Every scan's centroids: m/z and the flags it carries, as PeakOptions values.
# A fragmented lobe that is also an exception carries both.
PEAKS = [(100.0, 0), (101.0, 16), (102.0, 8), (103.0, 10), (104.0, 4)]
MZS = [mz for mz, _ in PEAKS]
FLAGS = [flags for _, flags in PEAKS]
SCAN_NUMBERS = [1, 2, 3]

_DESCRIPTOR_BITS = {
    8: EXCEPTION_BIT,
    16: REFERENCE_BIT,
    4: MERGED_BIT,
    2: FRAGMENTED_BIT,
}


def _descriptor(index: int, flags: int, charge: int = 0) -> int:
    """A centroid's descriptor word: its position in the peak list in bits
    0-19, its flags in bits 20-23 and a charge state from bit 24."""
    word = index | (charge << 24)
    for value, bit in _DESCRIPTOR_BITS.items():
        if flags & value:
            word |= bit
    return word


class _ScriptedOpenTFRaw:
    """The slice of ``opentfraw.RawFile`` the per-scan centroids read.

    Each scan stores ``PEAKS`` and, after them, one more centroid without FT
    label data (resolution NaN) that is flagged Reference: the backends keep
    only FT label peaks, so it shows in :meth:`centroid_flags`, which reports
    every stored centroid, and nowhere else.
    """

    instrument_model = "Scripted"

    def scan_table(self):
        n = len(SCAN_NUMBERS)
        return {
            "scan_number": list(SCAN_NUMBERS),
            "scan_event": [_SCAN_INDEX_UNSET] * n,
            "scan_segment": [_SCAN_INDEX_UNSET] * n,
            "data_size": [0] * n,
            "ms_level": [1] * n,
            "polarity": ["-"] * n,
            "retention_time": [(s - 1) / 60 for s in SCAN_NUMBERS],
            "filter_string": [FILTER] * n,
            "total_ion_current": [1e6] * n,
            "base_peak_mz": [100.0] * n,
            "base_peak_intensity": [1.0] * n,
            "low_mz": [50.0] * n,
            "high_mz": [750.0] * n,
        }

    def centroid_labels(self, scan_number):
        mzs = [*MZS, 105.0]
        n = len(mzs)
        resolution = np.full(n, 140000.0, dtype=np.float32)
        resolution[-1] = np.nan
        signal_to_noise = np.full(n, 50.0, dtype=np.float32)
        signal_to_noise[-1] = np.nan
        descriptors = [
            _descriptor(i, flags, charge=1 if i == 2 else 0)
            for i, flags in enumerate([*FLAGS, 16])
        ]
        return {
            "mz": np.asarray(mzs, dtype=np.float64),
            "intensity": np.full(n, 1e5, dtype=np.float32),
            "resolution": resolution,
            "noise": np.full(n, 10.0, dtype=np.float32),
            "baseline": np.full(n, 1.0, dtype=np.float32),
            "signal_to_noise": signal_to_noise,
            "descriptor": np.asarray(descriptors, dtype=np.uint32),
        }


class _Text:
    """A .NET enum or filter, as far as ``ToString()`` goes."""

    def __init__(self, text):
        self._text = text

    def ToString(self):  # noqa: N802
        return self._text


class _CentroidStream:
    """A Thermo ``CentroidStream``: its arrays, its flags and its label peaks."""

    def __init__(self, peaks, vendor_flags):
        mzs = np.asarray([mz for mz, _ in peaks], dtype=np.float64)
        self.Length = len(peaks)
        self.Masses = mzs
        self.Intensities = np.full(len(peaks), 1e5)
        self.Resolutions = np.full(len(peaks), 140000.0)
        self.Noises = np.full(len(peaks), 10.0)
        self.Flags = vendor_flags
        self._peaks = [
            SimpleNamespace(
                Mass=mz, Intensity=1e5, Resolution=140000.0, SignalToNoise=50.0
            )
            for mz in mzs
        ]

    def GetLabelPeaks(self):  # noqa: N802
        return self._peaks


class _ScriptedThermoRaw:
    """The slice of the Thermo ``RawFileReaderAdapter`` the per-scan centroids
    read.

    Like the vendor library, it leaves the reference and exception peaks out
    of a scan's centroid stream unless asked for them; the backend opens a
    file with ``IncludeReferenceAndExceptionData`` set, so the scans it hands
    over in a selection carry them (:class:`_ScriptedSelector`). Its flags
    carry a bit the backends do not report (1, Saturated) on the first peak.
    """

    def __init__(self):
        n = len(SCAN_NUMBERS)
        self.RunHeaderEx = SimpleNamespace(
            SpectraCount=n,
            StartTime=0.0,
            EndTime=(n - 1) / 60,
            LowMass=50.0,
            HighMass=750.0,
        )
        self.centroid_reads = []

    def GetFilterForScanNumber(self, number):  # noqa: N802
        scan_filter = _Text(FILTER)
        scan_filter.Polarity = _Text("Negative")
        scan_filter.MSOrder = _Text("Ms")
        return scan_filter

    def GetScanStatsForScanNumber(self, number):  # noqa: N802
        return SimpleNamespace(
            StartTime=(number - 1) / 60,
            TIC=1e6,
            SegmentNumber=0,
            ScanEventNumber=-1,
        )

    def GetCentroidStream(self, number, include_reference_and_exception):  # noqa: N802
        self.centroid_reads.append((number, include_reference_and_exception))
        peaks = [
            (mz, flags)
            for mz, flags in PEAKS
            if include_reference_and_exception or not flags & (8 | 16)
        ]
        vendor_flags = [flags | (1 if mz == 100.0 else 0) for mz, flags in peaks]
        return _CentroidStream(peaks, vendor_flags)


def _opentfraw():
    reader = OpenTFRawBackend("unused.raw")
    reader._raw = _ScriptedOpenTFRaw()
    return reader


class _ScriptedSelector:
    """The backend's ``ScanSelector`` over the scripted adapter, whose
    ``scans`` the vendor library's ``Extensions.GetScans`` would give."""

    def __init__(self, selector, raw):
        self._selector = selector
        self._raw = raw

    def __getattr__(self, name):
        return getattr(self._selector, name)

    @property
    def scans(self):
        return tuple(
            SimpleNamespace(CentroidScan=self._raw.GetCentroidStream(number, True))
            for number in self._selector.scan_indices_1based
        )


def _thermo():
    reader = ThermoBackend("unused.raw")
    reader._raw = _ScriptedThermoRaw()

    def selector(*args, **kwargs):
        return _ScriptedSelector(
            ThermoBackend._selector(reader, *args, **kwargs), reader._raw
        )

    reader._selector = selector
    return reader


@pytest.fixture(params=[_opentfraw, _thermo], ids=["opentfraw", "thermo"])
def reader(request):
    """A reader backend over the scripted acquisition, once per backend."""
    return request.param()


# ---- the flags, decoded --------------------------------------------------


@pytest.mark.parametrize(
    ("bit", "flags"),
    [
        (EXCEPTION_BIT, 8),
        (REFERENCE_BIT, 16),
        (MERGED_BIT, 4),
        (FRAGMENTED_BIT, 2),
    ],
    ids=["exception", "reference", "merged", "fragmented"],
)
def test_each_descriptor_bit_is_its_flag(bit, flags):
    assert centroid_flags_from_descriptor([bit]).tolist() == [flags]


def test_the_peak_index_and_the_charge_are_no_flags():
    words = [0x000FFFFF, 3 << 24, 0xFF << 24, 0]

    assert centroid_flags_from_descriptor(words).tolist() == [0, 0, 0, 0]


def test_a_centroid_carries_every_flag_set_on_it():
    words = [
        EXCEPTION_BIT | FRAGMENTED_BIT | 7,
        EXCEPTION_BIT | REFERENCE_BIT | MERGED_BIT | FRAGMENTED_BIT,
    ]

    assert centroid_flags_from_descriptor(words).tolist() == [10, 30]


def test_a_scan_without_centroids_has_no_flags():
    flags = centroid_flags_from_descriptor(np.zeros(0, dtype=np.uint32))

    assert flags.shape == (0,)


def test_the_flag_values_are_the_vendor_librarys():
    assert CENTROID_FLAGS == {
        "fragmented": 2,
        "merged": 4,
        "exception": 8,
        "reference": 16,
    }


def test_the_named_flags_are_the_excluded_bits():
    assert centroid_flag_mask() == 0
    assert centroid_flag_mask(["reference"]) == 16
    assert centroid_flag_mask(["reference", "exception"]) == 24
    assert centroid_flag_mask([" Exception ", "MERGED"]) == 12


def test_a_name_that_is_no_flag_is_refused():
    with pytest.raises(ValueError, match="'lock'.*reference"):
        centroid_flag_mask(["reference", "lock"])


# ---- in the backends' per-scan centroids --------------------------------


def test_centroid_flags_are_every_stored_centroids_in_order():
    assert _opentfraw().centroid_flags(2).tolist() == [*FLAGS, 16]
    assert _thermo().centroid_flags(2).tolist() == FLAGS


def test_thermo_centroid_flags_count_the_reference_and_exception_centroids():
    reader = _thermo()

    reader.centroid_flags(1)

    assert reader._raw.centroid_reads == [(1, True)]


def test_centroid_masses_pair_with_the_flags_centroid_for_centroid():
    # Every stored centroid, in the order of its flags: OpenTFRaw's last
    # one has no FT label data and is flagged Reference all the same.
    opentfraw = _opentfraw()
    assert opentfraw.centroid_masses(2).tolist() == [*MZS, 105.0]
    assert opentfraw.centroid_masses(2).size == opentfraw.centroid_flags(2).size
    thermo = _thermo()
    assert thermo.centroid_masses(2).tolist() == MZS
    assert thermo.centroid_masses(2).size == thermo.centroid_flags(2).size


def test_a_scan_without_centroids_has_no_masses():
    opentfraw = _opentfraw()
    opentfraw._raw.centroid_labels = lambda number: {"mz": np.zeros(0)}
    assert opentfraw.centroid_masses(1).shape == (0,)
    thermo = _thermo()
    thermo._raw.GetCentroidStream = lambda number, include: None
    assert thermo.centroid_masses(1).shape == (0,)


def test_thermo_centroid_masses_count_the_reference_and_exception_centroids():
    reader = _thermo()

    reader.centroid_masses(1)

    assert reader._raw.centroid_reads == [(1, True)]


def test_per_scan_centroids_carry_their_scan_and_flags(reader):
    per_scan = reader.centroids_per_scan(mz_min=50.0, mz_max=750.0)

    assert [row["scan"] for row in per_scan] == SCAN_NUMBERS
    for row in per_scan:
        assert row["masses"].tolist() == MZS
        assert row["flags"].tolist() == FLAGS
        assert row["flags"].dtype.kind == "i"


def test_per_scan_flags_follow_the_m_z_range(reader):
    per_scan = reader.centroids_per_scan(mz_min=100.5, mz_max=102.5)

    assert per_scan[0]["masses"].tolist() == [101.0, 102.0]
    assert per_scan[0]["flags"].tolist() == [16, 8]


def test_per_scan_centroids_leave_out_the_excluded_flags(reader):
    per_scan = reader.centroids_per_scan(exclude=["reference", "exception"])

    for row in per_scan:
        # 103 is a fragmented lobe and an exception: an excluded flag is
        # enough to leave a centroid out.
        assert row["masses"].tolist() == [100.0, 104.0]
        assert row["flags"].tolist() == [0, 4]
        assert row["intensities"].size == row["resolutions"].size == 2
        assert row["signal_to_noise"].size == 2


def test_per_scan_centroids_refuse_a_name_that_is_no_flag(reader):
    with pytest.raises(ValueError, match="'lock'"):
        reader.centroids_per_scan(exclude=["lock"])


def test_metadata_centroids_carry_their_scan_and_flags(reader):
    scans = list(reader.iter_centroids_meta())

    assert [scan for scan, _, _ in scans] == SCAN_NUMBERS
    for scan, _, data in scans:
        assert data["scan"] == scan
        assert data["mzs"] == MZS
        assert data["flags"] == FLAGS
        assert all(type(flag) is int for flag in data["flags"])


def test_metadata_centroids_leave_out_the_excluded_flags(reader):
    for _, _, data in reader.iter_centroids_meta(exclude=["merged", "fragmented"]):
        assert data["mzs"] == [100.0, 101.0, 102.0]
        assert data["flags"] == [0, 16, 8]
        assert len(data["intensities"]) == len(data["resolutions"]) == 3
        assert len(data["noises"]) == 3


def test_centroids_meta_is_the_scans_gathered(reader):
    meta = reader.centroids_meta(exclude=["reference"])

    assert [data["scan"] for data in meta["data"]] == SCAN_NUMBERS
    assert all(16 not in data["flags"] for data in meta["data"])


# ---- through mascope_thermo.thermo ------------------------------------------


@pytest.fixture(params=[_opentfraw, _thermo], ids=["opentfraw", "thermo"])
def scripted_file(request, monkeypatch):
    """Open every file as the scripted acquisition, once per backend."""

    @contextmanager
    def scripted(_path):
        yield request.param()

    monkeypatch.setattr(m_thermo, "open_backend", scripted)
    return POS_ORBI_FILE_PATH


def test_get_centroids_per_scan_hands_on_the_exclusion(scripted_file):
    every = m_thermo.get_centroids_per_scan(scripted_file)
    kept = m_thermo.get_centroids_per_scan(scripted_file, exclude=["reference"])

    assert [row["flags"].tolist() for row in every] == [FLAGS] * 3
    assert [row["flags"].tolist() for row in kept] == [[0, 8, 10, 4]] * 3


def test_iter_centroids_meta_hands_on_the_exclusion(scripted_file):
    metadata = m_thermo.RawFileMetadataLegacy(scripted_file)

    every = metadata.centroids_meta
    kept = list(metadata.iter_centroids_meta(exclude=["exception"]))

    assert [data["flags"] for data in every["data"]] == [FLAGS] * 3
    assert [data["flags"] for _, _, data in kept] == [[0, 16, 4]] * 3


def test_iter_json_hands_on_the_exclusion(scripted_file, monkeypatch):
    # Only the centroids are of interest here; the rest is the scripted
    # reader's, which holds no trailers.
    monkeypatch.setattr(
        m_thermo.RawFileMetadataLegacy,
        "to_dict",
        lambda self, include_centroids=True, exclude=(): {"num_of_scans": 3},
    )
    metadata = m_thermo.RawFileMetadataLegacy(scripted_file)

    document = json.loads("".join(metadata.iter_json(exclude=["reference"])))

    data = document["centroids_meta"]["data"]
    assert [scan["scan"] for scan in data] == SCAN_NUMBERS
    assert [scan["flags"] for scan in data] == [[0, 8, 10, 4]] * 3


def test_iter_json_refuses_a_name_that_is_no_flag_before_its_first_piece(
    scripted_file,
):
    pieces = m_thermo.RawFileMetadataLegacy(scripted_file).iter_json(["lock"])

    with pytest.raises(ValueError, match="'lock'"):
        next(pieces)


def test_to_dict_leaves_out_the_excluded_flags(scripted_file, monkeypatch):
    class Scripted(m_thermo.RawFileMetadataLegacy):
        num_of_scans = 3
        scan_statistics = {}
        scan_acquisition_settings = {"header_labels": [], "settings": {}}
        instrument_details = {"Model": "X"}

    metadata = Scripted(scripted_file)

    every = metadata.to_dict()["centroids_meta"]["data"]
    kept = metadata.to_dict(exclude=["reference", "merged"])["centroids_meta"]["data"]

    assert [scan["flags"] for scan in every] == [FLAGS] * 3
    assert [scan["flags"] for scan in kept] == [[0, 8, 10]] * 3
    assert [scan["scan"] for scan in kept] == SCAN_NUMBERS


def test_get_centroid_flag_shares_reads_the_flags_of_every_centroid(scripted_file):
    """The share of each peak's centroids carrying a flag, the Reference and
    Exception centroids included: one centroid under each of the first five
    peaks, and 102 and 103 under the last, as wide as the two."""
    masses = np.array([*MZS, 102.5])
    resolutions = np.array([1e4] * len(MZS) + [102.5 / 1.2])

    exception = m_thermo.get_centroid_flag_shares(scripted_file, masses, resolutions)
    reference = m_thermo.get_centroid_flag_shares(
        scripted_file, masses, resolutions, flag="reference"
    )

    assert exception.tolist() == [0.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    assert reference.tolist() == [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
