"""Tests: the name an upload is stored under, and the instrument it is filed under.

Everything that files, reads or lists a sample takes the instrument from the
first underscore-separated segment of the stored name. So an upload from an
agent that reports its instrument is stored under a name that starts with it,
and the file name the acquisition software chose no longer has to.
"""

import pytest

from mascope_backend.api.controllers.sample.files import sample_files_controller as ctl


def test_without_a_reported_instrument_the_name_is_stored_as_it_arrived():
    assert ctl.file_upload_name("Orbi-Lab2_2026.09.05-10h12m01s.raw", None) == (
        "Orbi-Lab2_2026.09.05-10h12m01s.raw",
        "Orbi-Lab2",
    )


def test_a_reported_instrument_is_put_in_front_of_a_name_that_lacks_it():
    # The case the whole change exists for: files named for the acquisition,
    # filed under the instrument the agent watches.
    assert ctl.file_upload_name("ambient_2026.09.05-10h12m01s.raw", "Test") == (
        "Test_ambient_2026.09.05-10h12m01s.raw",
        "Test",
    )


def test_a_name_that_already_starts_with_the_instrument_is_left_alone():
    # The agent's own upload prefix, or acquisition software that names files
    # by instrument: prefixing again would make Test_Test_... lineages.
    assert ctl.file_upload_name("Test_ambient_2026.09.05-10h12m01s.raw", "Test") == (
        "Test_ambient_2026.09.05-10h12m01s.raw",
        "Test",
    )


def test_a_name_starting_with_another_instrument_is_still_filed_where_the_agent_says():
    # The agent is the authority for what it watches; a file that carries a
    # different instrument segment is filed under the reported one and keeps
    # its original name intact behind the prefix.
    assert ctl.file_upload_name("KORBI2_2026.09.05-10h12m01s.raw", "Test") == (
        "Test_KORBI2_2026.09.05-10h12m01s.raw",
        "Test",
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_blank_report_is_no_report(value):
    assert ctl.reported_instrument_or_none(value) is None


def test_a_well_formed_report_is_kept_trimmed():
    assert ctl.reported_instrument_or_none(" Orbi-Lab2 ") == "Orbi-Lab2"


def test_a_malformed_report_is_dropped_with_a_warning(monkeypatch):
    # The agent validates the name before it starts, so this is a bug on the
    # far side; the upload has already been accepted, so it is filed by its
    # name rather than dropped, and the log says why.
    warnings = []
    monkeypatch.setattr(ctl.runtime.logger, "warning", lambda msg: warnings.append(msg))
    assert ctl.reported_instrument_or_none("orbi lab") is None
    assert warnings and "Ignoring the instrument reported" in warnings[0]
