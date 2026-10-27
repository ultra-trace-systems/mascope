"""The instrument functions of a raw Orbitrap file's centroid scans.

Scans that stored their centroids alone have no profile to fit a peak shape
on, and their summed signal says it was drawn from centroids
(``mascope_thermo.thermo.SPECTRUM_ATTR``). Their resolution function is read
off what each scan's centroids report, the strong ones where there are
enough, and their peak shape is a Gaussian
(``mascope_signal.instrument_func.fit.centroid_instrument_functions``).

The reader is scripted: what is pinned is which centroids count, and that a
signal drawn from centroids, and only such a signal, is read this way, over
the scans it was summed from.
"""

from functools import partial

import numpy as np
import pytest
import xarray as xr

import mascope_signal.instrument_func.fit as m_fit
import mascope_signal.instrument_func.streams as m_stream_functions
import mascope_thermo.thermo as m_thermo


FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"
DATAFILE = "/filestore/data.raw"


def _scan(masses, a, signal_to_noise):
    """One scan's centroids, each reporting the resolution ``a / sqrt(m/z)``."""
    masses = np.asarray(masses, dtype=float)
    return {
        "masses": masses,
        "resolutions": np.broadcast_to(a, masses.shape) / np.sqrt(masses),
        "signal_to_noise": np.broadcast_to(signal_to_noise, masses.shape).astype(float),
    }


@pytest.fixture
def reader(monkeypatch):
    """Script the scans' centroids; record what each read asked for."""
    asked = []

    def _script(*scans):
        def get_centroids_per_scan(datafile_path, **kwargs):
            asked.append({"datafile_path": datafile_path, **kwargs})
            return [dict(scan) for scan in scans]

        monkeypatch.setattr(
            m_fit.m_thermo, "get_centroids_per_scan", get_centroids_per_scan
        )
        return asked

    monkeypatch.setattr(m_fit, "filename_to_datafile_path", lambda _name: DATAFILE)
    return _script


class TestTheResolutionFunction:
    def test_it_is_read_off_the_strong_centroids_where_there_are_enough(self, reader):
        reader(
            _scan([100.0, 200.0], 2.0e6, 50.0),
            _scan([150.0, 300.0], 2.0e6, m_fit.CENTROID_MIN_SNR),
            # Weak peaks report themselves broader
            _scan([110.0, 120.0, 130.0, 140.0, 160.0], 1.5e6, 4.0),
        )

        _, resolution_function, stats = m_fit.centroid_instrument_functions(FILENAME)

        assert resolution_function.func is m_fit.r_orbi
        assert resolution_function.keywords["a"] == pytest.approx(2.0e6)
        assert stats["resolution_function"]["coefficients"] == [pytest.approx(2.0e6)]
        assert stats["resolution_function"]["n_points"] == 4
        assert stats["peakshape"] == {"num_of_peaks": 4, "source": "centroids"}

    def test_it_is_the_median_of_what_they_report(self, reader):
        reader(
            _scan([100.0], 1.0e6, 50.0),
            _scan([100.0], 2.0e6, 50.0),
            _scan([100.0], 9.0e6, 50.0),
        )

        _, resolution_function, _ = m_fit.centroid_instrument_functions(FILENAME)

        assert resolution_function.keywords["a"] == pytest.approx(2.0e6)

    def test_too_few_strong_centroids_and_every_one_counts(self, reader):
        reader(
            _scan([100.0, 200.0], 2.0e6, 50.0),
            _scan([110.0, 120.0, 130.0], 1.0e6, 2.0),
        )

        _, resolution_function, stats = m_fit.centroid_instrument_functions(FILENAME)

        assert resolution_function.keywords["a"] == pytest.approx(1.0e6)
        assert stats["resolution_function"]["n_points"] == 5

    def test_exactly_three_strong_centroids_are_enough(self, reader):
        reader(
            _scan([100.0, 200.0, 300.0], 2.0e6, m_fit.CENTROID_MIN_SNR),
            _scan([110.0, 120.0, 130.0, 140.0], 1.0e6, 2.0),
        )

        _, resolution_function, stats = m_fit.centroid_instrument_functions(FILENAME)

        assert m_fit.MIN_NUM_PEAKS == 3
        assert resolution_function.keywords["a"] == pytest.approx(2.0e6)
        assert stats["resolution_function"]["n_points"] == 3

    def test_a_centroid_with_no_resolution_does_not_count(self, reader):
        scan = _scan([100.0, 200.0, 300.0, 400.0, 500.0], 2.0e6, 50.0)
        scan["resolutions"] = np.array([np.nan, 0.0, -1.0, 2.0e6 / 20, 2.0e6 / 20])
        reader(scan, _scan([100.0], 2.0e6, 50.0))

        _, resolution_function, stats = m_fit.centroid_instrument_functions(FILENAME)

        assert stats["resolution_function"]["n_points"] == 3
        assert resolution_function.keywords["a"] == pytest.approx(
            np.median([2.0e6 / 20 * 20, 2.0e6 / 20 * np.sqrt(500), 2.0e6])
        )

    def test_a_centroid_with_no_mz_does_not_count(self, reader):
        # Each reports a resolution; only those at a positive m/z can be scaled
        reader(
            {
                "masses": np.array([100.0, 400.0, 900.0, 0.0, -1.0, np.nan, np.inf]),
                "resolutions": np.array([2.0e5, 1.0e5, 2.0e6 / 30, *[2.0e5] * 4]),
                "signal_to_noise": np.full(7, 50.0),
            }
        )

        _, resolution_function, stats = m_fit.centroid_instrument_functions(FILENAME)

        assert stats["resolution_function"]["n_points"] == 3
        assert resolution_function.keywords["a"] == pytest.approx(2.0e6)

    def test_fewer_than_three_centroids_with_a_resolution_are_too_few(self, reader):
        reader(_scan([100.0, 200.0], 2.0e6, 50.0), _scan([], 2.0e6, 50.0))

        with pytest.raises(m_fit.InsufficientPeaksError):
            m_fit.centroid_instrument_functions(FILENAME)

    def test_no_scans_are_too_few(self, reader):
        reader()

        with pytest.raises(m_fit.InsufficientPeaksError):
            m_fit.centroid_instrument_functions(FILENAME)

    def test_it_reads_each_ms1_scan_of_the_stream_without_the_flagged_ones(
        self, reader
    ):
        asked = reader(_scan([100.0, 200.0, 300.0], 2.0e6, 50.0))

        m_fit.centroid_instrument_functions(FILENAME, stream="+ 40-500 R=120000")

        assert asked == [
            {
                "datafile_path": DATAFILE,
                "scan_type": "Ms",
                "stream": "+ 40-500 R=120000",
                "exclude": ("merged", "fragmented", "exception"),
            }
        ]


def test_the_peak_shape_is_a_gaussian_normalised_as_a_fitted_one(reader):
    reader(_scan([100.0, 200.0, 300.0], 2.0e6, 50.0))
    x = np.linspace(-10, 10, 101)

    peak_shape, _, _ = m_fit.centroid_instrument_functions(FILENAME)
    fitted, _ = m_fit._calculate_peakshape(x, [np.exp(-(x**2) / 2)])

    np.testing.assert_array_equal(peak_shape["x"], fitted["x"])
    np.testing.assert_allclose(peak_shape["y"], fitted["y"], rtol=1e-12)


def _signal(**attrs):
    return xr.DataArray(
        np.array([0.0, 5.0, 0.0]),
        dims=["mz"],
        coords={"mz": [99.99999, 100.0, 100.00001]},
        attrs=attrs,
    )


@pytest.fixture
def centroid_fits(monkeypatch):
    """Answer the centroid read here; record the stream each was asked for."""
    asked = []

    def centroid_instrument_functions(filename, stream=None):
        asked.append((filename, stream))
        return "peak shape", partial(m_fit.r_orbi, a=2.0e6), {"peakshape": {}}

    monkeypatch.setattr(
        m_fit, "centroid_instrument_functions", centroid_instrument_functions
    )
    return asked


class TestWhichSignalIsReadOffItsCentroids:
    def test_one_drawn_from_centroids_is(self, monkeypatch, centroid_fits):
        monkeypatch.setattr(m_fit, "get_instrument_type", lambda _name: "orbi")

        peak_shape, _, _ = m_fit.fit_instrument_functions(
            FILENAME,
            sum_signal=_signal(**{m_thermo.SPECTRUM_ATTR: m_thermo.CENTROID_SPECTRUM}),
            stream="+ 40-500 R=120000",
        )

        assert peak_shape == "peak shape"
        assert centroid_fits == [(FILENAME, "+ 40-500 R=120000")]

    def test_a_profile_is_not(self, monkeypatch, centroid_fits):
        monkeypatch.setattr(m_fit, "get_instrument_type", lambda _name: "orbi")

        # Three samples are no peak shape to fit
        with pytest.raises(m_fit.InsufficientPeaksError):
            m_fit.fit_instrument_functions(FILENAME, sum_signal=_signal())

        assert centroid_fits == []

    def test_the_files_own_cached_signal_is_read_for_the_whole_file(
        self, monkeypatch, centroid_fits
    ):
        monkeypatch.setattr(m_fit, "get_instrument_type", lambda _name: "orbi")
        monkeypatch.setattr(
            m_fit,
            "get_sum_signal",
            lambda _name: _signal(
                **{m_thermo.SPECTRUM_ATTR: m_thermo.CENTROID_SPECTRUM}
            ),
        )

        m_fit.fit_instrument_functions(FILENAME, dmz=m_fit.ORBI_FIT_DMZ)

        assert centroid_fits == [(FILENAME, None)]

    def test_a_streams_fit_reads_that_streams_centroids(
        self, monkeypatch, centroid_fits
    ):
        """What a file detected per stream fits each of its streams by."""
        monkeypatch.setattr(m_fit, "get_instrument_type", lambda _name: "orbi")
        monkeypatch.setattr(
            m_stream_functions.m_name,
            "filename_to_datafile_path",
            lambda _name: DATAFILE,
        )
        summed = []

        def compute_sum_signal(datafile_path, stream=None):
            summed.append((datafile_path, stream))
            return (
                _signal(**{m_thermo.SPECTRUM_ATTR: m_thermo.CENTROID_SPECTRUM}),
                3,
            )

        monkeypatch.setattr(
            m_stream_functions.m_thermo, "compute_sum_signal", compute_sum_signal
        )

        m_stream_functions._fit_stream(FILENAME, "- 132-490 R=120000")

        assert summed == [(DATAFILE, "- 132-490 R=120000")]
        assert centroid_fits == [(FILENAME, "- 132-490 R=120000")]
