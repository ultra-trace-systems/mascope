"""What a scan stream's instrument function is stored by, whose a stream too
thin to fit takes, and how a stitched polarity's widths are read
(``mascope_signal.instrument_func``)."""

from functools import partial

import numpy as np
import pytest

import mascope_signal.instrument_func.streams as m_stream_functions
from mascope_signal.instrument_func.fit import (
    r_orbi,
    r_tof,
    resolution_coefficients,
    resolution_function_of,
)


@pytest.mark.parametrize("coefficients", [[1.7e6], [1e-5, 0.3]])
def test_a_resolution_function_round_trips_through_its_coefficients(coefficients):
    function = resolution_function_of(coefficients)

    assert resolution_coefficients(function) == coefficients


def test_the_functions_a_fit_returns_are_the_ones_stored():
    assert resolution_coefficients(partial(r_orbi, a=2.0)) == [2.0]
    assert resolution_coefficients(partial(r_tof, a=1.0, b=3.0)) == [1.0, 3.0]
    # Anything else is no stored function: a test's stand-in, say
    assert resolution_coefficients(lambda mz: mz) is None


def test_an_older_three_coefficient_function_still_reads():
    function = resolution_function_of([0.0, 0.0, 5.0])

    assert function(100.0) == 5.0
    with pytest.raises(ValueError):
        resolution_function_of([1.0, 2.0, 3.0, 4.0])


def test_an_orbitraps_resolution_is_stated_at_mz_200():
    assert m_stream_functions.resolution_at_reference(
        [120_000 * 200**0.5]
    ) == pytest.approx(120_000)
    assert m_stream_functions.resolution_at_reference([1e-5, 0.3]) is None
    assert m_stream_functions.resolution_at_reference(None) is None


def _stream(polarity, lower, upper, resolution=120_000, analyzer="FTMS"):
    return {
        "key": f"{polarity} {lower}-{upper} R={resolution}",
        "signature": {
            "analyzer": analyzer,
            "polarity": polarity,
            "scan_ranges": [[lower, upper]],
            "resolution": resolution,
        },
    }


def test_a_thin_stream_takes_the_nearest_fitted_stream_of_its_polarity():
    streams = [
        _stream("-", 40, 138),
        _stream("-", 132, 460),
        _stream("+", 440, 900),
        _stream("-", 440, 900),
    ]

    # The positive range is the same window, but of the other polarity
    assert m_stream_functions._sibling(streams, 3, {0, 1, 2}) == 1


def test_only_a_stream_of_its_analyzer_and_resolution_is_a_sibling():
    streams = [
        _stream("-", 40, 138, resolution=60_000),
        _stream("-", 132, 460, analyzer="ITMS"),
        _stream("-", 440, 900),
    ]

    assert m_stream_functions._sibling(streams, 2, {0, 1}) is None


def test_another_polarity_is_a_sibling_where_its_own_has_none():
    streams = [_stream("+", 40, 138), _stream("-", 132, 460)]

    assert m_stream_functions._sibling(streams, 1, {0}) == 0


def test_each_mz_is_read_by_the_stream_that_owns_it():
    runs = [[40, 67, 0], [67, 122, 1], [122, 900, 2]]
    functions = {
        index: (lambda mz, r=r: np.full_like(mz, r))
        for index, r in enumerate([1.0, 2.0, 3.0])
    }
    resolution = m_stream_functions.SegmentResolution(
        runs, functions, lambda mz: np.full_like(mz, -1.0)
    )

    np.testing.assert_array_equal(
        resolution(np.array([50.0, 67.0, 121.9, 500.0, 950.0, 10.0])),
        [1.0, 2.0, 2.0, 3.0, -1.0, -1.0],
    )
    # A scalar in, a scalar out, as the fitting code asks one m/z at a time
    assert resolution(100.0) == 2.0


def test_a_stream_with_no_function_of_its_own_is_read_by_the_fallback():
    resolution = m_stream_functions.SegmentResolution(
        [[40, 67, 0], [67, 122, 1]],
        {0: lambda mz: np.full_like(mz, 1.0)},
        lambda mz: np.full_like(mz, 9.0),
    )

    np.testing.assert_array_equal(resolution(np.array([50.0, 100.0])), [1.0, 9.0])
