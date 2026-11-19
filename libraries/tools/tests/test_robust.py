"""The shared robust spread (``mascope_tools.composition.robust``)."""

import numpy as np
import pytest

from mascope_tools.composition.robust import MAD_TO_SIGMA, robust_spread


def test_the_spread_is_the_scaled_mad_about_the_median():
    # Median 2.5, absolute deviations 1.5, 0.5, 0.5, 97.5: their median is 1,
    # however far out the stray reading sits.
    assert robust_spread([1.0, 2.0, 3.0, 100.0]) == pytest.approx(MAD_TO_SIGMA)
    assert robust_spread([1.0, 2.0, 3.0]) == pytest.approx(MAD_TO_SIGMA)


def test_a_given_centre_replaces_the_median():
    assert robust_spread([1.0, 2.0, 3.0], centre=0.0) == pytest.approx(
        2.0 * MAD_TO_SIGMA
    )


def test_it_estimates_a_normal_standard_deviation():
    rng = np.random.default_rng(7)
    assert robust_spread(rng.normal(0.0, 2.0, 200_000)) == pytest.approx(2.0, rel=0.01)


def test_identical_readings_have_no_spread():
    assert robust_spread(np.full(5, 0.3)) == 0.0
