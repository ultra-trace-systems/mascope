"""
Tests: the origin key, which part of the acquisition record a copy came from.

The key is a digest of the part's file, polarity and window
(``api/controllers/sample/lib/origin_key.py``). It is written once per copy
and compared for as long as the copy exists, so what it digests is pinned
here byte for byte: a change to it would give every part a key no copy made
before carries, and a refresh would add the samples a batch already holds.
"""

import re
from types import SimpleNamespace

import pytest

from mascope_backend.api.controllers.sample.lib.origin_key import (
    copy_origin_key,
    origin_key,
)


_PART = ("sf-0000000000001", "-", 0.0, 60.0)


def test_the_key_of_a_part_is_what_it_always_was():
    """The layout, frozen. Never update these to make the test pass."""
    assert origin_key(*_PART) == (
        "c162b64a2cb188ee54f0486c51284d6567f45a0902c9c04e20d2cdd5967f668a"
    )
    assert origin_key("sf-0000000000001", None, None, None) == (
        "ff36315e4bbd1b8c053e640dff94348853c8ae8e497a550cf6dd17fc2b391677"
    )


def test_a_key_fits_its_column():
    assert re.fullmatch(r"[0-9a-f]{64}", origin_key(*_PART))


@pytest.mark.parametrize(
    "part",
    [
        ("sf-0000000000002", "-", 0.0, 60.0),
        ("sf-0000000000001", "+", 0.0, 60.0),
        ("sf-0000000000001", "-", 0.5, 60.0),
        ("sf-0000000000001", "-", 0.0, 59.999),
        ("sf-0000000000001", "-", 60.0, 0.0),
        ("sf-0000000000001", "-", None, 60.0),
    ],
)
def test_each_part_of_the_natural_key_tells_two_parts_apart(part):
    assert origin_key(*part) != origin_key(*_PART)


@pytest.mark.parametrize(
    "window", [(0, 60), (-0.0, 60.0), (0.0, 60)], ids=["ints", "negative zero", "mixed"]
)
def test_one_window_written_two_ways_is_one_part(window):
    assert origin_key("sf-0000000000001", "-", *window) == origin_key(*_PART)


def _sample(**fields):
    """A sample as the view gives it."""
    return SimpleNamespace(
        **{
            "sample_file_id": "sf-0000000000001",
            "polarity": "-",
            "t0": 0.0,
            "t1": 60.0,
            "origin_key": None,
            # Neither of these is part of the key: what a copy is, not how it
            # was read or where it sits
            "ionization_mode_id": "im-0000000000001",
            "stream_id": "st-0000000000001",
        }
        | fields
    )


def test_a_copy_of_an_acquisition_sample_takes_the_key_of_its_part():
    assert copy_origin_key(_sample()) == origin_key(*_PART)


def test_the_chemistry_and_the_stream_an_item_reads_are_not_its_part():
    """The note's decision 10, and the stream named by its polarity: a file
    re-processed under another mode or under stream rows is the same part."""
    assert copy_origin_key(
        _sample(ionization_mode_id="im-0000000000002", stream_id=None)
    ) == copy_origin_key(_sample())


def test_a_copy_of_a_copy_takes_its_sources_key_whatever_its_window_is_now():
    edited = _sample(origin_key="k" * 64, t0=10.0, t1=20.0)

    assert copy_origin_key(edited) == "k" * 64
