"""Scan filter parsing (``mascope_thermo.scan_filter``).

The parser decides which scans form one stream, so two properties are
load-bearing and pinned here:

* every field that says what a scan measured reaches the key, so scans that
  must not be pooled never share one;
* the key does not depend on how a reader rendered the filter, so both
  backends agree on it.

The filters are written for these tests in Xcalibur's notation; none is taken
from a customer acquisition.
"""

import json

import pytest

from mascope_thermo.scan_filter import Precursor, parse_scan_filter


def test_a_survey_scan_filter_is_read_field_by_field():
    parsed = parse_scan_filter("FTMS - p NSI Full ms [40.0000-600.0000]")

    assert parsed.analyzer == "FTMS"
    assert parsed.polarity == "-"
    assert parsed.data_type == "p"
    assert parsed.source == "NSI"
    assert parsed.scan_mode == "Full"
    assert parsed.ms_order == 1
    assert parsed.scan_ranges == ((40.0, 600.0),)
    assert parsed.precursors == ()
    assert parsed.flags == ()
    assert parsed.dependent is False
    assert (
        parsed.stream_key(120000) == "FTMS - p NSI Full ms [40.0000-600.0000] R=120000"
    )


def test_every_documented_field_is_read():
    parsed = parse_scan_filter(
        "FTMS {1,2} + c ESI sid=35.00 cv=-45.00 d Full ms2 "
        "445.1200@cid30.00@hcd20.00 [50.0000-500.0000]"
    )

    assert (parsed.segment, parsed.event) == (1, 2)
    assert parsed.source_fragmentation == 35.0
    assert parsed.compensation_voltage == -45.0
    assert parsed.dependent is True
    assert parsed.ms_order == 2
    assert parsed.precursors == (Precursor(445.12, "cid30.00@hcd20.00"),)


def test_multiplexed_ranges_are_read_as_one_list():
    parsed = parse_scan_filter(
        "FTMS + p NSI SIM msx ms [300.0000-320.0000, 400.0000-420.0000]"
    )

    assert parsed.scan_mode == "SIM"
    assert parsed.flags == ("msx",)
    assert parsed.scan_ranges == ((300.0, 320.0), (400.0, 420.0))


def test_a_filter_without_an_analyzer_is_still_read():
    parsed = parse_scan_filter("+ c EI Full ms [50.00-500.00]")

    assert parsed.analyzer is None
    assert parsed.polarity == "+"
    assert parsed.source == "EI"
    assert parsed.stream_key() == "+ c EI Full ms [50.0000-500.0000]"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # The other scan range of the same polarity: the pooling this exists for.
        (
            "FTMS - p NSI Full ms [40.0000-160.0000]",
            "FTMS - p NSI Full ms [128.0000-600.0000]",
        ),
        (
            "FTMS + p ESI Full ms [50.0000-750.0000]",
            "FTMS - p ESI Full ms [50.0000-750.0000]",
        ),
        (
            "FTMS + p ESI Full ms [50.0000-750.0000]",
            "FTMS + c ESI Full ms [50.0000-750.0000]",
        ),
        (
            "FTMS + p ESI Full ms [50.0000-750.0000]",
            "FTMS + p APCI Full ms [50.0000-750.0000]",
        ),
        (
            "FTMS + p ESI Full ms [50.0000-750.0000]",
            "FTMS + p ESI SIM ms [50.0000-750.0000]",
        ),
        (
            "FTMS + p ESI Full ms [50.0000-750.0000]",
            "ITMS + p ESI Full ms [50.0000-750.0000]",
        ),
        (
            "FTMS + p ESI Full ms [50.0000-750.0000]",
            "FTMS + p ESI sid=35.00 Full ms [50.0000-750.0000]",
        ),
        (
            "FTMS + p NSI cv=-45.00 Full ms [50.0000-750.0000]",
            "FTMS + p NSI cv=-60.00 Full ms [50.0000-750.0000]",
        ),
        (
            "FTMS + p NSI Full ms2 200.0000@hcd30.00 [50.0000-210.0000]",
            "FTMS + p NSI Full ms2 300.0000@hcd30.00 [50.0000-310.0000]",
        ),
        (
            "FTMS + p NSI Full ms2 200.0000@hcd30.00 [50.0000-210.0000]",
            "FTMS + p NSI Full ms2 200.0000@cid30.00 [50.0000-210.0000]",
        ),
        (
            "FTMS + p NSI Full ms [50.0000-750.0000]",
            "FTMS + p NSI Full ms2 200.0000@hcd30.00 [50.0000-750.0000]",
        ),
        # A token the parser does not know still separates.
        (
            "FTMS + p NSI Full ms [50.0000-750.0000]",
            "FTMS + p NSI E Full ms [50.0000-750.0000]",
        ),
    ],
)
def test_filters_that_measure_differently_get_different_keys(first, second):
    assert (
        parse_scan_filter(first).stream_key() != parse_scan_filter(second).stream_key()
    )


def test_resolution_separates_otherwise_identical_filters():
    parsed = parse_scan_filter("FTMS - p NSI Full ms [40.0000-600.0000]")
    assert parsed.stream_key(120000) != parsed.stream_key(240000)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Readers render ITMS-style and FTMS-style precision for the same values.
        (
            "FTMS - p NSI Full ms [40.00-600.00]",
            "FTMS - p NSI Full ms [40.0000-600.0000]",
        ),
        (
            "FTMS + p NSI Full ms2 200.00@hcd30.0 [50.00-210.00]",
            "FTMS + p NSI Full ms2 200.0000@HCD30.00 [50.0000-210.0000]",
        ),
        # The segment and scan event index the method's layout, not a measurement.
        (
            "FTMS {1,1} - p NSI Full ms [40.0000-600.0000]",
            "FTMS {1,2} - p NSI Full ms [40.0000-600.0000]",
        ),
        # A negated flag states the default a missing one implies.
        (
            "FTMS - p NSI !corona Full ms [40.0000-600.0000]",
            "FTMS - p NSI Full ms [40.0000-600.0000]",
        ),
        # Flags in either order.
        (
            "FTMS - p NSI E t Full ms [40.0000-600.0000]",
            "FTMS - p NSI t E Full ms [40.0000-600.0000]",
        ),
        (
            "  FTMS - p NSI Full ms [40.0000-600.0000]  ",
            "FTMS - p NSI Full ms [40.0000-600.0000]",
        ),
    ],
)
def test_renderings_of_one_measurement_share_a_key(first, second):
    assert (
        parse_scan_filter(first).stream_key() == parse_scan_filter(second).stream_key()
    )


def test_data_dependent_fragmentation_folds_into_one_family():
    """Each data-dependent scan isolates what the survey scan picked, so its
    precursor and scan range change from scan to scan: one stream, not one per
    precursor."""
    keys = {
        parse_scan_filter(text).stream_key(15000)
        for text in (
            "FTMS + c NSI d Full ms2 445.1200@hcd30.00 [110.0000-455.0000]",
            "FTMS + c NSI d Full ms2 512.3300@hcd30.00 [140.0000-522.0000]",
            "FTMS + c NSI d Full ms2 610.0000@hcd30.00 [165.0000-620.0000]",
        )
    }
    assert keys == {"FTMS + c NSI d Full ms2 *@hcd30.00 R=15000"}


def test_a_data_dependent_survey_scan_is_not_folded():
    parsed = parse_scan_filter("FTMS + p NSI d Full ms [50.0000-750.0000]")
    assert parsed.folds_precursors is False
    assert parsed.scan_ranges == ((50.0, 750.0),)


def test_every_stage_of_a_multistage_scan_is_kept():
    parsed = parse_scan_filter(
        "ITMS + c NSI Full ms3 500.00@cid35.00 300.00@cid35.00 [135.00-1000.00]"
    )
    assert parsed.ms_order == 3
    assert parsed.precursors == (
        Precursor(500.0, "cid35.00"),
        Precursor(300.0, "cid35.00"),
    )
    assert parsed.stream_key() == (
        "ITMS + c NSI Full ms3 500.0000@cid35.00 300.0000@cid35.00 [135.0000-1000.0000]"
    )


def test_an_activation_without_an_energy_is_kept():
    parsed = parse_scan_filter("FTMS + c NSI Full ms2 445.1200@etd [50.0000-500.0000]")
    assert parsed.precursors == (Precursor(445.12, "etd"),)


@pytest.mark.parametrize("text", [None, "", "   ", "garbage", "[", "FTMS + p [a-b]"])
def test_an_unreadable_filter_parses_without_raising(text):
    parsed = parse_scan_filter(text)
    json.dumps(parsed.signature())
    assert isinstance(parsed.stream_key(), str)


def test_unknown_tokens_are_kept_as_flags():
    parsed = parse_scan_filter("FTMS + p NSI t u Full ms [50.0000-750.0000]")
    assert parsed.flags == ("t", "u")
    assert parsed.stream_key() == "FTMS + p NSI t u Full ms [50.0000-750.0000]"


def test_a_found_lock_mass_does_not_start_a_new_stream():
    """The Thermo library writes ``lock`` into the filter of each scan that
    found its lock mass; OpenTFRaw never does. Scans of one stream find it or
    not from scan to scan."""
    locked = parse_scan_filter("FTMS + p ESI Full lock ms [50.0000-750.0000]")
    unlocked = parse_scan_filter("FTMS + p ESI Full ms [50.0000-750.0000]")

    assert locked.flags == ("lock",)
    assert locked.stream_key(280000) == unlocked.stream_key(280000)
    assert locked.signature(280000) == unlocked.signature(280000)


def test_the_signature_is_json_safe():
    parsed = parse_scan_filter(
        "FTMS + p NSI cv=-45.00 Full ms2 200.0000@hcd30.00 [50.0000-210.0000]"
    )
    signature = parsed.signature(120000)
    assert json.loads(json.dumps(signature)) == signature
    assert signature["compensation_voltage"] == -45.0
    assert signature["precursors"] == [{"mz": 200.0, "activation": "hcd30.00"}]
    assert signature["scan_ranges"] == [[50.0, 210.0]]
    assert signature["resolution"] == 120000


def test_a_folded_signature_drops_what_varies_per_scan():
    signature = parse_scan_filter(
        "FTMS + c NSI d Full ms2 445.1200@hcd30.00 [110.0000-455.0000]"
    ).signature()
    assert signature["precursors"] == [{"mz": None, "activation": "hcd30.00"}]
    assert signature["scan_ranges"] == []
