"""Tests: default match parameters follow the recorded instrument class.

The class used to be parsed from the instrument name. It is now recorded by
the converter, and a row that carries it must be believed over the name -
otherwise an instrument called "Test" has no match parameters at all.
"""

import pandas as pd
import pytest

from mascope_backend.api.new.match.params import lib
from mascope_match.params import OrbiMatchParams, TofMatchParams


def test_a_recorded_type_settles_the_parameters_whatever_the_name_says():
    assert isinstance(
        lib.instrument_default_match_params("Test", instrument_type="orbi"),
        OrbiMatchParams,
    )
    assert isinstance(
        lib.instrument_default_match_params("Test", instrument_type="tof"),
        TofMatchParams,
    )


def test_without_a_recorded_type_the_name_has_to_say():
    assert isinstance(lib.instrument_default_match_params("Orbi-Lab2"), OrbiMatchParams)
    assert isinstance(lib.instrument_default_match_params("tof3"), TofMatchParams)
    with pytest.raises(ValueError, match="Failed to get instrument type"):
        lib.instrument_default_match_params("Test")


def test_an_unknown_recorded_type_is_refused():
    with pytest.raises(ValueError, match="Unknown instrument type"):
        lib.instrument_default_match_params("Test", instrument_type="quadrupole")


def test_a_frame_with_a_type_column_settles_every_instrument_in_it():
    df = pd.DataFrame(
        {
            "instrument": ["Test", "Test", "Orbi-Lab2"],
            "instrument_type": ["tof", "tof", "orbi"],
        }
    )
    assert lib._instrument_types_in(df) == {"Test": "tof", "Orbi-Lab2": "orbi"}


def test_a_frame_with_file_names_reads_the_type_off_them(monkeypatch):
    # Files converted under a name that does not say record their class in
    # the filestore; the frame's filenames reach it through get_instrument_type.
    monkeypatch.setattr(
        lib,
        "get_instrument_type",
        lambda filename: "tof" if "Test" in filename else "orbi",
    )
    df = pd.DataFrame(
        {
            "instrument": ["Test", "Orbi-Lab2"],
            "filename": [
                "Test_2026.09.05-10h12m01s_a",
                "Orbi-Lab2_2026.09.05-10h12m01s_b",
            ],
        }
    )
    assert lib._instrument_types_in(df) == {"Test": "tof", "Orbi-Lab2": "orbi"}


def test_a_frame_with_neither_leaves_it_to_the_name():
    df = pd.DataFrame({"instrument": ["Orbi-Lab2"]})
    assert lib._instrument_types_in(df) == {}
