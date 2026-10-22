"""The converter runs over each shape of the hermetic MS2 fixture end to end.

The fixture (``tests/ms2_fixture.py``) reads the committed MS1 file as three
MS2 acquisitions: manual, interleaved data-dependent and stepped-energy.
Each holds survey scans, so each takes the ordinary path: the properties and
the scan-stream census are extracted, the filestore directory is made with
the file copied into it, the instrument functions are fitted and the peaks
detected on the survey scans, and the record is asked for. Only the two
calls to the server are answered by the fixture.

What the conversion must show of the fragmentation scans is in the census:
one stream per targeted precursor and energy, one folded stream for the
data-dependent scans, each with the scans the shape gives it
(``tests/ms2_fixture.json``). And the stored copy reads as the shape, so
what reads the store afterwards reads the fixture's answers.
"""

import json

import pytest
from ms2_fixture import SHAPES, answers, converted, scan_time_s

import mascope_thermo.thermo as m_thermo


ANSWERS = answers()


@pytest.fixture(scope="module", params=SHAPES)
def conversion(request, tmp_path_factory):
    filestore = tmp_path_factory.mktemp(f"filestore_{request.param}")
    with converted(request.param, filestore) as done:
        yield done, ANSWERS["shapes"][request.param]


def test_it_is_stored_with_its_peaks_and_instrument_functions(conversion):
    done, _ = conversion

    stored = {path.name for path in done.sample_dir.iterdir()}

    assert {".props", "data.raw", "peak_timeseries.zarr"} <= stored
    ((props, instrument_function), *_) = done.records
    assert len(done.records) == 1
    assert props.filename == done.props.filename
    assert instrument_function == "instrument-config"


def test_its_census_shows_the_fragmentation_streams(conversion):
    done, shape = conversion

    streams = [
        {
            "key": s["key"],
            "ms_order": s["signature"]["ms_order"],
            "scans": s["scans"],
            "blocks": s["blocks"],
            "filters": s["filters"],
            **({"scan_event": s["scan_event"]} if "scan_event" in s else {}),
        }
        for s in done.props.scan_streams
    ]

    assert streams == shape["answers"]["streams"]
    fragment_scans = sum(s["scans"] for s in streams if s["ms_order"] == 2)
    assert fragment_scans == shape["answers"]["ms2_scan_count"]


def test_its_census_is_what_the_store_records(conversion):
    done, _ = conversion

    stored = json.loads((done.sample_dir / ".props").read_text())

    assert stored["scan_streams"] == json.loads(
        json.dumps(done.props.model_dump(by_alias=True)["scan_streams"])
    )


def test_its_length_runs_to_its_last_scan(conversion):
    done, shape = conversion

    assert done.props.length == pytest.approx(scan_time_s(len(shape["scans"])))
    assert done.props.interval == pytest.approx(scan_time_s(1))
    assert done.props.polarity == "+"


def test_the_stored_copy_reads_as_the_shape(conversion):
    done, shape = conversion

    summary = m_thermo.get_ms2_summary_metadata(str(done.sample_dir / "data.raw"))

    assert summary["ms1_scan_count"] == shape["answers"]["ms1_scan_count"]
    assert summary["ms2_scan_count"] == shape["answers"]["ms2_scan_count"]
    assert summary["isolation_width"] == shape["answers"]["isolation_width"]
    assert [
        (g["parent_peak_mz"], g["activation"], g["scan_count"], g["isolation_width"])
        for g in summary["groups"]
    ] == [
        (g["parent_peak_mz"], g["activation"], g["scan_count"], g["isolation_width"])
        for g in shape["answers"]["groups"]
    ]
