"""A manual MS2 acquisition: fragment scans after the MS1 block, and unlinked.

An operator who fragments a precursor by hand, in the instrument's tuning
software with no method loaded, leaves a file of a particular shape: MS1 scans
first, then the fragment scans as a block of their own, rather than the two
interleaved as a data-dependent method records them. Two things follow, and
each once cost such a file its MS2 data.

**A window taken from its MS1 scans ends before its fragment scans begin.**
Every MS2 read selects within the window of the sample it is asked through,
so a window that came from the MS1 scans held none of the fragment scans, and
the file read as an MS1 acquisition with nothing fragmented in it. The window
has to span every scan type (``get_acquisition_window``, which this is the
reader-side half of).

**Nothing in the file links a fragment scan to a precursor scan.** A method
records each dependent scan's master scan, and every scan's event; a manual
acquisition records neither. What a fragment scan belongs to is read from its
own filter - the precursor m/z and the activation - and its survey scan is the
MS1 scan before it in scan order.

No committed file records a fragmentation scan, so the committed MS1 file is
read here as such an acquisition: its first scan stays the survey scan it is,
and every later one is relabelled as a fragment scan of one precursor, at the
reader object the OpenTFRaw backend reads everything from. The Thermo backend
selects through the vendor's own filter objects and has no such seam, so
these run on OpenTFRaw alone.
"""

import pytest
from thermo_test_support import POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import OpenTFRawBackend, open_backend


PRECURSOR_MZ = 137.096
ACTIVATION = "hcd20.00"
#: What a targeted MS2 scan's filter reads like, in place of ``Full ms``.
_MS2 = f" Full ms2 {PRECURSOR_MZ:.4f}@{ACTIVATION} ["


def _as_fragment_scan(filter_string: str) -> str:
    return filter_string.replace(" Full ms [", _MS2)


class _ManualMs2Acquisition:
    """An ``opentfraw.RawFile`` whose scans after the first are fragment scans.

    Everything not overridden is the real file's: scan times, TICs, trailers
    and centroids. The scan table already records what a manual acquisition
    does about linkage - no master scan, and scan event 0 - which
    ``TestWhatLinksAFragmentScanToItsPrecursor`` checks rather than assumes.
    """

    def __init__(self, raw):
        self._raw = raw

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def scan_table(self):
        table = {name: list(values) for name, values in self._raw.scan_table().items()}
        for row in range(1, len(table["scan_number"])):
            table["ms_level"][row] = 2
            table["filter_string"][row] = _as_fragment_scan(table["filter_string"][row])
        return table

    def scan_filter(self, scan_number):
        filter_string = self._raw.scan_filter(scan_number)
        if int(scan_number) == 1 or not filter_string:
            return filter_string
        return _as_fragment_scan(filter_string)


@pytest.fixture(autouse=True)
def manual_ms2_acquisition(monkeypatch):
    """Read the committed MS1 file as a manual MS2 acquisition."""
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    enter = OpenTFRawBackend.__enter__

    def entered(self):
        backend = enter(self)
        backend._raw = _ManualMs2Acquisition(backend._raw)
        return backend

    monkeypatch.setattr(OpenTFRawBackend, "__enter__", entered)


def _window(scan_type):
    times = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH, scan_type=scan_type)
    return float(times.min()), float(times.max())


class TestTheFixtureIsSuchAnAcquisition:
    """Guards the stand-in, which every case below takes on trust."""

    def test_its_ms1_scans_come_before_its_fragment_scans(self):
        ms1 = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH, scan_type="Ms")
        ms2 = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH, scan_type="Ms2")

        assert ms1.size >= 1 and ms2.size >= 1
        assert ms1.max() < ms2.min()


class TestTheWindowOfItsSample:
    def test_a_window_from_its_ms1_scans_holds_no_fragment_scan(self):
        """The defect, kept on record: this is what such a window reads.

        A sample whose window was taken from the TIC - MS1 scans only - asked
        every MS2 route to look where no fragment scan was recorded.
        """
        t0, t1 = _window("Ms")

        summary = m_thermo.get_ms2_summary_metadata(
            POS_ORBI_FILE_PATH, t_min=t0, t_max=t1
        )

        assert summary["ms2_scan_count"] == 0
        assert summary["groups"] == []

    def test_a_window_from_every_scan_holds_them_all(self):
        whole_file = m_thermo.get_ms2_summary_metadata(POS_ORBI_FILE_PATH)
        t0, t1 = _window(None)

        summary = m_thermo.get_ms2_summary_metadata(
            POS_ORBI_FILE_PATH, t_min=t0, t_max=t1
        )

        assert summary["ms2_scan_count"] == whole_file["ms2_scan_count"] >= 1
        assert summary["ms1_scan_count"] == whole_file["ms1_scan_count"] >= 1

    def test_its_fragment_spectrum_is_read_through_that_window(self):
        t0, t1 = _window(None)

        by_parent = m_thermo.get_ms2_centroids_by_parent(
            POS_ORBI_FILE_PATH, t_min=t0, t_max=t1
        )

        assert list(by_parent) == [(PRECURSOR_MZ, ACTIVATION)]
        masses, intensities, _, _ = by_parent[(PRECURSOR_MZ, ACTIVATION)]
        assert masses.size == intensities.size > 0


class TestWhatLinksAFragmentScanToItsPrecursor:
    def test_the_file_records_no_master_scan_and_no_scan_event(self):
        """The premise: there is nothing else to link them by."""
        with open_backend(POS_ORBI_FILE_PATH) as backend:
            fragment_scans = [
                scan for scan in backend._all_scans() if int(scan["ms_level"]) == 2
            ]

        assert fragment_scans
        assert {scan["master_scan_number"] for scan in fragment_scans} == {None}
        assert {int(scan["scan_event"]) for scan in fragment_scans} == {0}

    def test_the_precursor_and_the_activation_come_from_its_own_filter(self):
        with open_backend(POS_ORBI_FILE_PATH) as backend:
            events = backend.ms2_events_by_scan()
            fragment_scans = backend.scan_indices(ms_type="Ms2")

        assert events == dict.fromkeys(fragment_scans, (PRECURSOR_MZ, ACTIVATION))

    def test_the_summary_groups_them_by_that_alone(self):
        summary = m_thermo.get_ms2_summary_metadata(POS_ORBI_FILE_PATH)

        assert summary["parent_peaks"] == [PRECURSOR_MZ]
        assert [
            (group["parent_peak_mz"], group["activation"], group["scan_count"])
            for group in summary["groups"]
        ] == [(PRECURSOR_MZ, ACTIVATION, summary["ms2_scan_count"])]

    def test_its_survey_scan_is_the_ms1_scan_before_it_in_scan_order(self):
        """Scan order is the only thing that says which MS1 scan it follows."""
        with open_backend(POS_ORBI_FILE_PATH) as backend:
            ms1_scans = backend.scan_indices(ms_type="Ms")
            fragment_scans = backend.scan_indices(ms_type="Ms2")

        assert max(ms1_scans) < min(fragment_scans)
