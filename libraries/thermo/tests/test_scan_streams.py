"""The scan-stream census (``mascope_thermo.streams``).

The census groups a file's scans into the experiments of its method and
records, per stream, how many scans it holds, how they are laid out in time,
and the acquisition parameters of its own scans. A scripted reader drives the
layouts no committed file has: alternating scan ranges, polarity switching,
data-dependent fragmentation, a resolution change, two experiments under one
filter and an experiment the method repeats. The two committed sample files
then pin the census on real data, through each backend available.
"""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo.backend import (
    _SCAN_INDEX_UNSET,
    OpenTFRawBackend,
    ThermoBackend,
    _method_experiment,
    _method_scan_event,
    _summarize_acquisition_parameters,
    open_backend,
)
from mascope_thermo.streams import (
    peak_streams,
    pooled_ms1_streams,
    scan_streams,
    stream_report,
)


class _ScriptedReader:
    """Reader stand-in: one ``(filter, trailer)``, ``(filter, trailer, event)``
    or ``(filter, trailer, event, segment)`` per scan, a second apart.

    A scan given no event records none, and so no segment. One given an
    event and no segment is in the method's first, as a real reader has it.
    """

    def __init__(self, scans):
        self._scans = [(*scan, None, None)[:4] for scan in scans]

    def scan_filters(self):
        return [
            {
                "scan": n,
                "time_s": float(n - 1),
                "filter": text,
                "segment": None if event is None else (segment or 1),
                "event": event,
            }
            for n, (text, _trailer, event, segment) in enumerate(self._scans, 1)
        ]

    def scan_trailer(self, scan_number):
        return self._scans[scan_number - 1][1]

    def acquisition_parameters(self, max_scans=5, scan_numbers=None):
        return _summarize_acquisition_parameters(
            "scripted", [self.scan_trailer(n) for n in scan_numbers[:max_scans]]
        )


NEG_LOW = "FTMS - p NSI Full ms [40.0000-160.0000]"
NEG_HIGH = "FTMS - p NSI Full ms [128.0000-600.0000]"
POS_LOW = "FTMS + p NSI Full ms [40.0000-160.0000]"


def _trailer(resolution=120000, **extra):
    return {"FT Resolution:": resolution, "AGC Target:": 100000, **extra}


def _by_key(census):
    return {stream["key"]: stream for stream in census}


def test_a_single_stream_file_is_one_stream_in_one_block():
    census = scan_streams(_ScriptedReader([(NEG_LOW, _trailer())] * 4))

    assert len(census) == 1
    stream = census[0]
    assert stream["key"] == f"{NEG_LOW} R=120000"
    assert stream["signature_key"] == stream["key"]
    assert stream["scans"] == 4
    assert stream["blocks"] == 1
    assert (stream["t_first"], stream["t_last"]) == (0.0, 3.0)
    assert stream["filters"] == 1
    assert stream["acquisition_params"]["constant"]["FT Resolution:"] == 120000
    assert pooled_ms1_streams(census) == {}


def test_alternating_ranges_in_one_polarity_are_two_pooled_streams():
    census = scan_streams(
        _ScriptedReader([(NEG_LOW, _trailer()), (NEG_HIGH, _trailer())] * 3)
    )

    streams = _by_key(census)
    assert set(streams) == {f"{NEG_LOW} R=120000", f"{NEG_HIGH} R=120000"}
    assert all(stream["scans"] == 3 for stream in census)
    # Every scan of one range is interrupted by a scan of the other.
    assert all(stream["blocks"] == 3 for stream in census)
    assert pooled_ms1_streams(census) == {
        "-": [f"{NEG_LOW} R=120000", f"{NEG_HIGH} R=120000"]
    }


def test_polarity_switching_leaves_each_polarity_one_block():
    census = scan_streams(
        _ScriptedReader([(NEG_LOW, _trailer()), (POS_LOW, _trailer())] * 3)
    )

    assert [stream["blocks"] for stream in census] == [1, 1]
    assert [stream["signature"]["polarity"] for stream in census] == ["-", "+"]
    assert pooled_ms1_streams(census) == {}


def test_a_range_switch_part_way_through_is_two_long_blocks():
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, _trailer())] * 3
            + [(NEG_HIGH, _trailer())] * 3
            + [(NEG_LOW, _trailer())] * 2
        )
    )

    streams = _by_key(census)
    assert streams[f"{NEG_LOW} R=120000"]["blocks"] == 2
    assert streams[f"{NEG_LOW} R=120000"]["scans"] == 5
    assert streams[f"{NEG_HIGH} R=120000"]["blocks"] == 1
    assert (
        streams[f"{NEG_HIGH} R=120000"]["t_first"],
        streams[f"{NEG_HIGH} R=120000"]["t_last"],
    ) == (3.0, 5.0)


def test_data_dependent_scans_are_one_family_beside_their_survey_stream():
    survey = "FTMS + p NSI Full ms [100.0000-1000.0000]"
    census = scan_streams(
        _ScriptedReader(
            [
                (survey, _trailer()),
                (
                    "FTMS + c NSI d Full ms2 445.1200@hcd30.00 [110.0000-455.0000]",
                    _trailer(15000),
                ),
                (
                    "FTMS + c NSI d Full ms2 512.3300@hcd30.00 [140.0000-522.0000]",
                    _trailer(15000),
                ),
                (survey, _trailer()),
                (
                    "FTMS + c NSI d Full ms2 610.0000@hcd30.00 [165.0000-620.0000]",
                    _trailer(15000),
                ),
            ]
        )
    )

    streams = _by_key(census)
    family = streams["FTMS + c NSI d Full ms2 *@hcd30.00 R=15000"]
    assert family["scans"] == 3
    assert family["filters"] == 3
    assert family["blocks"] == 1
    # Fragmentation scans in between do not break the survey stream's run.
    assert streams[f"{survey} R=120000"]["blocks"] == 1
    assert pooled_ms1_streams(census) == {}


def test_only_survey_streams_carry_a_parameter_summary():
    """A targeted method has one fragmentation stream per precursor, and each
    summary is a few kilobytes of a file that is read many times."""
    survey = "FTMS + p NSI Full ms [100.0000-1000.0000]"
    targeted = [
        f"FTMS + p NSI Full ms2 {mz:.4f}@hcd30.00 [50.0000-{mz + 10:.4f}]"
        for mz in (200.0, 300.0, 400.0)
    ]
    census = scan_streams(
        _ScriptedReader(
            [(survey, _trailer())] + [(text, _trailer(15000)) for text in targeted]
        )
    )

    assert [stream["signature"]["ms_order"] for stream in census] == [1, 2, 2, 2]
    assert census[0]["acquisition_params"]["scans_sampled"] == 1
    assert [stream["acquisition_params"] for stream in census[1:]] == [{}, {}, {}]


def test_a_lock_mass_found_or_not_is_neither_a_stream_nor_a_filter():
    """The Thermo library renders ``lock`` only on scans that found the lock
    mass, so one stream carries both renderings."""
    census = scan_streams(
        _ScriptedReader(
            [
                ("FTMS + p ESI Full lock ms [50.0000-750.0000]", _trailer()),
                ("FTMS + p ESI Full ms [50.0000-750.0000]", _trailer()),
                ("FTMS + p ESI Full lock ms [50.0000-750.0000]", _trailer()),
            ]
        )
    )

    assert len(census) == 1
    assert census[0]["scans"] == 3
    assert census[0]["filters"] == 1
    assert census[0]["blocks"] == 1


def test_a_resolution_change_is_a_new_stream():
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, _trailer(120000))] * 2 + [(NEG_LOW, _trailer(240000))] * 2
        )
    )

    assert [stream["key"] for stream in census] == [
        f"{NEG_LOW} R=120000",
        f"{NEG_LOW} R=240000",
    ]
    assert pooled_ms1_streams(census) == {"-": [stream["key"] for stream in census]}


def test_a_resolution_reported_as_text_gives_the_same_key():
    """The Thermo backend reports trailer values as text, OpenTFRaw as numbers."""
    as_number = scan_streams(_ScriptedReader([(NEG_LOW, _trailer(120000))]))
    as_text = scan_streams(_ScriptedReader([(NEG_LOW, _trailer("120000"))]))
    assert as_number[0]["key"] == as_text[0]["key"]


def test_each_stream_summarises_the_parameters_of_its_own_scans():
    census = scan_streams(
        _ScriptedReader(
            [
                (NEG_LOW, _trailer(**{"Max. Ion Time (ms):": 50})),
                (NEG_HIGH, _trailer(**{"Max. Ion Time (ms):": 200})),
            ]
            * 3
        )
    )

    streams = _by_key(census)
    low = streams[f"{NEG_LOW} R=120000"]["acquisition_params"]
    high = streams[f"{NEG_HIGH} R=120000"]["acquisition_params"]
    # Sampled across both streams, the setting would only show as varying.
    assert low["constant"]["Max. Ion Time (ms):"] == 50
    assert high["constant"]["Max. Ion Time (ms):"] == 200
    assert low["scans_sampled"] == high["scans_sampled"] == 3


def test_a_scan_without_a_resolution_is_keyed_without_one():
    census = scan_streams(
        _ScriptedReader([("ITMS + c NSI Full ms [100.00-1000.00]", {"AGC Target:": 1})])
    )
    assert census[0]["key"] == "ITMS + c NSI Full ms [100.0000-1000.0000]"
    assert census[0]["signature"]["resolution"] is None


def test_a_file_without_scans_has_no_streams():
    assert scan_streams(_ScriptedReader([])) == []


# -- streams are the experiments of the method --------------------------------


def _identity(stream):
    """What a stream is, as against what it is called in its file."""
    return stream["signature_key"], stream["scan_segment"], stream["scan_event"]


def test_two_experiments_under_one_filter_are_two_streams():
    """A method can define two scans that differ in nothing a filter shows: a
    short one at one microscan while the source settles, then the measurement
    at ten. The scan event is all that tells them apart."""
    settling = _trailer(**{"Micro Scan Count:": 1, "AGC Target:": 300000})
    measuring = _trailer(**{"Micro Scan Count:": 10, "AGC Target:": 1000000})
    census = scan_streams(
        _ScriptedReader([(NEG_LOW, settling, 1)] * 7 + [(NEG_LOW, measuring, 2)] * 7)
    )

    assert [stream["key"] for stream in census] == [
        f"{NEG_LOW} R=120000 event=1",
        f"{NEG_LOW} R=120000 event=2",
    ]
    assert [stream["scan_event"] for stream in census] == [1, 2]
    assert [stream["scan_segment"] for stream in census] == [1, 1]
    assert [stream["scans"] for stream in census] == [7, 7]
    assert [stream["blocks"] for stream in census] == [1, 1]
    assert [(s["t_first"], s["t_last"]) for s in census] == [(0.0, 6.0), (7.0, 13.0)]
    # One signature for both: what they measure is the same to a filter.
    assert census[0]["signature"] == census[1]["signature"]
    assert [stream["signature_key"] for stream in census] == [f"{NEG_LOW} R=120000"] * 2
    # Each stream summarises its own scans, so neither setting reads as varying.
    assert [
        stream["acquisition_params"]["constant"]["Micro Scan Count:"]
        for stream in census
    ] == [1, 10]
    assert pooled_ms1_streams(census) == {"-": [stream["key"] for stream in census]}


def test_a_repeated_experiment_is_one_stream_per_repeat():
    """A method that defines the same scan again later runs it as another
    experiment, with whatever the instrument measured in between."""
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, _trailer(), 1)] * 3
            + [(POS_LOW, _trailer(), 2)] * 3
            + [(NEG_LOW, _trailer(), 3)] * 2
            + [(POS_LOW, _trailer(), 4)] * 2
        )
    )

    assert [stream["key"] for stream in census] == [
        f"{NEG_LOW} R=120000 event=1",
        f"{POS_LOW} R=120000 event=2",
        f"{NEG_LOW} R=120000 event=3",
        f"{POS_LOW} R=120000 event=4",
    ]
    assert [stream["scans"] for stream in census] == [3, 3, 2, 2]
    # Each repeat is one run; by filter alone the two negative ones were one
    # stream in one block, the positive scans between them not counting.
    assert [stream["blocks"] for stream in census] == [1, 1, 1, 1]
    assert pooled_ms1_streams(census) == {
        "-": [f"{NEG_LOW} R=120000 event=1", f"{NEG_LOW} R=120000 event=3"],
        "+": [f"{POS_LOW} R=120000 event=2", f"{POS_LOW} R=120000 event=4"],
    }


def test_experiments_their_filters_already_separate_keep_their_keys():
    """Nearly every file: one experiment per filter. Its keys are the ones it
    had before streams followed the scan event, so nothing keyed on them
    moves."""
    census = scan_streams(
        _ScriptedReader([(NEG_LOW, _trailer(), 1), (NEG_HIGH, _trailer(), 2)] * 3)
    )

    assert [stream["key"] for stream in census] == [
        f"{NEG_LOW} R=120000",
        f"{NEG_HIGH} R=120000",
    ]
    assert [stream["signature_key"] for stream in census] == [
        stream["key"] for stream in census
    ]
    assert [stream["scan_event"] for stream in census] == [1, 2]
    assert [stream["blocks"] for stream in census] == [3, 3]


def test_the_event_joins_only_the_keys_that_need_it():
    """One file can hold both: an experiment run once, and one repeated."""
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, _trailer(), 1)] * 2
            + [(NEG_HIGH, _trailer(), 2)] * 2
            + [(NEG_HIGH, _trailer(), 3)] * 2
        )
    )

    assert [stream["key"] for stream in census] == [
        f"{NEG_LOW} R=120000",
        f"{NEG_HIGH} R=120000 event=2",
        f"{NEG_HIGH} R=120000 event=3",
    ]
    assert [stream["scan_event"] for stream in census] == [1, 2, 3]


def test_a_key_names_a_stream_in_its_file_and_its_identity_names_it_anywhere():
    """One method, three runs: complete, stopped during its third experiment,
    and stopped during its second. An experiment's key depends on whether the
    run got far enough to repeat its signature, so the same experiment is
    named two ways. Its signature, segment and event are not: those read the
    same in every run."""
    method = [(NEG_LOW, 1), (POS_LOW, 2), (NEG_LOW, 3), (POS_LOW, 4)]

    def run(experiments):
        return scan_streams(
            _ScriptedReader(
                [(text, _trailer(), event) for text, event in experiments] * 2
            )
        )

    complete, to_third, to_second = run(method), run(method[:3]), run(method[:2])

    assert [stream["key"] for stream in complete] == [
        f"{NEG_LOW} R=120000 event=1",
        f"{POS_LOW} R=120000 event=2",
        f"{NEG_LOW} R=120000 event=3",
        f"{POS_LOW} R=120000 event=4",
    ]
    assert [stream["key"] for stream in to_third] == [
        f"{NEG_LOW} R=120000 event=1",
        f"{POS_LOW} R=120000",
        f"{NEG_LOW} R=120000 event=3",
    ]
    assert [stream["key"] for stream in to_second] == [
        f"{NEG_LOW} R=120000",
        f"{POS_LOW} R=120000",
    ]
    # Every key is unique within its file...
    for census in (complete, to_third, to_second):
        assert len({stream["key"] for stream in census}) == len(census)
    # ...and the experiments each run reached are the complete run's first ones.
    identities = [_identity(stream) for stream in complete]
    assert [_identity(stream) for stream in to_third] == identities[:3]
    assert [_identity(stream) for stream in to_second] == identities[:2]
    assert identities[1] == (f"{POS_LOW} R=120000", 1, 2)


def test_a_file_that_records_no_event_is_keyed_by_its_filters():
    """An acquisition started with no method loaded. A setting changed by hand
    part way through leaves no mark, so it stays one stream, as it was."""
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, _trailer(**{"Micro Scan Count:": 1}))] * 3
            + [(NEG_LOW, _trailer(**{"Micro Scan Count:": 10}))] * 3
        )
    )

    assert len(census) == 1
    assert census[0]["key"] == f"{NEG_LOW} R=120000"
    assert _identity(census[0]) == (f"{NEG_LOW} R=120000", None, None)
    assert census[0]["scans"] == 6
    assert "Micro Scan Count:" in census[0]["acquisition_params"]["varying"]


def test_scans_that_record_no_event_are_a_stream_beside_those_that_do():
    """No file in reach mixes the two under one signature, so this pins the
    answer rather than a measurement: the scans that record no experiment
    are the signature's own stream, and the ones that record one are named
    by it."""
    census = scan_streams(
        _ScriptedReader([(NEG_LOW, _trailer())] * 2 + [(NEG_LOW, _trailer(), 1)] * 2)
    )

    assert [(stream["key"], stream["scans"]) for stream in census] == [
        (f"{NEG_LOW} R=120000", 2),
        (f"{NEG_LOW} R=120000 event=1", 2),
    ]
    assert [_identity(stream) for stream in census] == [
        (f"{NEG_LOW} R=120000", None, None),
        (f"{NEG_LOW} R=120000", 1, 1),
    ]


# -- which streams a file's peaks are detected per -------------------------------


def test_a_file_with_one_survey_stream_in_each_polarity_is_detected_whole():
    """Its polarity is its stream already."""
    for scans in (
        [(NEG_LOW, _trailer(), 1)] * 3,
        [(NEG_LOW, _trailer(), 1), (POS_LOW, _trailer(), 2)] * 2,
        [(NEG_LOW, _trailer())] * 3,
    ):
        assert peak_streams(scan_streams(_ScriptedReader(scans))) == []


def test_a_multi_stream_file_is_detected_per_survey_stream_it_holds():
    """Two experiments in one polarity, and the file is detected per stream.
    The single experiment of the other polarity is then a stream like them,
    and a fragmentation stream is not: it gets no peak list of its own."""
    fragments = "FTMS - p NSI Full ms2 300.0000@hcd30.00 [50.0000-310.0000]"
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, _trailer(), 1)] * 2
            + [(POS_LOW, _trailer(), 2)] * 2
            + [(fragments, _trailer(15000), 3)] * 2
            + [(NEG_HIGH, _trailer(), 4)] * 2
        )
    )

    assert [stream["key"] for stream in peak_streams(census)] == [
        f"{NEG_LOW} R=120000",
        f"{POS_LOW} R=120000",
        f"{NEG_HIGH} R=120000",
    ]


# -- an experiment is a scan event within a segment --------------------------


def test_the_same_event_number_in_two_segments_is_two_experiments():
    """A method numbers its scan events within each of its segments, so a
    settle-then-measure method written as two segments has an event 1 in
    each, under one filter. Grouped by the event alone they would pool."""
    settling = _trailer(**{"Micro Scan Count:": 1})
    measuring = _trailer(**{"Micro Scan Count:": 10})
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, settling, 1, 1)] * 2 + [(NEG_LOW, measuring, 1, 2)] * 3
        )
    )

    assert [stream["key"] for stream in census] == [
        f"{NEG_LOW} R=120000 event=1",
        f"{NEG_LOW} R=120000 segment=2 event=1",
    ]
    assert [_identity(stream) for stream in census] == [
        (f"{NEG_LOW} R=120000", 1, 1),
        (f"{NEG_LOW} R=120000", 2, 1),
    ]
    assert [stream["scans"] for stream in census] == [2, 3]
    assert [
        stream["acquisition_params"]["constant"]["Micro Scan Count:"]
        for stream in census
    ] == [1, 10]


def test_a_segment_is_named_only_outside_the_methods_first():
    """A method of one segment, which is nearly every method, reads
    ``event=N``. The segment is still on the stream either way."""
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, _trailer(), 1, 2)] * 2 + [(NEG_LOW, _trailer(), 2, 2)] * 2
        )
    )

    assert [stream["key"] for stream in census] == [
        f"{NEG_LOW} R=120000 segment=2 event=1",
        f"{NEG_LOW} R=120000 segment=2 event=2",
    ]
    assert [stream["scan_segment"] for stream in census] == [2, 2]


def test_segments_their_filters_already_separate_keep_their_keys():
    census = scan_streams(
        _ScriptedReader(
            [(NEG_LOW, _trailer(), 1, 1)] * 2 + [(NEG_HIGH, _trailer(), 1, 2)] * 2
        )
    )

    assert [stream["key"] for stream in census] == [
        f"{NEG_LOW} R=120000",
        f"{NEG_HIGH} R=120000",
    ]
    assert [(s["scan_segment"], s["scan_event"]) for s in census] == [(1, 1), (2, 1)]


# -- fragmentation scans ------------------------------------------------------


def test_fragmentation_scans_are_keyed_by_filter_whatever_their_event():
    """Only survey scans follow the scan event. A dependent scan's event may
    count its place in the cycle rather than an experiment, and a family
    split by it would be one stream per slot."""
    survey = "FTMS + p NSI Full ms [100.0000-1000.0000]"
    dependent = "FTMS + c NSI d Full ms2 {mz}@hcd30.00 [50.0000-700.0000]"
    census = scan_streams(
        _ScriptedReader(
            [
                (survey, _trailer(), 1),
                (dependent.format(mz="445.1200"), _trailer(15000), 2),
                (dependent.format(mz="512.3300"), _trailer(15000), 3),
                (survey, _trailer(), 1),
                (dependent.format(mz="610.0000"), _trailer(15000), 2),
            ]
        )
    )

    streams = _by_key(census)
    assert set(streams) == {
        f"{survey} R=120000",
        "FTMS + c NSI d Full ms2 *@hcd30.00 R=15000",
    }
    family = streams["FTMS + c NSI d Full ms2 *@hcd30.00 R=15000"]
    assert family["scans"] == 3
    assert family["signature_key"] == family["key"]
    assert streams[f"{survey} R=120000"]["scan_event"] == 1


def test_a_fragmentation_stream_says_nothing_about_its_experiment():
    """``None`` on a stream means the file records no experiment. A
    fragmentation stream is not grouped by one at all, which is a different
    thing to say, so it carries neither key - whether its scans share an
    event or not."""
    targeted = "FTMS + p NSI Full ms2 300.0000@hcd30.00 [50.0000-310.0000]"
    dependent = "FTMS + c NSI d Full ms2 {mz}@hcd30.00 [50.0000-700.0000]"
    census = scan_streams(
        _ScriptedReader(
            [(targeted, _trailer(15000), 2)] * 3
            + [
                (dependent.format(mz="445.1200"), _trailer(15000), 3),
                (dependent.format(mz="512.3300"), _trailer(15000), 4),
            ]
        )
    )

    assert [stream["signature"]["ms_order"] for stream in census] == [2, 2]
    for stream in census:
        assert "scan_event" not in stream
        assert "scan_segment" not in stream


def test_a_reader_that_reports_no_experiment_reads_as_recording_none():
    """``segment`` and ``event`` are read with a default, so a reader double
    written before the census followed them still takes one."""

    class _Eventless(_ScriptedReader):
        def scan_filters(self):
            return [
                {
                    key: value
                    for key, value in row.items()
                    if key not in ("segment", "event")
                }
                for row in super().scan_filters()
            ]

    census = scan_streams(_Eventless([(NEG_LOW, _trailer(), 1)] * 2))
    assert [(s["key"], _identity(s)) for s in census] == [
        (f"{NEG_LOW} R=120000", (f"{NEG_LOW} R=120000", None, None))
    ]


def test_an_event_reported_without_a_segment_is_in_the_first():
    class _Segmentless(_ScriptedReader):
        def scan_filters(self):
            return [
                {key: value for key, value in row.items() if key != "segment"}
                for row in super().scan_filters()
            ]

    census = scan_streams(_Segmentless([(NEG_LOW, _trailer(), 2)] * 2))
    assert _identity(census[0]) == (f"{NEG_LOW} R=120000", 1, 2)


# -- the experiment, from the scan index ----------------------------------------


@pytest.mark.parametrize(
    ("index_event", "expected"),
    [(0, 1), (7, 8), (_SCAN_INDEX_UNSET, None), (-1, None)],
    ids=["first", "eighth", "unset-in-the-index", "unset-as-thermo-reports-it"],
)
def test_the_method_counts_its_scan_events_from_one(index_event, expected):
    assert _method_scan_event(index_event) == expected


@pytest.mark.parametrize(
    ("index_segment", "index_event", "expected"),
    [
        (0, 0, (1, 1)),
        (0, 7, (1, 8)),
        (1, 0, (2, 1)),
        # No event, so no experiment - whatever the segment word holds, since
        # the Thermo library reports an unset segment as its first.
        (_SCAN_INDEX_UNSET, _SCAN_INDEX_UNSET, (None, None)),
        (0, -1, (None, None)),
        (3, _SCAN_INDEX_UNSET, (None, None)),
        # An event with no segment is in the method's first, under either
        # backend's spelling of "unset".
        (_SCAN_INDEX_UNSET, 2, (1, 3)),
        (-1, 2, (1, 3)),
    ],
    ids=[
        "first-of-the-first",
        "eighth-of-the-first",
        "first-of-the-second",
        "nothing-recorded",
        "no-event-as-thermo-reports-it",
        "a-segment-without-an-event",
        "an-event-without-a-segment",
        "an-event-without-a-segment-negative",
    ],
)
def test_a_scans_experiment_is_its_segment_and_event(
    index_segment, index_event, expected
):
    assert _method_experiment(index_segment, index_event) == expected


class _ScriptedRaw:
    """The slice of ``opentfraw.RawFile`` ``scan_filters`` reads: one
    ``(segment, event)`` per scan, as the scan index holds them."""

    def __init__(self, experiments):
        self._experiments = experiments

    def scan_table(self):
        scan_numbers = list(range(1, len(self._experiments) + 1))
        return {
            "scan_number": scan_numbers,
            "scan_segment": [segment for segment, _event in self._experiments],
            "scan_event": [event for _segment, event in self._experiments],
            "retention_time": [n / 60 for n in scan_numbers],
            "filter_string": [NEG_LOW] * len(scan_numbers),
        }


# The same four scans as each backend's scan index reports them: the first
# two events of the first segment, the first of the second, and a scan that
# records none.
_EXPERIMENTS = [(1, 1), (1, 2), (2, 1), (None, None)]


def test_opentfraw_reports_each_scans_experiment_and_none_where_unset():
    reader = OpenTFRawBackend("unused.raw")
    reader._raw = _ScriptedRaw(
        [(0, 0), (0, 1), (1, 0), (_SCAN_INDEX_UNSET, _SCAN_INDEX_UNSET)]
    )

    rows = reader.scan_filters()
    assert [(row["segment"], row["event"]) for row in rows] == _EXPERIMENTS
    assert [row["scan"] for row in rows] == [1, 2, 3, 4]


class _ScriptedThermoRaw:
    """The slice of the Thermo ``RawFileReaderAdapter`` ``scan_filters``
    reads: one ``(SegmentNumber, ScanEventNumber)`` per scan, as the Thermo
    library reports the scan index. Needs nothing from pythonnet."""

    def __init__(self, experiments):
        self._experiments = experiments
        self.RunHeaderEx = SimpleNamespace(SpectraCount=len(experiments))

    def GetFilterForScanNumber(self, number):  # noqa: N802
        return SimpleNamespace(ToString=lambda: NEG_LOW)

    def GetScanStatsForScanNumber(self, number):  # noqa: N802
        segment, event = self._experiments[number - 1]
        return SimpleNamespace(
            StartTime=number / 60, SegmentNumber=segment, ScanEventNumber=event
        )


def test_thermo_reports_each_scans_experiment_and_none_where_unset():
    """The Thermo library's spelling of the same index: -1 for an event never
    set, and 0 for a segment never set, which is also its first."""
    reader = ThermoBackend("unused.raw")
    reader._raw = _ScriptedThermoRaw([(0, 0), (0, 1), (1, 0), (0, -1)])

    rows = reader.scan_filters()
    assert [(row["segment"], row["event"]) for row in rows] == _EXPERIMENTS
    assert [row["scan"] for row in rows] == [1, 2, 3, 4]
    assert [row["filter"] for row in rows] == [NEG_LOW] * 4


def test_both_backends_group_the_same_scripted_scans_alike():
    """What the parity test asserts on real files, where the DLLs are: the
    census cannot come out differently because of who read the index."""
    opentfraw = OpenTFRawBackend("unused.raw")
    opentfraw._raw = _ScriptedRaw(
        [(0, 0), (0, 1), (1, 0), (_SCAN_INDEX_UNSET, _SCAN_INDEX_UNSET)]
    )
    thermo = ThermoBackend("unused.raw")
    thermo._raw = _ScriptedThermoRaw([(0, 0), (0, 1), (1, 0), (0, -1)])

    def experiments(reader):
        return [(row["segment"], row["event"]) for row in reader.scan_filters()]

    assert experiments(opentfraw) == experiments(thermo)


# -- the committed sample files, through each backend available --------------


@pytest.mark.parametrize(
    ("path", "polarity"),
    [(POS_ORBI_FILE_PATH, "+"), (NEG_ORBI_FILE_PATH, "-")],
)
def test_a_sample_file_is_one_survey_stream(backend, path, polarity):
    with open_backend(path) as reader:
        census = scan_streams(reader)
        num_scans = reader.num_scans()

    assert len(census) == 1
    stream = census[0]
    signature = stream["signature"]
    assert signature["polarity"] == polarity
    assert signature["ms_order"] == 1
    assert signature["analyzer"] == "FTMS"
    assert signature["scan_ranges"] == [[40.0, 500.0]]
    assert signature["resolution"] == 120000
    assert stream["key"].endswith("[40.0000-500.0000] R=120000")
    assert stream["signature_key"] == stream["key"]
    # One experiment, recorded on every scan as the first event of the
    # method's one segment.
    assert (stream["scan_segment"], stream["scan_event"]) == (1, 1)
    # Every scan counts, the outlier first scan included.
    assert stream["scans"] == num_scans
    assert stream["blocks"] == 1
    assert stream["t_first"] <= stream["t_last"]
    assert stream["acquisition_params"]["scans_sampled"] > 0
    json.dumps(census)  # written verbatim into .props


def test_the_report_adds_the_file_header_and_each_survey_streams_top_peaks(backend):
    report = stream_report(NEG_ORBI_FILE_PATH, top=3)

    assert report["file"] == "KORBI2_AMB_NEG_20260108144525.raw"
    assert report["model"]
    assert report["method_file"].endswith(".meth")
    assert report["scans"] == sum(stream["scans"] for stream in report["streams"])
    (stream,) = report["streams"]
    peaks = stream["top_peaks"]
    assert len(peaks) == 3
    intensities = [intensity for _mz, intensity in peaks]
    assert intensities == sorted(intensities, reverse=True)
    # Nitrate, the reagent ion of this negative-mode acquisition, leads.
    assert abs(peaks[0][0] - 61.9884) < 0.001
    json.dumps(report)


def test_no_top_peaks_are_read_when_none_are_asked_for():
    report = stream_report(POS_ORBI_FILE_PATH, top=0)
    assert "top_peaks" not in report["streams"][0]


def test_the_module_prints_the_report_as_its_last_line():
    """``mascope file scans`` runs this in the backend container and reads the
    last line of stdout, where the runtime's own log lines also go."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mascope_thermo.streams",
            POS_ORBI_FILE_PATH,
            "--top",
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    last = [line for line in result.stdout.splitlines() if line.strip()][-1]
    report = json.loads(last[last.index("{") :])
    assert report["file"] == "KORBI2_AMB_POS_20260109174345.raw"
    assert len(report["streams"][0]["top_peaks"]) == 2
