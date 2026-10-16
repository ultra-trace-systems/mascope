"""The signal-to-noise a TOF peak list carries, and that it tracks height.

The column measured the noise as the standard deviation of whatever lay
between one and ten widths of a peak. In a TOF spectrum that is the peak's own
wings and its neighbours at the same unit mass, so the noise grew with the
heights around it: over a file's picked peaks the column did not follow their
heights (a reference engine measured Spearman -0.07 to 0.17 on a TOF batch,
where an Orbitrap file gives 0.99), and the assignment engine reads it to
decide whether an absent isotope line is evidence. These pin the measurement
on synthetic summed TOF spectra: a Gaussian peak of the resolution function's
width over a Poisson baseline.
"""

import warnings

import numpy as np
import pytest
import xarray
from scipy.stats import spearmanr

from mascope_backend.api.models.calibration.config import calibration_config
from mascope_signal.peak import OrbiZarrPeakDetector, TofPeakDetector


RESOLUTION = 5000.0
STEP = 0.004


def _resolution(mz):
    return np.full_like(np.asarray(mz, dtype=float), RESOLUTION)


def _spectrum(mz_axis, peak_mzs, peak_heights, tail=0.0):
    """Peaks of the resolution's width, with a Lorentzian share ``tail``
    standing in for the wings a TOF peak has beyond a Gaussian's."""
    signal = np.zeros_like(mz_axis)
    for mz, height in zip(peak_mzs, peak_heights):
        sigma = mz / RESOLUTION / (2 * np.sqrt(2 * np.log(2)))
        x = (mz_axis - mz) / sigma
        signal += height * (
            (1 - tail) * np.exp(-0.5 * x**2) + tail / (1 + (x / 1.177) ** 2)
        )
    return signal


def _snr(mz_axis, signal, peak_mzs, peak_heights):
    detector = TofPeakDetector.__new__(TofPeakDetector)
    detector._sum_signal = xarray.DataArray(signal, coords={"mz": mz_axis}, dims="mz")
    detector._resolution_function = _resolution
    return detector._compute_snr(np.asarray(peak_mzs), np.asarray(peak_heights))


def _width(mz):
    return mz / RESOLUTION


def _crowded_spectrum(seed, baseline, tail):
    """One to five peaks per unit mass over three decades of height, on a
    Poisson baseline that varies along the axis: the m/z axis, the counts,
    the peaks and their heights, and the noise each peak's baseline truly
    has."""
    rng = np.random.default_rng(seed)
    mz_axis = np.arange(40.0, 300.0, STEP)
    peak_mzs, peak_heights = [], []
    for unit in range(45, 295):
        for _ in range(rng.integers(1, 6)):
            peak_mzs.append(unit + rng.uniform(-0.15, 0.25))
            peak_heights.append(10 ** rng.uniform(0.5, 4))
    order = np.argsort(peak_mzs)
    peak_mzs = np.asarray(peak_mzs)[order]
    peak_heights = np.asarray(peak_heights)[order]
    level = baseline * (1 + np.sin(mz_axis / 30))
    signal = rng.poisson(_spectrum(mz_axis, peak_mzs, peak_heights, tail) + level)
    true_noise = np.sqrt(baseline * (1 + np.sin(peak_mzs / 30)))
    return mz_axis, signal.astype(float), peak_mzs, peak_heights, true_noise


@pytest.mark.parametrize(
    "baseline, tail",
    [
        pytest.param(2.0, 0.0, id="gaussian"),
        pytest.param(2.0, 0.3, id="wings"),
        pytest.param(0.05, 0.3, id="sparse-baseline"),
        pytest.param(20.0, 0.3, id="high-baseline"),
    ],
)
def test_snr_tracks_height_over_a_crowded_spectrum(baseline, tail):
    """A crowded spectrum (:func:`_crowded_spectrum`): what a TOF file's peak
    list is."""
    mz_axis, signal, peak_mzs, peak_heights, _ = _crowded_spectrum(1, baseline, tail)

    snr = _snr(mz_axis, signal, peak_mzs, peak_heights)

    finite = np.isfinite(snr)
    assert finite.mean() > 0.8
    assert spearmanr(snr[finite], peak_heights[finite])[0] >= 0.9


def test_a_peaks_own_wings_are_not_its_noise():
    """Ten times the height is ten times the ratio. Measured between one and
    ten widths, an isolated peak's noise was its own wings, and the two read
    about the same. The wings are a modest Lorentzian share: a strong peak's
    far wing still lifts the baseline around it some, which a heavy one
    would make visible here."""
    rng = np.random.default_rng(2)
    mz_axis = np.arange(99.0, 103.0, STEP)
    peak_mzs, peak_heights = [100.0, 102.0], [500.0, 5000.0]
    signal = _spectrum(mz_axis, peak_mzs, peak_heights, tail=0.05)
    signal += rng.normal(0.0, 1.0, mz_axis.size)

    weak, strong = _snr(mz_axis, signal, peak_mzs, peak_heights)

    assert strong / weak == pytest.approx(10.0, rel=0.25)


def test_a_strong_neighbour_is_not_a_peaks_noise():
    """A peak beside one a hundred times its height reads as it does alone:
    every fitted peak's core is cut out of the baseline, not only its own."""
    rng = np.random.default_rng(3)
    mz_axis = np.arange(99.0, 101.0, STEP)
    noise = rng.normal(0.0, 1.0, mz_axis.size)
    alone = _snr(
        mz_axis, _spectrum(mz_axis, [100.0], [200.0]) + noise, [100.0], [200.0]
    )
    peak_mzs, peak_heights = [100.0, 100.0 + 6 * _width(100.0)], [200.0, 20000.0]
    signal = _spectrum(mz_axis, peak_mzs, peak_heights) + noise

    beside = _snr(mz_axis, signal, peak_mzs, peak_heights)

    assert beside[0] == pytest.approx(alone[0], rel=0.1)


def test_an_unfitted_peak_in_the_baseline_is_not_noise():
    """A peak too small to be fitted sits in the baseline window, and the
    spread is the MAD's, so it moves the noise little; the standard deviation
    would read it as noise."""
    rng = np.random.default_rng(4)
    mz_axis = np.arange(99.0, 101.0, STEP)
    noise = rng.normal(0.0, 1.0, mz_axis.size)
    alone = _snr(
        mz_axis, _spectrum(mz_axis, [100.0], [200.0]) + noise, [100.0], [200.0]
    )
    signal = _spectrum(mz_axis, [100.0, 100.3], [200.0, 60.0]) + noise

    with_unfitted = _snr(mz_axis, signal, [100.0], [200.0])

    assert with_unfitted[0] == pytest.approx(alone[0], rel=0.1)


def test_an_empty_baseline_falls_back_to_its_spread():
    """A summed spectrum with a few counts in a mostly empty baseline has a
    MAD of zero; the standard deviation stands in, so the ratio is a number
    rather than undefined."""
    mz_axis = np.arange(99.0, 101.0, STEP)
    signal = _spectrum(mz_axis, [100.0], [200.0])
    # Nothing but the counts beyond the core, not even the peak's own tail
    signal[np.abs(mz_axis - 100.0) > 3 * _width(100.0)] = 0.0
    signal[::40] += 3.0

    (snr,) = _snr(mz_axis, signal, [100.0], [200.0])

    assert np.isfinite(snr)
    assert snr > 3


def test_the_baseline_reaches_past_a_crowded_unit_mass():
    """Five peaks four widths apart cover the middle one's ten widths; the
    window reaches half a unit either side, where the baseline is."""
    rng = np.random.default_rng(5)
    # Sampled finely enough that the baseline left is not a handful of points
    mz_axis = np.arange(199.0, 201.0, STEP / 4)
    width = _width(200.0)
    peak_mzs = 200.0 + width * np.array([-8.0, -4.0, 0.0, 4.0, 8.0])
    peak_heights = np.full(5, 300.0)
    signal = _spectrum(mz_axis, peak_mzs, peak_heights)
    signal += rng.normal(0.0, 1.0, mz_axis.size)

    snr = _snr(mz_axis, signal, peak_mzs, peak_heights)

    assert snr[2] == pytest.approx(300.0, rel=0.25)


@pytest.mark.parametrize(
    "signal_offset",
    [pytest.param(None, id="no-baseline"), pytest.param(0.0, id="flat-baseline")],
)
def test_no_measurable_baseline_has_no_ratio(signal_offset):
    """A peak whose window holds nothing but fitted cores, or a baseline that
    does not vary at all, has no noise to divide by."""
    if signal_offset is None:
        mz_axis = np.arange(100.0 - 0.05, 100.05, STEP)
    else:
        mz_axis = np.arange(99.0, 101.0, STEP)
    signal = _spectrum(mz_axis, [100.0], [200.0])
    if signal_offset is not None:
        # Exactly flat: the peak itself added onto zeros, nothing beside it
        core = np.abs(mz_axis - 100.0) <= 3 * _width(100.0)
        signal[~core] = signal_offset

    # Answered before any statistic is taken over an empty baseline, so
    # nothing warns about one either
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        (snr,) = _snr(mz_axis, signal, [100.0], [200.0])

    assert np.isnan(snr)


@pytest.mark.parametrize(
    "baseline, tail, strong",
    [
        pytest.param(2.0, 0.0, 2, id="low"),
        pytest.param(20.0, 0.0, 2, id="high"),
        # The neighbours' wings are counts in a peak's baseline too, and they
        # lift its noise above the baseline's own
        pytest.param(2.0, 0.3, 4, id="wings"),
    ],
)
def test_the_tof_calibration_gate_reads_as_times_the_noise(baseline, tail, strong):
    """TOF m/z calibration keeps a calibrant candidate whose signal-to-noise
    clears ``TOF_SNR_THRESHOLD``. The column now says how many times the
    baseline's noise a peak stands, so the gate keeps the peaks well above
    ten times it and drops the ones well below, whatever stands beside them.
    Before the fix it kept about a third of the peaks, and which third did
    not follow their heights."""
    mz_axis, signal, peak_mzs, peak_heights, true_noise = _crowded_spectrum(
        1, baseline, tail
    )
    threshold = calibration_config.TOF_SNR_THRESHOLD

    passes = _snr(mz_axis, signal, peak_mzs, peak_heights) >= threshold

    true_snr = peak_heights / true_noise
    assert passes[true_snr >= strong * threshold].mean() >= 0.95
    assert passes[true_snr <= threshold / 2].mean() <= 0.05


def test_an_orbitrap_without_its_raw_file_keeps_its_signal_to_noise():
    """An ``orbi_zarr`` sample is picked on the TOF path, but its
    signal-to-noise stays the standard deviation between one and ten widths
    of the peak: the baseline fix is measured on TOF spectra, not on
    Orbitrap profiles."""
    mz_axis, signal, peak_mzs, peak_heights, _ = _crowded_spectrum(6, 2.0, 0.0)
    detector = OrbiZarrPeakDetector.__new__(OrbiZarrPeakDetector)
    detector._sum_signal = xarray.DataArray(signal, coords={"mz": mz_axis}, dims="mz")
    detector._resolution_function = _resolution

    snr = detector._compute_snr(peak_mzs, peak_heights)

    widths = _width(peak_mzs)
    expected = np.empty_like(peak_heights)
    for i, (mz, height) in enumerate(zip(peak_mzs, peak_heights)):
        distance = np.abs(mz_axis - mz)
        noise = np.std(signal[(distance >= widths[i]) & (distance <= 10 * widths[i])])
        expected[i] = height / noise if noise > 0 else np.nan
    np.testing.assert_allclose(snr, expected, rtol=1e-9)
    assert not np.allclose(
        snr, _snr(mz_axis, signal, peak_mzs, peak_heights), equal_nan=True
    )
