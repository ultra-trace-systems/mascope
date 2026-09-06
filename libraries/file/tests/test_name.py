"""Tests for the instrument name and class rules in ``mascope_file.name``.

The instrument name is the first segment of a stored file name. The class
("orbi" or "tof") used to be parsed from that name, which forced every
instrument to be called something containing "orbi" or "tof"; it is now
decided by the file itself: a ``.raw`` is an Orbitrap acquisition and a
``.h5`` a TOF one. The props record what the reader read, for a sample that
keeps no source file, and the name rule survives only as the last fallback,
for files converted before that field existed.
"""

import os
import pathlib

import pytest

from mascope_file import name as m_name
from mascope_file.io import write_props


@pytest.fixture(autouse=True)
def _forget_recorded_types():
    """The props cache is module state; no test may inherit another's."""
    m_name._INSTRUMENT_TYPE.clear()
    yield
    m_name._INSTRUMENT_TYPE.clear()


def _converted(filename: str, data: str | None = None, **props) -> None:
    """Lay down a converted sample under the temporary filestore.

    ``data`` is the source file's extension, when the sample keeps one.
    """
    path = m_name.parse_path_from_item_filename(filename)
    os.makedirs(path, exist_ok=True)
    write_props(filename, props)
    if data is not None:
        pathlib.Path(path, f"data{data}").write_bytes(b"")


@pytest.mark.parametrize("instrument", ["Test", "Orbi-Lab2", "tof3", "a" * 64])
def test_validate_instrument_name_accepts_any_well_formed_name(instrument):
    # The name no longer has to say which class it is.
    m_name.validate_instrument_name(instrument)


@pytest.mark.parametrize("instrument", ["", "orbi lab", "orbi_lab", "a" * 65, "x.raw"])
def test_validate_instrument_name_rejects_what_the_separator_rule_forbids(instrument):
    with pytest.raises(ValueError, match="Invalid instrument name"):
        m_name.validate_instrument_name(instrument)


def test_a_file_from_before_the_field_falls_back_to_its_name():
    # No props exist for these; the name alone answers, as it always did.
    assert m_name.get_instrument_type("Orbi-Lab2_2026.09.05-10h12m01s_x") == "orbi"
    assert m_name.get_instrument_type("tof3_2026.09.05-10h12m01s_x") == "tof"


def test_a_name_that_does_not_say_takes_the_type_the_reader_recorded():
    filename = "Test_2026.09.05-10h12m01s_ambient"
    _converted(filename, instrument_type="tof", polarity="-")
    assert m_name.get_instrument_type(filename) == "tof"


@pytest.mark.parametrize("instrument", ["Rapid", "Capillary", "Napier"])
def test_the_recorded_type_beats_a_name_that_only_looks_like_it_says(instrument):
    # An instrument may be called anything now, and plenty of ordinary words
    # carry "api" - so the reader's answer has to outrank the name's.
    assert m_name.resolve_instrument_type(instrument, throw=False) == "tof"
    filename = f"{instrument}_2026.09.05-13h12m01s_ambient"
    _converted(filename, instrument_type="orbi", polarity="+")
    assert m_name.get_instrument_type(filename) == "orbi"


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


def test_a_deleted_file_takes_its_remembered_class_with_it():
    # The name is free again once the file is gone, so a later file taking it
    # must not be read as the old one's class.
    filename = "Rapid_2026.09.05-14h12m01s_ambient"
    _converted(filename, instrument_type="orbi", polarity="+")
    assert m_name.get_instrument_type(filename) == "orbi"

    m_name.forget_instrument_type(filename)
    _converted(filename, instrument_type="tof", polarity="+")
    assert m_name.get_instrument_type(filename) == "tof"


@pytest.mark.parametrize("extension,expected", [(".raw", "orbi"), (".h5", "tof")])
def test_the_data_file_decides_the_class(extension, expected):
    # ".raw" is an Orbitrap acquisition, ".h5" a TOF one - the same rule the
    # reader applies when it converts the file.
    filename = f"Test_2026.09.06-10h12m01s_by{extension.strip('.')}"
    _converted(filename, data=extension)
    assert m_name.get_instrument_type(filename) == expected


def test_the_data_file_outranks_a_name_and_a_stale_props_record():
    # Nothing about a name or an old props entry may override the file that
    # is actually sitting there.
    filename = "Rapid_2026.09.06-11h12m01s_ambient"
    assert m_name.resolve_instrument_type("Rapid", throw=False) == "tof"
    _converted(filename, data=".raw", instrument_type="tof")
    assert m_name.get_instrument_type(filename) == "orbi"


def test_a_sample_without_its_source_file_falls_back_to_the_props():
    # The source file can be dropped once the zarr exists; the props still say.
    filename = "Test_2026.09.06-12h12m01s_zarronly"
    _converted(filename, instrument_type="tof")
    assert m_name.get_instrument_type(filename) == "tof"


def test_a_name_with_no_acquisition_time_still_answers_from_the_name():
    # Callers pass bare names that have no filestore path at all - an
    # instrument name on its own, a sample never converted here. Deriving the
    # path must not turn that into an error.
    assert m_name.get_instrument_type("orbitrap") == "orbi"
    assert m_name.get_instrument_type("tof3") == "tof"
