"""One MS2 group's isolation window, drawn over the MS1 centroids.

``compute.ms2_isolation_window`` is peaky's window table
(``peaky.ms2.sources.window_table``, its PR #79), one group at a time: every
centroid within +/-(width / 2 + 5 mDa) of the key, whatever its S/N, with
its offset, its share of the window and the precursor marked. Its known
answers at m/z 100 are peaky's own.
"""

import numpy as np
import pytest

from mascope_signal.compute import MS2_WINDOW_PAD, ms2_isolation_window


def _centroids(mz, intensity):
    mz = np.asarray(mz, dtype=np.float64)
    return (
        mz,
        np.asarray(intensity, dtype=np.float64),
        np.full(mz.shape, 1e5),
        np.full(mz.shape, 5.0),
    )


#: A 0.4 Da window at 100 reaches 0.2049 Da on either side, not 0.2051.
EDGE = _centroids(
    [99.7949, 99.7951, 99.85, 100.0, 100.2049, 100.2051],
    [1.0, 1.0, 1.0, 5.0, 1.0, 1.0],
)


def test_reaches_half_the_width_and_the_pad():
    window = ms2_isolation_window(EDGE, 100.0, 0.4)

    assert MS2_WINDOW_PAD == 0.005
    assert window["mz"] == [99.7951, 99.85, 100.0, 100.2049]
    assert window["offset"] == pytest.approx([-0.2049, -0.15, 0.0, 0.2049])
    assert window["share"] == pytest.approx([1 / 8, 1 / 8, 5 / 8, 1 / 8])
    assert window["is_parent"] == [False, False, True, False]
    assert window["intensity"] == [1.0, 1.0, 5.0, 1.0]
    assert window["resolution"] == [1e5] * 4
    assert window["signal_to_noise"] == [5.0] * 4


def test_the_parent_is_the_precursor_rules_not_the_strongest():
    """A co-isolate 2.2 mDa above the key outshines the precursor at it: the
    nearest of the strong enough is the parent, and both are listed."""
    window = ms2_isolation_window(
        _centroids([137.0572, 137.0594], [13.0, 19.0]), 137.0572, 0.4
    )

    assert window["is_parent"] == [True, False]
    assert window["share"] == pytest.approx([13 / 32, 19 / 32])


def test_lists_in_mz_order_whatever_the_centroids_order():
    window = ms2_isolation_window(
        _centroids([100.1, 99.9, 100.0], [1.0, 1.0, 2.0]), 100.0, 0.4
    )

    assert window["mz"] == [99.9, 100.0, 100.1]
    assert window["is_parent"] == [False, True, False]


def test_with_no_width_lists_the_target_alone():
    window = ms2_isolation_window(EDGE, 100.0, None)

    assert window["mz"] == [100.0]
    assert window["share"] == [None]
    assert window["is_parent"] == [True]


def test_with_no_width_and_no_target_lists_nothing():
    window = ms2_isolation_window(_centroids([99.9, 100.1], [1.0, 1.0]), 100.0, None)

    assert window == {
        name: []
        for name in (
            "mz",
            "offset",
            "intensity",
            "share",
            "resolution",
            "signal_to_noise",
            "is_parent",
        )
    }


def test_an_empty_window_and_one_of_no_intensity():
    assert ms2_isolation_window(_centroids([], []), 100.0, 0.4)["mz"] == []
    nothing = ms2_isolation_window(_centroids([100.0], [0.0]), 100.0, 0.4)
    assert nothing["mz"] == [100.0] and nothing["share"] == [None]
