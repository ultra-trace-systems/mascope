"""An acquisition that stored centroids alone is converted with its centroids as peaks.

Such a file holds no profile. Its averaged profile used to come back empty
under OpenTFRaw, the instrument-function fit found no peak on it, and the
file was stored as a blank measurement: no peaks, nothing to calibrate,
match or assign, although every scan holds the instrument's own centroids.
Under the Thermo library the centroids stood in for a profile instead, and
the fit and the spectrum were read off sticks.

Now its summed signal is its centroids, drawn as sticks and marked as such;
its instrument function is read off the centroids' resolutions, with a
Gaussian peak shape; and its peaks are the centroids, averaged over its
scans as every Orbitrap file's are. The fixture reads the committed MS1 file
as such an acquisition (``tests/centroid_acquisition.py``), so the
centroids are real ones.
"""

import numpy as np
import opentfraw
import pytest
from centroid_acquisition import SOURCE_FILE, presented
from ms2_fixture import convert

import mascope_file.io as m_io
import mascope_thermo.thermo as m_thermo
from mascope_signal.compute import get_sum_signal
from mascope_signal.instrument_func.fit import CENTROID_FIT_EXCLUDE, CENTROID_MIN_SNR
from mascope_thermo.processor import RawProcessor


@pytest.fixture(scope="module")
def conversion(tmp_path_factory):
    """The acquisition converted, and the instrument functions its
    conversion fitted, as the converter recorded them."""
    if not SOURCE_FILE.exists():
        pytest.skip(f"sample file missing: {SOURCE_FILE}")
    fitted = []
    process = RawProcessor._process_instrument_config

    def recorded(self, props):
        functions = process(self, props)
        fitted.append(functions)
        return functions

    filestore = tmp_path_factory.mktemp("filestore_centroids")
    with (
        pytest.MonkeyPatch.context() as monkeypatch,
        presented() as source,
    ):
        monkeypatch.setattr(RawProcessor, "_process_instrument_config", recorded)
        with convert(source, filestore) as done:
            store = m_io.load_array(done.props.filename, "peak_timeseries").compute()
            signal = get_sum_signal(done.props.filename).compute()
            yield done, store, signal, fitted


def _labels():
    """Every centroid the file's scans report, as the backend keeps them:
    those with a finite resolution and signal-to-noise."""
    raw = opentfraw.RawFile(str(SOURCE_FILE))
    labels = [raw.centroid_labels(n) for n in range(1, raw.num_scans + 1)]

    def kept(name):
        return np.concatenate(
            [
                np.asarray(label[name], dtype=np.float64)[
                    np.isfinite(label["resolution"])
                    & np.isfinite(label["signal_to_noise"])
                ]
                for label in labels
            ]
        )

    return kept("mz"), kept("intensity")


def test_it_is_stored_with_peaks_and_an_instrument_function(conversion):
    done, store, _, _ = conversion

    ((props, instrument_function),) = done.records
    assert props.filename == done.props.filename
    assert instrument_function == "instrument-config"
    assert store.sizes["mz"] > 0


def test_its_census_says_its_scans_stored_centroids(conversion):
    done, _, _, _ = conversion

    (stream,) = done.props.scan_streams
    assert stream["signature"]["data_type"] == "c"


def test_its_peaks_are_its_centroids_averaged_over_its_scans(conversion):
    """Each peak is a centroid the instrument reported, and together they
    hold every count the scans' centroids do: nothing is read off a profile."""
    _, store, _, _ = conversion
    label_mz, label_intensity = _labels()

    peak_mz = store.mz.values
    nearest = np.abs(peak_mz[:, None] - label_mz[None, :]).min(axis=1)
    assert np.all(nearest / peak_mz * 1e6 < 1)
    assert store.sum_peak_heights.values.sum() == pytest.approx(
        label_intensity.sum(), rel=1e-9
    )


def test_its_peaks_are_what_the_reader_averages(conversion):
    _, store, _, _ = conversion
    with presented() as source:
        masses, intensities, _, signal_to_noise = m_thermo.get_centroids(
            str(source), polarity="+"
        )

    np.testing.assert_array_equal(store.mz.values, masses)
    np.testing.assert_array_equal(store.sum_peak_heights.values, intensities)
    np.testing.assert_array_equal(store.signal_to_noise.values, signal_to_noise)


def test_its_spectrum_is_its_centroids_drawn_as_sticks(conversion):
    _, store, signal, _ = conversion

    assert signal.attrs[m_thermo.SPECTRUM_ATTR] == m_thermo.CENTROID_SPECTRUM
    mz, values = signal.mz.values, signal.values
    assert np.all(np.diff(mz) > 0)
    np.testing.assert_array_equal(mz[1::3], store.mz.values)
    np.testing.assert_array_equal(values[1::3], store.sum_peak_heights.values)
    assert not values[0::3].any() and not values[2::3].any()


def test_its_instrument_function_is_read_off_its_centroids(conversion):
    """The file is a weak one: none of its centroids reaches the ratio the
    strong ones are counted at, so every one whose width is its own is."""
    _, _, _, fitted = conversion
    ((peak_shape, resolution_function, _, _),) = fitted
    with presented() as source:
        scans = m_thermo.get_centroids_per_scan(
            str(source), scan_type="Ms", exclude=CENTROID_FIT_EXCLUDE
        )
    masses = np.concatenate([scan["masses"] for scan in scans])
    resolutions = np.concatenate([scan["resolutions"] for scan in scans])
    signal_to_noise = np.concatenate([scan["signal_to_noise"] for scan in scans])
    assert not (signal_to_noise >= CENTROID_MIN_SNR).any()

    assert resolution_function == [
        pytest.approx(np.median(resolutions * np.sqrt(masses)))
    ]
    # A Gaussian, as wide as one sigma per unit of x
    x, y = np.asarray(peak_shape.x), np.asarray(peak_shape.y)
    np.testing.assert_allclose(y / y.max(), np.exp(-(x**2) / 2), atol=1e-12)
