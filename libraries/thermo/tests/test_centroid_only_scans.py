"""Scans that stored their centroids alone are summed as their centroids.

Such a scan has no profile: OpenTFRaw reads it as empty and the Thermo
library as the centroids standing in for one, so the summed profile of a
file of them was empty under one reader and sticks without a baseline under
the other. Its summed signal is now its centroids, averaged as the peaks
are, drawn as sticks, and marked as drawn from centroids; a file of profile
scans is summed as before, unmarked.

The centroid scans are the committed MS1 file read as such
(``server/backend/tests/centroid_acquisition.py``).
"""

import numpy as np
import pytest
from thermo_test_support import POS_ORBI_FILE_PATH, centroid_acquisition

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import open_backend


acquisition = centroid_acquisition()


class TestTheCentroidSpectrum:
    def test_each_centroid_is_a_stick_at_zero_either_side(self):
        mz, values = m_thermo.centroid_spectrum(
            np.array([300.0, 100.0, 200.0]), np.array([3.0, 1.0, 2.0])
        )

        np.testing.assert_array_equal(mz[1::3], [100.0, 200.0, 300.0])
        np.testing.assert_array_equal(values, [0, 1, 0, 0, 2, 0, 0, 3, 0])
        # As narrow as the neighbours leave room for: a tenth of a ppm
        np.testing.assert_allclose(mz[2::3] - mz[1::3], mz[1::3] * 1e-7)
        np.testing.assert_allclose(mz[1::3] - mz[0::3], mz[1::3] * 1e-7)

    def test_close_neighbours_narrow_their_sticks_to_a_third_of_the_gap(self):
        gap = 100.0 * 1e-7  # a tenth of a ppm: closer than two sticks reach
        mz, _ = m_thermo.centroid_spectrum(
            np.array([100.0, 100.0 + gap]), np.array([1.0, 1.0])
        )

        assert np.all(np.diff(mz) > 0)
        assert mz[2] - mz[1] == pytest.approx(gap / 3)
        assert mz[4] - mz[3] == pytest.approx(gap / 3)
        # The outer sides keep their full reach
        assert mz[1] - mz[0] == pytest.approx(100.0 * 1e-7)

    def test_centroids_on_one_mz_are_one_stick_at_their_sum(self):
        mz, values = m_thermo.centroid_spectrum(
            np.array([150.0, 150.0, 120.0]), np.array([1.0, 4.0, 2.0])
        )

        np.testing.assert_array_equal(mz[1::3], [120.0, 150.0])
        np.testing.assert_array_equal(values[1::3], [2.0, 5.0])
        assert np.all(np.diff(mz) > 0)

    def test_no_centroids_is_no_spectrum(self):
        mz, values = m_thermo.centroid_spectrum(np.array([]), np.array([]))

        assert mz.size == values.size == 0


class _Filters:
    """A reader that knows its scans' filters and nothing else."""

    def __init__(self, *filters):
        self._filters = filters

    def scan_filters(self):
        return [
            {"scan": number, "filter": text}
            for number, text in enumerate(self._filters, start=1)
        ]


CENTROID = "FTMS + c NSI Full ms [40.0000-500.0000]"
PROFILE = "FTMS + p NSI Full ms [40.0000-500.0000]"


class TestWhetherScansHoldCentroidsOnly:
    def test_scans_that_all_stored_centroids_do(self):
        assert m_thermo.holds_centroids_only(_Filters(CENTROID, CENTROID), [1, 2])

    def test_one_profile_scan_among_them_and_they_do_not(self):
        assert not m_thermo.holds_centroids_only(_Filters(CENTROID, PROFILE), [1, 2])

    def test_only_the_scans_asked_about_count(self):
        assert m_thermo.holds_centroids_only(_Filters(PROFILE, CENTROID), [2])

    def test_a_filter_that_states_no_data_type_is_no_centroid_scan(self):
        assert not m_thermo.holds_centroids_only(
            _Filters("FTMS + NSI Full ms [40.0000-500.0000]", ""), [1, 2]
        )

    def test_no_scans_hold_nothing(self):
        assert not m_thermo.holds_centroids_only(_Filters(CENTROID), [])


class TestTheSumSignalOfCentroidScans:
    def test_it_is_their_averaged_centroids_drawn_as_sticks(self):
        with acquisition.presented() as path:
            signal, combined = m_thermo.compute_sum_signal(str(path))
            with open_backend(str(path)) as backend:
                scans = backend.scan_indices()
                masses, intensities, *_ = backend.average_centroids(
                    scans, ppm=1, average=False
                )

        mz, values = m_thermo.centroid_spectrum(masses, intensities)
        np.testing.assert_array_equal(signal.mz.values, mz)
        np.testing.assert_array_equal(signal.values, values)
        assert values.any()
        assert combined == len(scans)
        assert signal.attrs == {m_thermo.SPECTRUM_ATTR: m_thermo.CENTROID_SPECTRUM}

    def test_a_selection_is_summed_as_its_own_centroids(self):
        with acquisition.presented() as path:
            first_scan, _ = m_thermo.compute_sum_signal(str(path), t_max=10.0)
            whole, _ = m_thermo.compute_sum_signal(str(path))

        assert first_scan.attrs == whole.attrs
        assert first_scan.values.sum() < whole.values.sum()

    def test_they_are_recognised_as_centroid_scans(self):
        with acquisition.presented() as path, open_backend(str(path)) as backend:
            assert m_thermo.holds_centroids_only(backend, backend.scan_indices())


class TestTheSumSignalOfProfileScans:
    def test_it_is_their_profile_summed_as_before_and_unmarked(self, backend):
        signal, combined = m_thermo.compute_sum_signal(POS_ORBI_FILE_PATH)
        with open_backend(POS_ORBI_FILE_PATH) as reader:
            scans = reader.scan_indices()
            mz, values, expected = reader.average_profile(scans, ppm=1, average=False)
            assert not m_thermo.holds_centroids_only(reader, scans)

        np.testing.assert_array_equal(signal.mz.values, mz)
        np.testing.assert_array_equal(signal.values, values)
        assert combined == expected
        assert signal.attrs == {}
