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
from scipy.stats import median_abs_deviation, spearmanr

from mascope_backend.api.models.calibration.config import calibration_config
from mascope_signal.peak import (
    SNR_CORE_WIDTHS,
    SNR_WINDOW_DMZ,
    OrbiZarrPeakDetector,
    TofPeakDetector,
)


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


def _ions(mz_axis, ion_mzs):
    """Single ions, each a blip one high and a peak's width wide, cut off
    where it falls to nothing, as a TOF counts them."""
    signal = np.zeros_like(mz_axis)
    for mz in ion_mzs:
        sigma = _width(mz) / (2 * np.sqrt(2 * np.log(2)))
        x = (mz_axis - mz) / sigma
        signal += np.where(np.abs(x) <= 3, np.exp(-0.5 * x**2), 0.0)
    return signal


def _counted_spectrum(seed, peak_mzs, peak_ions, ions_per_unit=4.0):
    """A summed spectrum that counts single ions, as an ambient TOF's does:
    stray ions (:func:`_ions`) scattered over the axis at a few per unit of
    mass, and each peak that many ions at its m/z. Most of the axis stays
    exactly zero, so the baseline's MAD is zero."""
    rng = np.random.default_rng(seed)
    mz_axis = np.arange(99.0, 99.0 + len(peak_mzs) + 1, STEP)
    stray = rng.uniform(
        mz_axis[0], mz_axis[-1], rng.poisson(ions_per_unit * len(peak_mzs))
    )
    ion_mzs = np.concatenate([stray, np.repeat(peak_mzs, peak_ions)])
    return mz_axis, _ions(mz_axis, ion_mzs)


def test_a_peak_of_a_few_ions_over_an_empty_baseline_is_not_strong():
    """Over a counted baseline (:func:`_counted_spectrum`) the noise is the
    height one ion leaves, so a peak scores about the ions it holds. The
    standard deviation of a mostly empty baseline shrinks with how rarely an
    ion arrives: three ions scored about fifteen, past the TOF calibration's
    gate of ten and the assignment's full mass precision, as strong as a
    peak of hundreds. An ambient TOF file fits a peak of a few ions at most
    unit masses."""
    peak_mzs = np.array([100.0, 101.0, 102.0, 103.0])
    peak_ions = np.array([3, 3, 4, 300])
    mz_axis, signal = _counted_spectrum(7, peak_mzs, peak_ions)
    assert median_abs_deviation(signal, scale="normal") == 0

    snr = _snr(mz_axis, signal, peak_mzs, peak_ions.astype(float))

    noise_peaks, real = snr[:3], snr[3]
    assert np.all(noise_peaks < 6)
    assert np.all(noise_peaks > 1)
    assert real >= calibration_config.TOF_SNR_THRESHOLD * 10


def test_one_ions_height_is_measured_over_the_whole_spectrum():
    """A peak whose own window holds no stray ion still reads its ions: the
    height of one is measured wherever the baseline is empty, not only
    around the peak. Its baseline is all zeros, which had no ratio."""
    mz_axis = np.arange(99.0, 103.0, STEP)
    stray = [101.2, 101.4, 101.7, 102.3, 102.6]
    signal = _ions(mz_axis, stray + [100.0] * 3 + [102.0] * 200)

    snr = _snr(mz_axis, signal, [100.0, 102.0], [3.0, 200.0])

    assert snr == pytest.approx([3.0, 200.0], rel=0.1)


def test_a_peaks_own_ions_are_not_one_ion():
    """The ions of a fitted peak are one run above zero, as a single ion
    is; a run in a peak's core is left out of one ion's height, so a
    spectrum of more peaks than stray ions does not read a peak as one."""
    peak_mzs = np.arange(100.0, 106.0)
    mz_axis = np.arange(99.0, 107.0, STEP)
    stray = [100.4, 102.6, 104.3]
    signal = _ions(mz_axis, stray + list(np.repeat(peak_mzs, 40)))

    snr = _snr(mz_axis, signal, peak_mzs, np.full(peak_mzs.size, 40.0))

    assert snr == pytest.approx(np.full(peak_mzs.size, 40.0), rel=0.1)


def test_an_unfitted_cluster_over_an_empty_baseline_is_its_spread():
    """Where the MAD is zero the noise is one ion's height or the standard
    deviation, whichever is larger. A mostly empty window that holds a small
    cluster too weak to be fitted spreads wider than one ion, and the peak
    beside it divides by that spread, as it did before the floor."""
    mz_axis = np.arange(99.0, 102.0, STEP)
    stray = [99.2, 99.4, 99.7, 101.2, 101.4, 101.7]
    signal = _ions(mz_axis, stray + [100.0] * 200 + [100.5] * 40)
    window = np.abs(mz_axis - 100.0) <= max(SNR_WINDOW_DMZ, 10 * _width(100.0))
    core = np.abs(mz_axis - 100.0) <= SNR_CORE_WIDTHS * _width(100.0)
    baseline = signal[window & ~core]
    assert median_abs_deviation(baseline, scale="normal") == 0
    spread = np.std(baseline)
    assert spread > 2  # one ion is 1 high

    (snr,) = _snr(mz_axis, signal, [100.0], [200.0])

    assert snr == pytest.approx(200.0 / spread, rel=1e-6)


def test_a_counted_baseline_is_not_one_ion():
    """Where the baseline is dense its MAD is its noise, and its stretches
    above zero are not single ions: one ion's height is read only where the
    baseline is mostly empty."""
    rng = np.random.default_rng(8)
    mz_axis = np.arange(99.0, 105.0, STEP)
    stray = [99.3, 99.6, 100.4, 100.7]
    signal = _ions(mz_axis, stray + [100.0] * 3 + [103.0] * 300)
    dense = mz_axis > 101.6
    signal[dense] += rng.poisson(3.0, dense.sum())

    weak, strong = _snr(mz_axis, signal, [100.0, 103.0], [3.0, 300.0])

    assert weak == pytest.approx(3.0, rel=0.1)
    assert strong == pytest.approx(300.0 / np.sqrt(3.0), rel=0.25)


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
