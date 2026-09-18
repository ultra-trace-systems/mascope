"""The scan-stream census (``mascope_thermo.streams``).

The census groups a file's scans by signature and records, per stream, how
many scans it holds, how they are laid out in time, and the acquisition
parameters of its own scans. A scripted reader drives the layouts no
committed file has: alternating scan ranges, polarity switching,
data-dependent fragmentation and a resolution change. The two committed
sample files then pin the census on real data, through each backend
available.
"""

import json
import subprocess
import sys

import pytest
from conftest import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo.backend import _summarize_acquisition_parameters, open_backend
from mascope_thermo.streams import pooled_ms1_streams, scan_streams, stream_report


class _ScriptedReader:
    """Reader stand-in: one ``(filter, trailer)`` per scan, a second apart."""

    def __init__(self, scans):
        self._scans = scans

    def scan_filters(self):
        return [
            {"scan": n, "time_s": float(n - 1), "filter": text}
            for n, (text, _trailer) in enumerate(self._scans, start=1)
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
