"""The TIC is read per scan type, and an acquisition with no MS1 scans has one.

``get_tic_per_scan`` reads MS1 scans unless it is told otherwise, which is the
TIC a sample reports. An acquisition of fragmentation scans alone records no
MS1 scan, so that read finds nothing - and the reader raises rather than hand
back an empty selection. Such a sample's TIC is read over the scans it does
have, which is what the scan type is passed for (``create_sample_items``, and
the empty peak store of a file with no MS1 scans).

No committed file records a fragmentation scan. The second half therefore
relabels the scans of an ordinary file at the OpenTFRaw backend's scan-list
seam, where every selection reads the MS order from: everything above that
seam then behaves as it does for a genuine such file, which is how the
condition is reproduced for issue #2068. The Thermo backend selects through
the vendor's own filter objects and has no such seam, so those tests run on
OpenTFRaw alone; what they pin - which scans a scan type selects - is the
contract both backends are held to by ``test_backend_parity``.
"""

import numpy as np
import pytest
from thermo_test_support import POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import OpenTFRawBackend
from mascope_thermo.thermo import NoScansFoundError


@pytest.mark.usefixtures("backend")
class TestScanTypeOnAnMs1File:
    """The committed sample files hold MS1 scans and nothing else."""

    def test_ms1_is_the_default(self):
        default_times, default_tic = m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH)
        ms1_times, ms1_tic = m_thermo.get_tic_per_scan(
            POS_ORBI_FILE_PATH, scan_type="Ms"
        )

        np.testing.assert_array_equal(default_times, ms1_times)
        np.testing.assert_array_equal(default_tic, ms1_tic)

    def test_every_scan_type_is_the_same_scans_here(self):
        ms1_times, ms1_tic = m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH)
        all_times, all_tic = m_thermo.get_tic_per_scan(
            POS_ORBI_FILE_PATH, scan_type=None
        )

        np.testing.assert_array_equal(all_times, ms1_times)
        np.testing.assert_array_equal(all_tic, ms1_tic)

    def test_a_scan_type_the_file_lacks_raises(self):
        with pytest.raises(NoScansFoundError):
            m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH, scan_type="Ms2")

    def test_the_scan_type_goes_with_the_other_filters(self):
        times, tic = m_thermo.get_tic_per_scan(
            POS_ORBI_FILE_PATH, timestamps=[0], polarity="+", scan_type=None
        )

        assert times.size == tic.size == 1


@pytest.fixture
def ms1_tic(monkeypatch):
    """The file's TIC as recorded, read before its scans are relabelled."""
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    return m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH)


@pytest.fixture
def fragmentation_scans_only(monkeypatch, ms1_tic):
    """Make the sample file read as an acquisition of MS2 scans alone."""
    all_scans = OpenTFRawBackend._all_scans

    def relabelled(self):
        return [{**scan, "ms_level": 2} for scan in all_scans(self)]

    monkeypatch.setattr(OpenTFRawBackend, "_all_scans", relabelled)


@pytest.mark.usefixtures("fragmentation_scans_only")
class TestScanTypeOnAFileWithNoMs1Scans:
    def test_the_ms1_read_finds_nothing(self):
        with pytest.raises(NoScansFoundError):
            m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH)

    def test_what_it_raises_is_a_value_error(self):
        """``create_sample_items`` tells this apart by the base class.

        It names no reader error, so that the controller does not depend on
        which reader a file takes: a ``ValueError`` from the MS1 read is what
        sends it to the scans the file does have.
        """
        with pytest.raises(ValueError):
            m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH, polarity="+")

    def test_every_scan_type_reads_the_scans_it_has(self, ms1_tic):
        recorded_times, recorded_tic = ms1_tic

        times, tic = m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH, scan_type=None)

        np.testing.assert_array_equal(times, recorded_times)
        np.testing.assert_array_equal(tic, recorded_tic)

    def test_the_ms2_read_is_those_scans(self, ms1_tic):
        recorded_times, recorded_tic = ms1_tic

        times, tic = m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH, scan_type="Ms2")

        np.testing.assert_array_equal(times, recorded_times)
        np.testing.assert_array_equal(tic, recorded_tic)

    def test_a_polarity_it_does_not_hold_still_raises(self):
        """The wider read must not answer for a polarity the file lacks.

        That second ``ValueError`` is how a sample item asked for in the wrong
        polarity is still refused rather than created with someone else's TIC.
        """
        with pytest.raises(NoScansFoundError):
            m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH, polarity="-", scan_type=None)

    def test_the_time_axis_of_every_scan_is_the_tic_axis(self, ms1_tic):
        """The empty peak store of such a file is written on this axis."""
        recorded_times, _ = ms1_tic

        times = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH, scan_type=None)

        np.testing.assert_array_equal(times, recorded_times)
        with pytest.raises(NoScansFoundError):
            m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH)
