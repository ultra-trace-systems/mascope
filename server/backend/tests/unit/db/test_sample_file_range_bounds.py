"""
Tests: a sample file's m/z bounds, the two ends of its range as numbers.

``sample_file.mz_min`` and ``mz_max`` are what the finder compares and
indexes, and they are only true while they say what ``range`` says. They are
set from it whenever it is assigned (``SampleFile._range_bounds``), by the
rule the migration that made them filled every row by (``range_bounds``):
the first and last element, as the batch export reads a range.

Hermetic: an ORM instance needs no session for its attributes to be set.
"""

import math

import numpy as np
import pytest

from mascope_backend.db import SampleFile
from mascope_backend.db.models import range_bounds


@pytest.mark.parametrize(
    "mz_range, bounds",
    [
        ([50.0, 750.0], (50.0, 750.0)),
        # As the converter of the day, an older one, or a fixture writes them
        ([50, 750], (50.0, 750.0)),
        ((50.0, 750.0), (50.0, 750.0)),
        ([np.float64(50.5), np.float32(750.25)], (50.5, 750.25)),
        # A whole mass axis, which the TofWerk range bug stored: its ends
        ([20.0, 20.5, 21.0, 1200.0], (20.0, 1200.0)),
    ],
)
def test_a_range_gives_its_two_ends(mz_range, bounds):
    low, high = range_bounds(mz_range)

    assert (low, high) == bounds
    assert type(low) is float and type(high) is float


@pytest.mark.parametrize(
    "mz_range",
    [
        None,
        [],
        [50.0],
        {"min": 0, "max": 500},
        "[50, 750]",
        ["50", 750.0],
        [50.0, None],
        [True, 750.0],
        [math.nan, 750.0],
        [50.0, math.inf],
    ],
)
def test_a_range_that_holds_no_two_numbers_gives_none(mz_range):
    assert range_bounds(mz_range) == (None, None)


def _file(**fields) -> SampleFile:
    return SampleFile(
        sample_file_id="sf-bounds",
        filename="bounds.raw",
        instrument="instrument-a",
        **fields,
    )


def test_a_file_made_with_a_range_carries_its_bounds():
    """Registration builds the row from the converter's fields this way."""
    sample_file = _file(range=[40.0, 600.0])

    assert (sample_file.mz_min, sample_file.mz_max) == (40.0, 600.0)


def test_a_new_range_moves_the_bounds_with_it():
    """What the m/z calibration and a file's update do: set the range again."""
    sample_file = _file(range=[40.0, 600.0])

    sample_file.range = [40.012, 599.987]
    assert (sample_file.mz_min, sample_file.mz_max) == (40.012, 599.987)

    sample_file.range = []
    assert (sample_file.mz_min, sample_file.mz_max) == (None, None)


def test_the_range_itself_is_stored_as_given():
    mz_range = [np.float64(40.0), np.float64(600.0)]

    assert _file(range=mz_range).range is mz_range
