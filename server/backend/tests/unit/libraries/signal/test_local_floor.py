"""The local floor an Orbitrap peak is measured against where its spectrum is
dense.

Averaged over many microscans, the stretch around a dominant ion holds a floor
of maxima, a centroid every few ppm over several Da, and the instrument reads
each far above its own noise. The weak flag also measures a peak against the
peaks around it (``below_local_floor``). These pin that on synthetic peak
lists, and through the detector on a scripted acquisition.
"""

import numpy as np
import pytest
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.peak as m_peak
from mascope_signal.peak import (
    LOCAL_FLOOR_FACTOR,
    LOCAL_FLOOR_MIN_NEIGHBOURS,
    LOCAL_FLOOR_WINDOW_PPM,
    below_local_floor,
)


ION = 62.0
FLOOR_STEP_PPM = 3.0
FLOOR_HEIGHT = 100.0


def _floor(centre=ION, half_width_ppm=1500.0, step_ppm=FLOOR_STEP_PPM):
    """Maxima of one height every ``step_ppm`` around ``centre``, the centre
    itself left out."""
    steps = np.arange(step_ppm, half_width_ppm + step_ppm / 2, step_ppm)
    offsets = np.concatenate([-steps[::-1], steps])
    return centre * (1 + offsets * 1e-6)


def test_the_maxima_of_a_floor_are_under_it_and_an_ion_standing_out_is_not():
    floor = _floor()
    mz = np.append(floor, ION)
    heights = np.append(np.full(floor.size, FLOOR_HEIGHT), 10 * FLOOR_HEIGHT)

    below = below_local_floor(mz, heights)

    assert below[:-1].all()
    assert not below[-1]


def test_a_peak_standing_the_factor_over_the_floor_is_kept():
    floor = _floor()
    mz = np.append(floor, [ION, ION * (1 + 1e-6)])
    heights = np.append(
        np.full(floor.size, FLOOR_HEIGHT),
        [LOCAL_FLOOR_FACTOR * FLOOR_HEIGHT, 0.99 * LOCAL_FLOOR_FACTOR * FLOOR_HEIGHT],
    )

    below = below_local_floor(mz, heights)

    assert not below[-2]
    assert below[-1]


def test_resolved_ions_are_left_to_their_signal_to_noise():
    """However weak beside its neighbours, a peak of a stretch too sparse to
    be a floor is not judged by it."""
    step = 2 * LOCAL_FLOOR_WINDOW_PPM / LOCAL_FLOOR_MIN_NEIGHBOURS * 1.1
    mz = _floor(half_width_ppm=2 * LOCAL_FLOOR_WINDOW_PPM, step_ppm=step)
    heights = np.where(np.arange(mz.size) % 2, 1e6, 1.0)

    assert not below_local_floor(mz, heights).any()


def test_a_floor_needs_the_neighbours_within_the_window():
    """The peaks at the edge of a stretch just dense enough are judged as
    long as their own window holds the neighbours, and no further."""
    count = LOCAL_FLOOR_MIN_NEIGHBOURS
    step = LOCAL_FLOOR_WINDOW_PPM / count
    mz = ION * (1 + np.arange(count + 1) * step * 0.999e-6)

    below = below_local_floor(mz, np.full(mz.size, FLOOR_HEIGHT))

    assert below[0] and below[-1]
    assert not below_local_floor(mz[1:], np.full(mz.size - 1, FLOOR_HEIGHT)).any()


def test_each_spectrum_is_measured_against_its_own_peaks():
    """Two spectra on one axis, each too sparse alone, are no floor together;
    and one spectrum's floor is none of a peak of another at the same m/z."""
    half = LOCAL_FLOOR_MIN_NEIGHBOURS // 2 + 1
    step = 2 * LOCAL_FLOOR_WINDOW_PPM / (2 * half) * 0.9
    mz = ION * (1 + (np.arange(2 * half) - half) * step * 1e-6)
    heights = np.full(mz.size, FLOOR_HEIGHT)
    interleaved = np.where(np.arange(mz.size) % 2, "+", "-")

    assert below_local_floor(mz, heights).any()
    assert not below_local_floor(mz, heights, interleaved).any()

    floor = _floor()
    mz = np.append(floor, ION)
    heights = np.full(mz.size, FLOOR_HEIGHT)
    polarity = np.append(np.full(floor.size, "-"), "+")

    below = below_local_floor(mz, heights, polarity)

    assert below[:-1].all()
    assert not below[-1]


def test_the_order_of_the_list_does_not_matter():
    """A floor of uneven heights with an ion standing out of it, and low peaks
    of a sparse stretch out of the floor's reach, handed over shuffled and in
    falling m/z: each peak is still measured against its own window. The
    floor is twice as wide as a window, so a window taken from the wrong
    peaks cuts it anywhere; falling m/z is the order a list left unsorted
    gets those windows from without fail."""
    rng = np.random.default_rng(7)
    floor = _floor(half_width_ppm=2 * LOCAL_FLOOR_WINDOW_PPM)
    # One every 1000 ppm or so, beyond any window of the floor's peaks
    sparse = np.arange(70.0, 80.0, 0.07)
    mz = np.concatenate([floor, [ION], sparse])
    heights = np.concatenate(
        [
            rng.uniform(0.5, 1.5, floor.size) * FLOOR_HEIGHT,
            [10 * FLOOR_HEIGHT],
            np.full(sparse.size, FLOOR_HEIGHT / 100),
        ]
    )
    expected = np.concatenate(
        [np.ones(floor.size, bool), [False], np.zeros(sparse.size, bool)]
    )
    for order in (rng.permutation(mz.size), np.argsort(-mz)):
        assert np.array_equal(
            below_local_floor(mz[order], heights[order]), expected[order]
        )


def test_an_empty_list_has_nothing_under_a_floor():
    assert below_local_floor(np.array([]), np.array([])).size == 0


# -- through the detector ------------------------------------------------------------

NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
POS = "FTMS + p NSI Full ms [40.0000-600.0000]"


#: An ion standing ten times out of the floor, between two of its maxima
STANDING = ION * (1 + 451.5e-6)
LONE = 188.0
POSITIVE = ION * (1 + 1e-6)
OTHER_STREAM = ION * (1 + 1e-6)


def _store():
    """The whole peak store, weak and satellite peaks included."""
    return m_io.load_peak_data(
        SAMPLE_FILENAME, drop_bad_peaks=False, composite=False
    ).compute()


@pytest.fixture
def floored(acquire, instrument_functions):
    """The store of a negative spectrum whose reagent ion at 62 sits in a
    floor, with an ion standing out of it and a lone peak of the floor's
    height elsewhere, and of a positive spectrum holding one peak of the
    floor's height inside the negative floor's stretch. The scripted reader
    reads every centroid at a signal-to-noise of 100."""
    negative = {float(mz): FLOOR_HEIGHT for mz in _floor()}
    negative[ION] = 1e6
    negative[STANDING] = 10 * FLOOR_HEIGHT
    negative[LONE] = FLOOR_HEIGHT
    acquire([(NEG, 1, negative), (POS, 1, {POSITIVE: FLOOR_HEIGHT})])
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    return _store()


@pytest.fixture
def streamed(acquire, instrument_functions):
    """The store of a file detected per stream, whose method runs two
    negative experiments under one filter, told apart by their microscans.
    The first holds the reagent ion at 62 in its floor; the second, one peak
    of the floor's height inside the floor's stretch."""
    floor = {float(mz): FLOOR_HEIGHT for mz in _floor()}
    floor[ION] = 1e6
    acquire(
        [(NEG, 1, floor), (NEG, 2, {OTHER_STREAM: FLOOR_HEIGHT})],
        microscans={1: 1, 2: 10},
    )
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    return _store()


def _row(store, polarity, mz):
    """The index of the store's row of one polarity at an m/z."""
    (rows,) = np.nonzero(
        (store.polarity.values == polarity) & (np.abs(store.mz.values - mz) / mz < 1e-8)
    )
    assert rows.size == 1
    return int(rows[0])


def test_the_detector_flags_the_floor_weak_and_keeps_what_stands_out(floored):
    weak = floored.is_weak.values.astype(bool)
    kept = [_row(floored, "-", mz) for mz in (ION, STANDING, LONE)]
    floor = np.setdiff1d(np.flatnonzero(floored.polarity.values == "-"), kept)

    assert not weak[kept].any()
    assert floor.size == _floor().size
    assert weak[floor].all()


def test_the_detector_measures_each_polarity_against_its_own_peaks(floored):
    assert not floored.is_weak.values.astype(bool)[_row(floored, "+", POSITIVE)]


def test_the_detector_measures_each_stream_against_its_own_peaks(streamed):
    """Detected per stream, a peak is measured against its stream's peaks,
    not its polarity's: one stream's floor is none of a peak of another
    stream at the same m/z."""
    weak = streamed.is_weak.values.astype(bool)
    stream = streamed.stream.values
    other = _row(streamed, "-", OTHER_STREAM)
    floor = np.setdiff1d(
        np.flatnonzero(stream != stream[other]), [_row(streamed, "-", ION)]
    )

    assert np.unique(stream).size == 2
    assert not weak[other]
    assert floor.size == _floor().size
    assert weak[floor].all()
