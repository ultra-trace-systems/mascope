"""The rule that leaves a raw file's first scan out (``mascope_thermo.first_scan``).

The cases are shaped on what one customer instrument's acquisitions hold:
eight to sixteen scans that agree to a percent or two, and a first scan that
reads anywhere from a few percent to 25 times above them. How the two reader
backends apply
the rule to a file is in ``test_stream_selection.py``.
"""

import pytest

from mascope_thermo.first_scan import (
    FACTOR,
    MIN_EXCESS,
    is_outlier,
)


def _flat(first, n=7, level=1.0e6, wobble=(1.0, 0.99, 1.01, 1.0, 1.0, 0.99, 1.01)):
    """A first scan, then ``n`` scans of one experiment within a percent of
    ``level``."""
    rest = [level * wobble[i % len(wobble)] for i in range(n)]
    return [first * level, *rest]


def _alike(tic):
    return [True] * len(tic)


# -- the long-standing test -------------------------------------------------------


@pytest.mark.parametrize("ratio", [5.0, 9.4, 25.0])
def test_a_first_scan_five_times_the_median_or_more_is_left_out(ratio):
    tic = _flat(ratio)
    assert is_outlier(tic, _alike(tic))


def test_the_first_test_holds_whatever_the_others_spread():
    """The rule as it always was: a scan it left out stays left out, so no
    store built without its first scan is made stale by the second test."""
    tic = [5.0, 1.0, 0.2, 1.8, 0.4, 1.6]
    assert is_outlier(tic, [True, False, False, False, False, False])


# -- the tail below five ----------------------------------------------------------


@pytest.mark.parametrize("ratio", [4.98, 3.0, 1.9, 1.0 + MIN_EXCESS])
def test_a_first_scan_far_above_an_experiment_that_agrees_is_left_out(ratio):
    """The spike the old bound kept: every ion of the first scan, and so its
    TIC, a fixed factor above scans that agree to a percent."""
    tic = _flat(ratio)
    assert is_outlier(tic, _alike(tic))


@pytest.mark.parametrize("ratio", [1.05, 1.2, 1.4])
def test_a_first_scan_under_the_floor_is_kept(ratio):
    """Ten standard deviations of scans that agree to a percent are a few
    percent. Some methods open every file on a first scan 5 to 40% high,
    and a bound inside that spread would keep the first scan of one file
    and leave out that of the next."""
    tic = _flat(ratio)
    assert not is_outlier(tic, _alike(tic))


# Scans that move by 10 to 12% from one to the next: their robust relative
# spread is 1.4826 times a median deviation of 0.1, about 0.15, so ten of it
# puts the bar near 2.5 times their median, well above the floor.
_MOVING = [1.0, 0.9, 1.1, 0.88, 1.12, 0.9, 1.1]


def test_a_signal_that_moves_raises_the_bar_above_the_floor():
    """Twice the median of scans that move is one of them; twice the median
    of scans that agree to a percent is the quirk. The spread alone tells
    the two apart: both stand above the floor."""
    moving = [2.0, *_MOVING]
    steady = _flat(2.0)

    assert not is_outlier(moving, _alike(moving))
    assert is_outlier(steady, _alike(steady))


def test_a_first_scan_far_above_a_moving_signal_is_still_left_out():
    """The bar rises with the spread, by ten of it and no more: three times
    the median of scans that move by a tenth stands out of them."""
    tic = [3.0, *_MOVING]
    assert is_outlier(tic, _alike(tic))


def test_a_signal_that_starts_on_a_plateau_keeps_its_first_scan():
    """The quirk is one scan. Five scans at 1.47 and then ten at 1.0 is a
    signal that came down: the first is no higher than the next."""
    tic = [1.47, 1.46, 1.45, 1.47, 1.48, 1.18] + [0.97, 1.0, 1.01, 1.0] * 3
    assert not is_outlier(tic, _alike(tic))


def test_a_decay_over_the_first_scans_keeps_the_first():
    tic = [1.38, 1.22, 1.17, 1.06, 1.0, 0.98, 0.99, 0.94]
    assert not is_outlier(tic, _alike(tic))


def test_the_next_scan_a_little_high_does_not_hide_the_spike():
    """The scan after a strong spike can carry a little of it."""
    tic = [3.5, 1.33, 1.05, 1.01, 1.0, 0.97, 0.97, 0.97]
    assert is_outlier(tic, _alike(tic))


def test_a_rare_experiment_is_not_judged_against_another():
    """A method that runs one experiment rarely among another of its
    polarity: the first scan is ordinary among its own scans, and well above
    the other experiment's, which agree with each other."""
    tic = [1.4, 1.0, 1.0, 1.01, 0.99, 1.4, 1.0, 1.0, 1.01, 0.99, 1.41]
    alike = [True, False, False, False, False, True, False, False, False, False, True]
    assert not is_outlier(tic, alike)
    # Its own experiment's scans are what it is judged by
    tic[0] = 3.0
    assert is_outlier(tic, alike)


# -- nothing to judge by ----------------------------------------------------------


@pytest.mark.parametrize("tic", [[], [9e9]], ids=["no-scans", "one-scan"])
def test_a_scan_with_nothing_to_compare_with_is_kept(tic):
    assert not is_outlier(tic, _alike(tic))


def test_one_other_scan_of_its_experiment_is_judged_by_the_first_test_alone():
    """With one, which of the two is the odd one cannot be told."""
    assert not is_outlier([4.0, 1.0], [True, True])
    assert is_outlier([FACTOR, 1.0], [True, True])


def test_an_experiment_with_no_current_judges_nothing():
    """Its own experiment's other scans recorded nothing, so there is no
    level to stand above; against every scan it is ordinary."""
    tic = [1.0, 2.0, 0.0, 0.0, 2.0]
    assert not is_outlier(tic, [True, False, True, True, False])


def test_a_flag_per_scan_is_required():
    with pytest.raises(ValueError):
        is_outlier([3.0, 1.0, 1.0, 1.0], [True, True])
