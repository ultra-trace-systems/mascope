import numpy as np
import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH, read_or_xfail

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import open_backend


# Run every test in this module under each reader backend (thermo + opentfraw).
# The Thermo backend is skipped unless its DLLs are available (see the `backend`
# fixture in conftest.py); OpenTFRaw always runs.
pytestmark = pytest.mark.usefixtures("backend")


class TestGetPolarityOptions:
    """Test that the correct polarity is extracted from the raw files.

    Expected behavior:
    - For the positive mode file, the polarity should be "+".
    - For the negative mode file, the polarity should be "-".
    """

    def test_positive_mode_polarity(self):
        polarity = m_thermo.get_polarity_options(POS_ORBI_FILE_PATH)
        assert polarity == "+", f"Expected polarity to be '+', but got '{polarity}'"

    def test_negative_mode_polarity(self):
        polarity = m_thermo.get_polarity_options(NEG_ORBI_FILE_PATH)
        assert polarity == "-", f"Expected polarity to be '-', but got '{polarity}'"


class TestGetSignal:
    """Test that the signal is correctly extracted from the raw file.

    Expected behavior:
    - The m/z and time arrays should have more than 0 elements.
    - The intensity values should sum to more than 0.
    - A ValueError should be raised if an invalid polarity is provided.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, backend):
        # Depends on `backend` so the env var is set before get_signal is called
        # (a plain setup_method would run before the backend fixture). Wrapped so
        # the not-yet-implemented opentfraw backend xfails rather than erroring in
        # setup.
        self.sig = read_or_xfail(m_thermo.get_signal, POS_ORBI_FILE_PATH, polarity="+")

    def test_correct_signal_extraction(self):
        assert self.sig.mz.size > 0, "Expected m/z array to have more than 0 elements"
        assert self.sig.time.size > 0, (
            "Expected time array to have more than 0 elements"
        )
        # Check the signal has non-zero values
        assert self.sig.signal.sum() > 0, (
            "Expected intensity values to sum to more than 0"
        )

    def test_invalid_polarity(self):
        with pytest.raises(ValueError):
            m_thermo.get_signal(POS_ORBI_FILE_PATH, polarity="-")


class TestComputeSumSignal:
    """Test that the sum signal is correctly computed from the raw file.

    Expected behavior:
    - The m/z and time arrays should have more than 0 elements.
    - The intensity values should sum to more than 0.
    """

    def test_correct_sum_signal_computation(self):
        sum_sig, n_scans = m_thermo.compute_sum_signal(POS_ORBI_FILE_PATH)
        sum_sig_pos, n_scans_pos = m_thermo.compute_sum_signal(
            POS_ORBI_FILE_PATH, polarity="+"
        )

        assert sum_sig.mz.size > 0, "Expected m/z array to have more than 0 elements"
        assert n_scans == n_scans_pos, (
            "Expected number of combined scans to be the same for None and '+' polarity"
        )
        np.testing.assert_array_equal(
            sum_sig.values,
            sum_sig_pos.values,
            "Expected sum signal values to be the same for None and '+' polarity",
        )

    def test_incorrect_polarity(self):
        with pytest.raises(ValueError):
            m_thermo.compute_sum_signal(POS_ORBI_FILE_PATH, polarity="-")


class TestGetTicPerScan:
    """Test that the TIC per scan is correctly extracted from the raw file.

    Expected behavior:
    - The scan timestamps and TIC arrays should have more than 0 elements
    - One timestamp extraction should return exactly 1 TIC value
    """

    def test_correct_tic_extraction(self):
        scan_timestamps, tic_per_scan = m_thermo.get_tic_per_scan(POS_ORBI_FILE_PATH)
        assert tic_per_scan.size > 0, "Expected TIC array to have more than 0 elements"
        assert scan_timestamps.size > 0, (
            "Expected scan timestamps array to have more than 0 elements"
        )

    def test_one_timestamp_extraction(self):
        scan_timestamps, tic_per_scan = m_thermo.get_tic_per_scan(
            POS_ORBI_FILE_PATH, timestamps=[0]
        )
        assert tic_per_scan.size == 1, "Expected TIC array to have exactly 1 element"
        assert scan_timestamps.size == 1, (
            "Expected scan timestamps array to have exactly 1 element"
        )


class TestGetPeakTimeseries:
    """Test that the peak timeseries is correctly extracted from the raw file.

    Expected behavior:
    - The output DataArray should have dimensions ('mz', 'time').
    - The m/z coordinates should be within 5 ppm of the input m/z values.
    - The time coordinates should be within the specified time range if provided.
    - The intensity values should be finite and non-negative.
    - A ValueError should be raised if an invalid polarity is provided.
    - An InvalidRangeError should be raised if t_min is greater than t_max.
    """

    def setup_method(self):
        with open_backend(POS_ORBI_FILE_PATH) as be:
            low, high = be.mass_range()
            self.test_mzs = np.array(
                [
                    low + (high - low) * 0.25,
                    low + (high - low) * 0.5,
                    low + (high - low) * 0.75,
                ]
            )

    def test_correct_peak_timeseries_extraction(self):

        peak_ts = m_thermo.get_peak_timeseries(POS_ORBI_FILE_PATH, self.test_mzs)

        assert peak_ts.dims == (
            "mz",
            "time",
        ), "Expected dimensions to be ('mz', 'time')"
        assert peak_ts.mz.size == len(self.test_mzs), (
            "Expected one trace per requested m/z"
        )
        assert peak_ts.time.size > 0, "Expected non-empty time axis"

        diffs_ppm = np.abs(peak_ts.mz.values - self.test_mzs) / self.test_mzs * 1e6
        assert np.all(
            diffs_ppm < 5,
        ), "Expected output m/z coords to be within 5 ppm of input m/zs"

        assert np.isfinite(peak_ts.values).all(), "Expected finite timeseries values"
        assert np.all(peak_ts.values >= 0), "Expected non-negative intensities"

    def test_time_range_filtering(self):
        scan_time = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH)
        t_min = float(scan_time.min())
        t_max = float(scan_time.max())

        peak_ts = m_thermo.get_peak_timeseries(
            POS_ORBI_FILE_PATH, self.test_mzs, t_min=t_min, t_max=t_max
        )

        assert peak_ts.time.size > 0, "Expected non-empty time axis after filtering"

    def test_incorrect_polarity(self):
        with pytest.raises(ValueError):
            m_thermo.get_peak_timeseries(
                POS_ORBI_FILE_PATH, self.test_mzs, polarity="-"
            )

    def test_invalid_time_range(self):
        with pytest.raises(m_thermo.InvalidRangeError):
            m_thermo.get_peak_timeseries(
                POS_ORBI_FILE_PATH, self.test_mzs, t_min=100.0, t_max=10.0, polarity="+"
            )


class TestGetCentroids:
    """Test centroid extraction from Thermo raw files."""

    def test_correct_centroid_extraction(self):
        masses, intensities, resolutions, signal_to_noise = m_thermo.get_centroids(
            POS_ORBI_FILE_PATH,
            polarity="+",
        )

        assert masses.size > 0, "Expected centroid masses to be non-empty"
        assert intensities.size > 0, "Expected centroid intensities to be non-empty"
        assert resolutions.size > 0, "Expected centroid resolutions to be non-empty"
        assert signal_to_noise.size > 0, (
            "Expected centroid signal-to-noise values to be non-empty"
        )

        assert (
            masses.size == intensities.size == resolutions.size == signal_to_noise.size
        )
        assert np.isfinite(masses).all(), "Expected finite centroid masses"
        assert np.isfinite(intensities).all(), "Expected finite centroid intensities"
        assert np.isfinite(resolutions).all(), "Expected finite centroid resolutions"
        assert np.isfinite(signal_to_noise).all(), (
            "Expected finite signal-to-noise values"
        )
        assert np.all(intensities >= 0), "Expected non-negative centroid intensities"

    def test_average_flag_scales_intensity(self):
        masses_avg, intensities_avg, resolutions_avg, signal_to_noise_avg = (
            m_thermo.get_centroids(
                POS_ORBI_FILE_PATH,
                polarity="+",
                average=True,
            )
        )
        masses_sum, intensities_sum, resolutions_sum, signal_to_noise_sum = (
            m_thermo.get_centroids(
                POS_ORBI_FILE_PATH,
                polarity="+",
                average=False,
            )
        )

        np.testing.assert_allclose(
            masses_avg,
            masses_sum,
            err_msg="Expected centroid masses to be identical for average=True/False",
        )
        np.testing.assert_allclose(
            resolutions_avg,
            resolutions_sum,
            err_msg="Expected centroid resolutions to be identical for average=True/False",
        )
        np.testing.assert_allclose(
            signal_to_noise_avg,
            signal_to_noise_sum,
            err_msg="Expected signal-to-noise to be identical for average=True/False",
        )

        _, n_scans = m_thermo.compute_sum_signal(POS_ORBI_FILE_PATH, polarity="+")
        np.testing.assert_allclose(
            intensities_sum,
            intensities_avg * n_scans,
            rtol=1e-6,
            err_msg="Expected average=False intensities to be scaled by number of scans",
        )

    def test_time_range_filtering(self):
        scan_time = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH)
        t_min = float(scan_time.min())
        t_max = float(scan_time.max())

        masses, intensities, resolutions, signal_to_noise = m_thermo.get_centroids(
            POS_ORBI_FILE_PATH,
            t_min=t_min,
            t_max=t_max,
            polarity="+",
        )

        assert masses.size > 0, "Expected centroid masses after time filtering"
        assert intensities.size > 0, (
            "Expected centroid intensities after time filtering"
        )
        assert (
            masses.size == intensities.size == resolutions.size == signal_to_noise.size
        )

    def test_incorrect_polarity(self):
        with pytest.raises(ValueError):
            m_thermo.get_centroids(POS_ORBI_FILE_PATH, polarity="-")

    def test_invalid_time_range(self):
        with pytest.raises(m_thermo.InvalidRangeError):
            m_thermo.get_centroids(
                POS_ORBI_FILE_PATH,
                t_min=100.0,
                t_max=10.0,
                polarity="+",
            )

    def test_invalid_ppm(self):
        with pytest.raises(ValueError):
            m_thermo.get_centroids(
                POS_ORBI_FILE_PATH,
                polarity="+",
                ppm=0,
            )


class TestGetCentroidsPerScan:
    """Test per-scan centroid extraction from Thermo raw files."""

    def test_correct_centroids_per_scan_extraction(self):
        centroids = m_thermo.get_centroids_per_scan(
            POS_ORBI_FILE_PATH,
            polarity="+",
        )

        assert len(centroids) > 0, "Expected at least one scan worth of centroid data"

        first = centroids[0]
        expected_keys = {
            "masses",
            "intensities",
            "resolutions",
            "signal_to_noise",
            "timestamp",
        }
        assert set(first.keys()) == expected_keys, "Unexpected centroid dictionary keys"

        for scan_data in centroids:
            masses = scan_data["masses"]
            intensities = scan_data["intensities"]
            resolutions = scan_data["resolutions"]
            signal_to_noise = scan_data["signal_to_noise"]
            timestamp = scan_data["timestamp"]

            assert isinstance(masses, np.ndarray)
            assert isinstance(intensities, np.ndarray)
            assert isinstance(resolutions, np.ndarray)
            assert isinstance(signal_to_noise, np.ndarray)
            assert np.isscalar(timestamp)

            assert (
                masses.size
                == intensities.size
                == resolutions.size
                == signal_to_noise.size
            )
            assert np.isfinite(masses).all(), "Expected finite centroid masses"
            assert np.isfinite(intensities).all(), (
                "Expected finite centroid intensities"
            )
            assert np.isfinite(resolutions).all(), (
                "Expected finite centroid resolutions"
            )
            assert np.isfinite(signal_to_noise).all(), (
                "Expected finite signal-to-noise values"
            )
            assert np.all(intensities >= 0), (
                "Expected non-negative centroid intensities"
            )

    def test_time_range_filtering_and_timestamp_alignment(self):
        scan_times = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH, polarity="+")
        t_min = float(scan_times.min())
        t_max = float(scan_times.max())

        centroids = m_thermo.get_centroids_per_scan(
            POS_ORBI_FILE_PATH,
            t_min=t_min,
            t_max=t_max,
            polarity="+",
        )

        returned_timestamps = np.array(
            [scan_data["timestamp"] for scan_data in centroids]
        )

        assert returned_timestamps.size > 0, "Expected timestamps after filtering"
        assert returned_timestamps.min() >= t_min - 1e-6
        assert returned_timestamps.max() <= t_max + 1e-6

        expected_timestamps = m_thermo.get_scan_timestamps(
            POS_ORBI_FILE_PATH,
            t_min=t_min,
            t_max=t_max,
            polarity="+",
        )
        np.testing.assert_allclose(
            returned_timestamps,
            expected_timestamps,
            err_msg="Expected centroid timestamps to align with filtered scan timestamps",
        )

    def test_mz_range_filtering(self):
        with open_backend(POS_ORBI_FILE_PATH) as be:
            low, high = be.mass_range()

        mz_min = low + (high - low) * 0.25
        mz_max = low + (high - low) * 0.50

        centroids = m_thermo.get_centroids_per_scan(
            POS_ORBI_FILE_PATH,
            mz_min=mz_min,
            mz_max=mz_max,
            polarity="+",
        )

        assert len(centroids) > 0, "Expected at least one filtered scan"

        for scan_data in centroids:
            masses = scan_data["masses"]
            if masses.size > 0:
                assert np.all(masses >= mz_min), "Expected centroid masses >= mz_min"
                assert np.all(masses <= mz_max), "Expected centroid masses <= mz_max"

    def test_incorrect_polarity(self):
        with pytest.raises(ValueError):
            m_thermo.get_centroids_per_scan(POS_ORBI_FILE_PATH, polarity="-")

    def test_invalid_time_range(self):
        with pytest.raises(m_thermo.InvalidRangeError):
            m_thermo.get_centroids_per_scan(
                POS_ORBI_FILE_PATH,
                t_min=100.0,
                t_max=10.0,
                polarity="+",
            )

    def test_invalid_mz_range(self):
        with pytest.raises(m_thermo.InvalidRangeError):
            m_thermo.get_centroids_per_scan(
                POS_ORBI_FILE_PATH,
                mz_min=200.0,
                mz_max=100.0,
                polarity="+",
            )
