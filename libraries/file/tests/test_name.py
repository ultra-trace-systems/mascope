"""Tests for the instrument name and class rules in ``mascope_file.name``.

The instrument name is the first segment of a stored file name. The class
("orbi" or "tof") used to be parsed from that name, which forced every
instrument to be called something containing "orbi" or "tof"; it is now
recorded by the reader that converts the file, in the file's props, and the
name rule survives only as the fallback for files converted before that.
"""

import os

import pytest

from mascope_file import name as m_name
from mascope_file.io import write_props


def _converted(filename: str, **props) -> None:
    """Lay down a converted file's props under the temporary filestore."""
    os.makedirs(m_name.parse_path_from_item_filename(filename), exist_ok=True)
    write_props(filename, props)


@pytest.mark.parametrize("instrument", ["Test", "Orbi-Lab2", "tof3", "a" * 64])
def test_validate_instrument_name_accepts_any_well_formed_name(instrument):
    # The name no longer has to say which class it is.
    m_name.validate_instrument_name(instrument)


@pytest.mark.parametrize("instrument", ["", "orbi lab", "orbi_lab", "a" * 65, "x.raw"])
def test_validate_instrument_name_rejects_what_the_separator_rule_forbids(instrument):
    with pytest.raises(ValueError, match="Invalid instrument name"):
        m_name.validate_instrument_name(instrument)


def test_a_name_that_says_decides_the_type_without_touching_the_filestore():
    # No props exist for these; the name alone answers, as it always did.
    assert m_name.get_instrument_type("Orbi-Lab2_2026.09.05-10h12m01s_x") == "orbi"
    assert m_name.get_instrument_type("tof3_2026.09.05-10h12m01s_x") == "tof"


def test_a_name_that_does_not_say_takes_the_type_the_reader_recorded():
    filename = "Test_2026.09.05-10h12m01s_ambient"
    _converted(filename, instrument_type="tof", polarity="-")
    assert m_name.get_instrument_type(filename) == "tof"


def test_a_name_that_does_not_say_and_no_recorded_type_is_an_error():
    # A file from before the field whose name does not say cannot exist -
    # such a name was refused at upload - but a props file without the field
    # must still fail loudly rather than guess a class.
    filename = "Test_2026.09.05-11h12m01s_ambient"
    _converted(filename, polarity="-")
    with pytest.raises(
        ValueError, match="Failed to get instrument type for instrument Test"
    ):
        m_name.get_instrument_type(filename)


def test_a_missing_file_is_an_error_too():
    with pytest.raises(
        ValueError, match="Failed to get instrument type for instrument Test"
    ):
        m_name.get_instrument_type("Test_2026.09.05-12h12m01s_never-converted")


def test_the_reader_decides_the_class_by_what_it_reads():
    assert m_name.INSTRUMENT_TYPE_BY_EXTENSION == {".raw": "orbi", ".h5": "tof"}
