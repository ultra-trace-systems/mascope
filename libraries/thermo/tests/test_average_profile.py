"""Averaging a profile, in the frequency domain and in the m/z fallback.

``average_profile`` averages in the frequency domain, which needs each scan's
Conversion Parameters B and C from its trailer. A non-FTMS scan carries none,
and then the m/z-domain fallback runs: a constant-ppm m/z grid, summed with an
integral-conserving interpolation. The first tests drive that fallback with a
fake reader whose trailer holds no conversion parameters. The rest drive the
frequency path: its grid has to land on the samples it averages, every scan
count beside its own samples only, each of its peaks be written on the
calibration of the scans that peak came from, and the baseline fall where no
scan stored anything.
"""

import numpy as np
import pytest

from mascope_thermo.backend import (
    _ZEROFILL_EDGE_PPM,
    _ZEROFILL_GAP_FACTOR,
    OpenTFRawBackend,
)


MZ = 301.2
FWHM_PPM = 8.0
HEIGHT = 1e5
SCANS = [1, 2, 3]


def _peak(centre: float = MZ, fwhm_ppm: float = FWHM_PPM, points: int = 81) -> tuple:
    """One Gaussian peak sampled evenly in m/z, out to where it has died away."""
    fwhm = centre * fwhm_ppm / 1e6
    sigma = fwhm / (2 * np.sqrt(2 * np.log(2)))
    mz = np.linspace(centre - 4 * fwhm, centre + 4 * fwhm, points)
    return mz, HEIGHT * np.exp(-0.5 * ((mz - centre) / sigma) ** 2)


class _FakeRaw:
    """The slice of ``opentfraw.RawFile`` that ``average_profile`` touches. The
    trailer carries no Conversion Parameters, as a non-FTMS scan's does not.
    """

    def __init__(self, profile: tuple):
        self._profile = profile

    def profile(self, scan_number):
        return self._profile

    def scan_parameters(self, scan_number):
        return {"Ion Injection Time (ms):": 10.0}


def _backend() -> OpenTFRawBackend:
    backend = OpenTFRawBackend("unused.raw")
    backend._raw = _FakeRaw(_peak())
    return backend


def test_a_scan_without_conversion_parameters_still_averages():
    """The fallback returns the summed profile, with its apex where the peak is
    and the area of every scan it summed."""
    mz, intensity = _peak()
    grid, summed, combined = _backend().average_profile(SCANS)

    assert combined == len(SCANS)
    assert grid.size and summed.size
    assert np.all(np.isfinite(grid)) and np.all(np.isfinite(summed))
    apex = float(grid[np.argmax(summed)])
    assert abs(apex - MZ) / MZ * 1e6 < 1.0
    assert float(np.trapezoid(summed, grid)) == pytest.approx(
        len(SCANS) * float(np.trapezoid(intensity, mz)), rel=1e-3
    )


def test_averaging_divides_by_the_scans_combined():
    mz, intensity = _peak()
    grid, summed, _ = _backend().average_profile(SCANS, average=True)

    assert float(np.trapezoid(summed, grid)) == pytest.approx(
        float(np.trapezoid(intensity, mz)), rel=1e-3
    )


# -- the FTMS path: the output grid must land on the samples it averages --

# Realistic Orbitrap conversion parameters, m/z = B/f^2 + C/f^4.
_B = 169405382.13
_C = -71563003.56
# Where each sample sits inside its native cell, as a fraction of the cell. Any
# value clear of 0 and 1 will do: it only has to be somewhere the centre of the
# cell is not, so that a grid built at cell centres cannot pass by accident.
_SUB_CELL = 0.2


def _ftms_scan(points: int = 400) -> tuple:
    """A profile sampled on a uniform frequency grid, as an FT transient's is.

    Two Gaussian peaks, so the profile has slope nearly everywhere and reading
    it half a cell off cannot return the stored value by chance.
    """
    f_start = np.sqrt(_B / 301.0)
    df = (np.sqrt(_B / 300.0) - f_start) / points
    # The first sample anchors the cell lattice, so it sits at the cell's edge;
    # the rest sit _SUB_CELL into theirs.
    offsets = np.concatenate(([0.0], np.arange(1, points) + _SUB_CELL))
    freq = f_start + offsets * df
    mz = _B / freq**2 + _C / freq**4
    intensity = np.zeros_like(mz)
    for centre, height in ((300.25, 1e5), (300.75, 4e4)):
        sigma = centre * FWHM_PPM / 1e6 / (2 * np.sqrt(2 * np.log(2)))
        intensity += height * np.exp(-0.5 * ((mz - centre) / sigma) ** 2)
    return mz, intensity


def test_the_frequency_grid_lands_on_the_samples_it_averages():
    """One scan in, and every grid point comes back at a stored sample, carrying
    that sample's intensity.

    With a single scan there is nothing to average, so the averaged profile can
    only be the scan's own samples. That pins where the output grid sits: a grid
    built at the centres of the native cells lands between the samples, and the
    interpolation then returns a chord across the peak rather than the measured
    value. A raw file keeps only about three points per peak width, so that
    costs several percent of the apex -- and it costs a varying amount, because
    the phase between the cells and the samples drifts across the mass range,
    which is how a change in the reader's m/z axis used to move peak heights.
    """
    mz, intensity = _ftms_scan()
    grid, summed, _ = OpenTFRawBackend("unused.raw")._average_profile_in_frequency(
        [(mz, intensity, _B, _C)]
    )

    assert grid.size and np.all(np.diff(grid) > 0)
    order = np.argsort(mz)
    stored_mz, stored_intensity = mz[order], intensity[order]
    nearest = np.abs(grid[:, None] - stored_mz[None, :]).argmin(axis=1)

    assert np.max(np.abs(grid - stored_mz[nearest]) / grid) < 1e-9, (
        "grid points sit between the stored samples, not on them"
    )
    assert summed == pytest.approx(stored_intensity[nearest], rel=1e-9), (
        "the averaged profile does not carry the stored intensities"
    )
    # The apex is the tallest sample itself, not an interpolation short of it.
    assert summed.max() == pytest.approx(stored_intensity.max(), rel=1e-9)


# -- scans that sample the same bins a few percent of a bin apart --


def _scans_on_shared_bins(
    spans: tuple, heights: tuple, shifts: tuple, points: int = 200
) -> tuple[list[tuple], np.ndarray]:
    """Flat profiles on one lattice of bins, each scan displaced from it by its
    own fraction of a bin and holding its own runs of bins.

    The scans of a file sample the same bins, but the frequencies recovered
    from what each one wrote agree to a few percent of a bin, not to the last
    digit. A flat profile reads the same wherever it is interpolated, so the
    sum at a grid point is exactly the heights of the scans that reach it.
    Returns the scans and, bin by bin, the heights of the scans storing it.
    """
    f_start = np.sqrt(_B / 301.0)
    df = (np.sqrt(_B / 300.0) - f_start) / points
    offsets = np.concatenate(([0.0], np.arange(1, points) + _SUB_CELL))
    scans, stored = [], np.zeros(points)
    for runs, height, shift in zip(spans, heights, shifts):
        held = np.zeros(points, dtype=bool)
        for first, last in runs:
            held[first : last + 1] = True
        freq = f_start + (offsets[held] + shift) * df
        mz = _B / freq**2 + _C / freq**4
        scans.append((mz, np.full(mz.size, height), _B, _C))
        stored[held] += height
    return scans, stored


@pytest.mark.parametrize(
    ("spans", "heights", "shifts"),
    [
        pytest.param(
            ([(0, 199)], [(40, 159)], [(40, 159)]),
            (1.0, 2.0, 4.0),
            (0.0, 0.03, -0.03),
            id="scans a few percent of a bin apart",
        ),
        pytest.param(
            ([(0, 199)], [(0, 199)], [(40, 199)]),
            (1.0, 2.0, 4.0),
            (0.0, 0.0, 0.7),
            id="a grid point nearly half a bin outside the range",
        ),
        pytest.param(
            ([(0, 199)], [(40, 159)]),
            (1.0, 2.0),
            (0.0, 0.4),
            id="the next bin's grid point just over half a bin away",
        ),
    ],
)
def test_a_scan_reaches_the_sum_at_its_first_and_last_samples(spans, heights, shifts):
    """Every scan adds its first and its last stored sample to the sum, and
    nothing in the bin beyond them.

    A grid point is the mean of the frequencies the scans sampled in its cell,
    so a scan's own sample lies to one side of it, above as often as below. At
    the two ends of a scan's stored range that leaves the grid point outside
    the range half the time, and the scan belongs in the sum there all the
    same. In the first case the second scan's first sample lies 3% of a bin
    above its grid point and the third scan's last sample as far below its
    own. In the second the third scan begins 0.47 of a bin above its grid
    point, so less than half a bin of reach would not do. In the third the
    grid point of the bin after the second scan's last lies 0.6 of a bin
    beyond it, so much more than half a bin would be too much.
    """
    scans, stored = _scans_on_shared_bins(spans, heights, shifts)
    backend = OpenTFRawBackend("unused.raw")
    grid, summed, _ = backend._average_profile_in_frequency(scans)

    assert grid.size == stored.size
    # The grid ascends in m/z, the bins in frequency.
    np.testing.assert_allclose(summed[::-1], stored, rtol=1e-12)


def test_a_scan_adds_nothing_where_it_stored_nothing():
    """A scan stays out of the sum away from its own samples: between two of
    its clusters, and from the bin next to either end of its range.

    A raw file keeps a scan's profile around its peaks only, and interpolating
    a scan across what it left out would draw a ramp from one cluster to the
    next, under every peak another scan holds in between. A scan therefore
    counts within two bins of its own samples and no further, and past the two
    ends of its range for its own end bins alone.

    The second scan is displaced by 0.4 of a bin, which keeps every grid point
    clear of the two-bin threshold: the bins between its clusters lie 0.6, 1.6
    and 2.6 bins from its last sample on one side, and 1.4, 2.4 and 3.4 from
    its first on the other. Displaced by a few percent they would lie on the
    threshold itself, two bins give or take the displacement away.
    """
    scans, stored = _scans_on_shared_bins(
        spans=([(0, 199)], [(40, 79), (120, 159)]),
        heights=(1.0, 2.0),
        shifts=(0.0, 0.4),
    )
    backend = OpenTFRawBackend("unused.raw")
    grid, summed, _ = backend._average_profile_in_frequency(scans)
    summed = summed[::-1]

    assert grid.size == stored.size
    np.testing.assert_allclose(summed[stored == 3.0], 3.0, rtol=1e-12)
    # Bins 39 and 160 lie next to the second scan's range.
    assert summed[39] == 1.0 and summed[160] == 1.0
    # Bins 80-119 lie between its clusters. It still counts in the three
    # within two bins of a sample of its own, and in none of the rest.
    np.testing.assert_allclose(summed[[80, 81, 119]], 3.0, rtol=1e-12)
    np.testing.assert_allclose(summed[82:119], 1.0, rtol=1e-12)


# -- several scans, each written on a calibration of its own --

# Seven scans of the same ions, scans 1-3 written on a calibration 2.5 ppm above
# that of scans 4-7, as when a lock mass engages part-way through a file.
_STEP_PPM = 2.5
_STEP_SCANS = list(range(1, 8))


class _FakeFtmsRaw:
    """Scans that sample one frequency grid, as every scan of a file does, and
    differ only in the calibration each is written with: profile, centroid
    labels and the Conversion Parameters behind both."""

    def __init__(self, scans: dict[int, dict]):
        self._scans = scans

    def profile(self, scan_number):
        return self._scans[scan_number]["profile"]

    def centroid_labels(self, scan_number):
        return self._scans[scan_number]["labels"]

    def scan_parameters(self, scan_number):
        return self._scans[scan_number]["params"]


def _stepped_ftms_scans(
    ions: tuple = ((300, 1e5), (900, 4e4)),
    step_ppm: float = _STEP_PPM,
    floor: float = 0.0,
    points: int = 1200,
) -> tuple[dict[int, dict], np.ndarray]:
    """Ions centred on a sample, so that an ion's tallest sample is its apex,
    written out by every scan on that scan's calibration.

    An ion is the index of its sample and its height, either one height for
    every scan or one per scan, 0 where the scan does not hold the ion. A scan
    stores nothing below ``floor``, as a raw file drops what lies under a
    scan's noise. Returns the scans and the frequencies they all sample.
    """
    f_start = np.sqrt(_B / 301.0)
    df = (np.sqrt(_B / 300.0) - f_start) / points
    offsets = np.concatenate(([0.0], np.arange(1, points) + _SUB_CELL))
    freq = f_start + offsets * df
    scans = {}
    for n in _STEP_SCANS:
        scale = 1 + step_ppm / 1e6 if n <= 3 else 1.0
        b, c = _B * scale, _C * scale
        held = [
            (freq[index], np.broadcast_to(heights, len(_STEP_SCANS))[n - 1])
            for index, heights in ions
        ]
        held = [(f_ion, height) for f_ion, height in held if height > 0]
        intensity = np.zeros(points)
        for f_ion, height in held:
            # m/z goes as 1/f^2, so a peak is half as wide in frequency, relatively.
            sigma = f_ion * FWHM_PPM / 2 / 1e6 / (2 * np.sqrt(2 * np.log(2)))
            intensity += height * np.exp(-0.5 * ((freq - f_ion) / sigma) ** 2)
        intensity[intensity < floor] = 0.0
        f_ions = np.array([f_ion for f_ion, _ in held])
        scans[n] = {
            "profile": (b / freq**2 + c / freq**4, intensity),
            "labels": {
                "mz": b / f_ions**2 + c / f_ions**4,
                "intensity": np.array([height for _, height in held]),
                "resolution": np.full(len(held), 1e6 / FWHM_PPM),
                "signal_to_noise": np.full(len(held), 4000.0),
            },
            "params": {"Conversion Parameter B:": b, "Conversion Parameter C:": c},
        }
    return scans, freq


def _stepped_backend(scans: dict[int, dict]) -> OpenTFRawBackend:
    backend = OpenTFRawBackend("unused.raw")
    backend._raw = _FakeFtmsRaw(scans)
    return backend


def _apex_offsets_ppm(grid, summed, masses) -> np.ndarray:
    """How far the profile's tallest sample near each centroid sits from it."""
    offsets = []
    for mass in masses:
        near = np.abs(grid - mass) / mass * 1e6 < 2 * FWHM_PPM
        apex = grid[near][np.argmax(summed[near])]
        offsets.append((apex - mass) / mass * 1e6)
    return np.array(offsets)


def _mean_calibration_mz(scans: dict[int, dict], freq: np.ndarray) -> np.ndarray:
    """Frequencies written out on the plain mean of the scans' calibrations."""
    b = np.mean([scan["params"]["Conversion Parameter B:"] for scan in scans.values()])
    c = np.mean([scan["params"]["Conversion Parameter C:"] for scan in scans.values()])
    return b / freq**2 + c / freq**4


def test_the_profile_is_written_on_the_mean_of_the_scans_calibrations():
    """Across a calibration step the averaged profile's peaks sit on the
    averaged centroids.

    An ion's frequency is the same in every scan, but each scan writes it out
    on its own calibration, so a step moves every label of the scans on one
    side of it. An averaged centroid reports the mean of its labels as written,
    which for an ion present alike in every scan is its frequency on the mean
    calibration, and its profile peak is written on that too. On any one
    scan's calibration it would sit as far off the centroids as that scan is
    from the mean: 1.4 ppm for scans 1-3 here, a sixth of the peak's width.
    The spectrum views draw the centroids over the profile, and a centroid's
    height is read off the profile apex within 3 ppm of it.
    """
    scans, _ = _stepped_ftms_scans()
    backend = _stepped_backend(scans)

    masses, *_ = backend.average_centroids(_STEP_SCANS)
    grid, summed, _ = backend.average_profile(_STEP_SCANS)

    assert masses.size == 2
    # The step is there to be averaged: scan 1 writes each ion 4/7 of it above
    # the ion's averaged centroid.
    above = (np.sort(scans[1]["labels"]["mz"]) - masses) / masses * 1e6
    np.testing.assert_allclose(above, _STEP_PPM * 4 / 7, rtol=1e-3)
    for offset in _apex_offsets_ppm(grid, summed, masses):
        assert abs(offset) < 0.01, f"the profile peaks {offset:+.3f} ppm off"


def test_an_ion_is_written_on_the_calibration_of_the_scans_it_was_in():
    """An ion that is there for part of a file has its profile peak on its
    centroid too, beside one that is there throughout.

    An averaged centroid weighs each scan's label by the ion's intensity in
    that scan, so an ion that only appears after the calibration stepped
    reports its frequency on the later scans' calibration, while one present
    alike in every scan reports it on the mean of them all. No one calibration
    writes both where their centroids are: on the mean, the late ion's peak
    sits 3/7 of the step above its centroid. Each profile peak is written on
    the weighted calibration of its own scans instead.
    """
    late = [0, 0, 0, 4e4, 4e4, 4e4, 4e4]
    scans, freq = _stepped_ftms_scans(ions=((300, 1e5), (900, late)))
    backend = _stepped_backend(scans)

    masses, *_ = backend.average_centroids(_STEP_SCANS)
    grid, summed, _ = backend.average_profile(_STEP_SCANS)

    # Ascending m/z: the late ion, at the higher frequency, comes first.
    assert masses.size == 2
    on_the_mean = _mean_calibration_mz(scans, freq[[900, 300]])
    np.testing.assert_allclose(
        (on_the_mean - masses) / masses * 1e6, [_STEP_PPM * 3 / 7, 0.0], atol=1e-3
    )
    for offset in _apex_offsets_ppm(grid, summed, masses):
        assert abs(offset) < 0.01, f"the profile peaks {offset:+.3f} ppm off"


def test_a_profile_peak_moves_as_a_whole():
    """Every sample of a peak is written on the peak's one calibration, so the
    peak keeps the width the frequency axis gives it.

    The ion here is strong before the calibration step and weak after it, and
    a scan stores nothing below its noise, so the weak scans hold the top of
    the peak and none of its flanks. Weighted sample by sample, the flanks
    would follow the strong scans alone and the top all seven, and the peak
    would be written narrower or wider than it is.
    """
    fading = [1e5, 1e5, 1e5, 2e3, 2e3, 2e3, 2e3]
    scans, freq = _stepped_ftms_scans(ions=((300, fading),), floor=1e3)
    backend = _stepped_backend(scans)
    assert np.count_nonzero(scans[7]["profile"][1]) < np.count_nonzero(
        scans[1]["profile"][1]
    ), "the weak scans are meant to store less of the peak"

    masses, *_ = backend.average_centroids(_STEP_SCANS)
    grid, summed, _ = backend._average_profile_in_frequency(
        [(*scans[n]["profile"], *scans[n]["params"].values()) for n in _STEP_SCANS]
    )

    assert masses.size == 1 and grid.size == freq.size
    on_the_mean = np.sort(_mean_calibration_mz(scans, freq))
    shift = (grid / on_the_mean - 1) * 1e6
    peak = summed > 0
    assert peak.sum() >= 5
    # The peak leans towards the strong scans, 4/7 of the step above the mean
    # were it only in them, and every sample of it leans alike.
    assert 0.9 * _STEP_PPM * 4 / 7 < shift[peak].mean() < _STEP_PPM * 4 / 7
    assert np.ptp(shift[peak]) < 1e-6, "the peak's samples moved against each other"
    assert abs(_apex_offsets_ppm(grid, summed, masses)[0]) < 0.01


def test_a_peak_keeps_its_edge_sample_beside_a_gap():
    """The last sample of a peak before a gap in the grid is written with the
    rest of the peak.

    A raw file drops the samples under a scan's noise, so the grid has gaps,
    and a peak ends where one begins. Its last sample lies on a falling flank,
    lower than the sample before it; when the next peak, beyond the gap,
    happens to begin on a taller sample, it is lower than the sample after it
    too. That makes no valley of it: a valley lies between samples of one run.
    Here the fading ion's run ends lower than the steady ion's run begins, and
    every sample of the fading ion still leans as its top does.
    """
    fading = [1e5, 1e5, 1e5, 2e3, 2e3, 2e3, 2e3]
    # The outer two ions keep the scans' first and last samples away from the
    # two under test.
    ions = ((60, 5e4), (300, fading), (330, 1e5), (1100, 5e4))
    scans, freq = _stepped_ftms_scans(ions=ions, floor=1e3)
    stored = np.zeros(freq.size, dtype=bool)
    for scan in scans.values():
        mz, intensity = scan["profile"]
        stored |= intensity > 0
        scan["profile"] = (mz[intensity > 0], intensity[intensity > 0])
    backend = _stepped_backend(scans)

    grid, _, _ = backend._average_profile_in_frequency(
        [(*scans[n]["profile"], *scans[n]["params"].values()) for n in _STEP_SCANS]
    )

    assert grid.size == stored.sum() < freq.size, "the scans are meant to leave gaps"
    on_the_mean = np.sort(_mean_calibration_mz(scans, freq[stored]))
    shift = (grid / on_the_mean - 1) * 1e6
    centre = _mean_calibration_mz(scans, freq[[300]])[0]
    ion = np.abs(on_the_mean / centre - 1) * 1e6 < 2 * FWHM_PPM
    assert ion.sum() >= 5
    # The tallest sample weighs three strong scans above the mean calibration
    # and four weak ones below it.
    strong, weak = 3 * 1e5, 4 * 2e3
    leans = _STEP_PPM * (strong * 4 / 7 - weak * 3 / 7) / (strong + weak)
    np.testing.assert_allclose(shift[ion], leans, atol=1e-3)


def test_peaks_that_would_cross_share_a_calibration():
    """Two neighbouring peaks whose calibrations lie further apart than the
    samples between them are written on one, and do not swap samples.

    The upper ion is only in the scans after a 6 ppm step and the lower one
    only in those before it, so their calibrations pull them towards each
    other by more than the 2.8 ppm between two samples. Written each on its
    own, the samples between them would change places. Both are written on
    the calibration weighted over the tallest sample of each instead, which
    leans towards the taller peak: the grid still ascends, and the profile is
    the scans' samples in the order they were measured in.
    """
    step = 6.0
    tall, short = 1e5, 2e4
    after, before = [0, 0, 0, tall, tall, tall, tall], [short, short, short, 0, 0, 0, 0]
    scans, freq = _stepped_ftms_scans(ions=((300, after), (308, before)), step_ppm=step)
    backend = _stepped_backend(scans)

    grid, summed, _ = backend._average_profile_in_frequency(
        [(*scans[n]["profile"], *scans[n]["params"].values()) for n in _STEP_SCANS]
    )

    # The premise: on their own calibrations the two would overlap.
    sample_ppm = (freq[301] - freq[300]) / freq[300] * 2e6
    assert step > sample_ppm
    assert np.all(np.diff(grid) > 0), "the grid does not ascend"
    measured = sum(scans[n]["profile"][1] for n in _STEP_SCANS)[::-1]
    np.testing.assert_allclose(summed, measured, rtol=1e-9, atol=1e-6)
    # The mean calibration lies 3/7 of the step above the later scans' and 4/7
    # below the earlier ones'; the tallest samples weigh 4 and 3 scans of each.
    shared = (4 * tall * -step * 3 / 7 + 3 * short * step * 4 / 7) / (
        4 * tall + 3 * short
    )
    shift = (grid / np.sort(_mean_calibration_mz(scans, freq)) - 1) * 1e6
    peaks = summed > 0.5 * 3 * short
    assert peaks.sum() >= 6
    np.testing.assert_allclose(shift[peaks], shared, atol=1e-3)


# -- the baseline between clusters --


def test_no_baseline_zero_goes_between_adjacent_bins():
    """Two peaks in neighbouring bins get no baseline between them, however far
    apart their calibrations write them.

    The ion at the higher m/z is only in the scans before a 12 ppm step, which
    write it higher still, and the other one only in the scans after it. The
    step across the valley between them is written five times as wide as any
    other, and no bin is missing there: every scan stored every sample. A
    baseline zero belongs where the scans stored nothing, which is read off
    the occupied cells of the frequency grid and not off the m/z axis.
    """
    before, after = [1e5, 1e5, 1e5, 0, 0, 0, 0], [0, 0, 0, 1e5, 1e5, 1e5, 1e5]
    scans, freq = _stepped_ftms_scans(ions=((300, before), (308, after)), step_ppm=12.0)

    grid, summed, _ = _stepped_backend(scans).average_profile(_STEP_SCANS)

    # The premise: the valley is written wide enough to pass for a gap.
    steps = np.diff(grid) / grid[:-1]
    assert steps.max() > _ZEROFILL_GAP_FACTOR * np.median(steps)
    assert grid.size == freq.size, "a baseline zero went in between adjacent bins"
    measured = sum(scans[n]["profile"][1] for n in _STEP_SCANS)[::-1]
    np.testing.assert_allclose(summed, measured, rtol=1e-9, atol=1e-6)


def test_a_cluster_ends_at_a_step_over_four_median_steps():
    """A cluster ends where the step to the next occupied cell is more than
    four of the grid's median steps.

    The bins here all lie within a third of a percent of one m/z, so a step
    is the same number of bins wherever it falls: the one bin missing after
    bin 59 makes a step of two and ends nothing, and the five missing after
    bin 119 make a step of six and end a cluster.
    """
    scans, stored = _scans_on_shared_bins(
        spans=([(0, 59), (61, 119), (125, 199)],), heights=(1.0,), shifts=(0.0,)
    )
    backend = OpenTFRawBackend("unused.raw")
    grid, summed, frequency = backend._average_profile_in_frequency(scans)
    _, filled = OpenTFRawBackend._zerofill_baseline(grid, summed, frequency)

    assert grid.size == np.count_nonzero(stored)
    # Ascending m/z is descending bins: bins 199-125 come first, 75 of them.
    np.testing.assert_array_equal(np.flatnonzero(filled == 0), [75, 76])


def test_the_threshold_is_a_relative_step_not_a_count_of_bins():
    """One missing bin ends a cluster high in the mass range and not low in it.

    A bin is the same step in frequency all along the grid, which is a larger
    share of the frequency the higher the m/z. The grid here runs from m/z 50
    to 750 with its median near 126, where four median steps come to two bins
    from about m/z 500 up: the bin missing near m/z 600 ends a cluster, and
    the one missing near m/z 100 does not.
    """
    frequency = np.linspace(np.sqrt(_B / 750.0), np.sqrt(_B / 50.0), 3001)
    missing = [np.abs(_B / frequency**2 - mz).argmin() for mz in (100.0, 600.0)]
    frequency = np.delete(frequency, missing)[::-1]
    grid = _B / frequency**2

    filled_mz, filled = OpenTFRawBackend._zerofill_baseline(
        grid, np.ones(grid.size), frequency
    )

    zeros = filled_mz[filled == 0]
    assert zeros.size == 2
    np.testing.assert_allclose(zeros, 600.0, rtol=0, atol=2.0)


def _gapped_profile(step_ppm: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The averaged profile of scans that drop what lies under their noise, and
    the bins any of them stored.

    The two ions in the middle are in the scans of one side of the calibration
    step each, four empty bins apart, and the step writes them towards each
    other. The outer two keep the scans' first and last samples away.
    """
    after, before = [0, 0, 0, 1e5, 1e5, 1e5, 1e5], [1e5, 1e5, 1e5, 0, 0, 0, 0]
    ions = ((60, 5e4), (300, after), (311, before), (1100, 5e4))
    scans, freq = _stepped_ftms_scans(ions=ions, step_ppm=step_ppm, floor=1e3)
    stored = np.zeros(freq.size, dtype=bool)
    for scan in scans.values():
        mz, intensity = scan["profile"]
        stored |= intensity > 0
        scan["profile"] = (mz[intensity > 0], intensity[intensity > 0])
    grid, summed, _ = _stepped_backend(scans).average_profile(_STEP_SCANS)
    return grid, summed, stored


@pytest.mark.parametrize("step_ppm", [0.0, 6.0])
def test_the_baseline_falls_where_bins_are_missing(step_ppm):
    """A baseline zero goes in on either side of every stretch of bins no scan
    stored, and the scans' calibrations do not move it.

    The gap between the two middle ions is five bins wide, 14 ppm, and a
    6 ppm calibration step between the scans holding one and the other writes
    it as 8 ppm, which is under four of the grid's steps. It is the same four
    empty bins either way, and the profile drops to the baseline in it.
    """
    grid, summed, stored = _gapped_profile(step_ppm)

    # Ascending m/z is descending bins; two zeros follow each cluster but the last.
    bins = np.flatnonzero(stored)[::-1]
    ends_cluster = np.flatnonzero(np.diff(bins) < -1)
    assert np.any(np.diff(bins)[ends_cluster] == -5), "the five-bin gap is the premise"
    expected = np.insert(
        np.zeros(bins.size, dtype=bool), np.repeat(ends_cluster + 1, 2), True
    )
    assert np.all(np.diff(grid) > 0)
    np.testing.assert_array_equal(summed == 0, expected)
    # Each zero sits just outside the sample its cluster ends or begins on.
    left, right = np.flatnonzero(expected)[::2], np.flatnonzero(expected)[1::2]
    np.testing.assert_allclose(
        grid[left] / grid[left - 1] - 1, _ZEROFILL_EDGE_PPM / 1e6, rtol=1e-6
    )
    np.testing.assert_allclose(
        1 - grid[right] / grid[right + 1], _ZEROFILL_EDGE_PPM / 1e6, rtol=1e-6
    )


@pytest.mark.parametrize(
    ("gap_ppm", "zeros_ppm"),
    [(14.5, [2.0, 12.5]), (4.1, [2.0, 2.1]), (3.9, [1.95]), (1.5, [0.75])],
)
def test_baseline_zeros_stay_between_the_clusters(gap_ppm, zeros_ppm):
    """A zero goes 2 ppm outside each of the two samples a gap lies between,
    and a gap too narrow for both takes one zero, at its middle.

    Under 4 ppm the two would pass each other, and under 2 ppm each would land
    among the next cluster's samples. The middle is where the two meet as a
    gap narrows to 4 ppm, so the baseline does not jump on the way: they sit
    2.0 and 2.1 ppm into a gap of 4.1, and the one zero 1.95 into a gap of 3.9.
    """
    inside = 0.3 * np.arange(10.0)
    ppm = np.concatenate([inside, inside[-1] + gap_ppm + inside])
    grid = 300.0 * (1 + ppm / 1e6)
    summed = np.arange(1.0, ppm.size + 1)

    filled_mz, filled = OpenTFRawBackend._zerofill_baseline(grid, summed)

    assert np.all(np.diff(filled_mz) > 0)
    np.testing.assert_array_equal(filled_mz[filled > 0], grid)
    np.testing.assert_array_equal(filled[filled > 0], summed)
    into_gap = (filled_mz[filled == 0] / grid[9] - 1) * 1e6
    np.testing.assert_allclose(into_gap, zeros_ppm, atol=1e-4)


def test_the_fallback_finds_its_clusters_on_the_mz_axis():
    """Without a frequency grid a cluster ends at an m/z gap large against the
    spacing inside the clusters, as the m/z fallback's grid has no bins to
    count.
    """
    ppm = np.concatenate([np.arange(10.0), 30.0 + np.arange(10.0)])
    grid = 300.0 * (1 + ppm / 1e6)
    summed = np.ones(ppm.size)

    filled_mz, filled = OpenTFRawBackend._zerofill_baseline(grid, summed)

    assert np.all(np.diff(filled_mz) > 0)
    zeros = np.flatnonzero(filled == 0)
    np.testing.assert_array_equal(zeros, [10, 11])
    at_ppm = (filled_mz[zeros] / 300.0 - 1) * 1e6
    np.testing.assert_allclose(at_ppm, [11.0, 28.0], atol=1e-4)
