"""
Unit tests for the sample file processing status helpers.

The status a stage records carries a detail for a person. For a file whose
MS1 scans of one polarity come from more than one scan stream, the detail
says that peak detection pools them, read from the converter's census in the
file's ``.props``. Writing the status is best effort: a failure to write it
must never fail the processing it reports on.
"""

from unittest.mock import AsyncMock, patch

import pytest
from test_utils import captured_logs

from mascope_backend.api.controllers.sample.files.process import status
from mascope_backend.api.models.sample.files.config import (
    IN_PROGRESS,
    ProcessingStatus,
)


def _stream(key: str, polarity: str, ms_order: int = 1) -> dict:
    return {"key": key, "signature": {"polarity": polarity, "ms_order": ms_order}}


LOW = "FTMS - p NSI Full ms [40.0000-160.0000] R=120000"
HIGH = "FTMS - p NSI Full ms [128.0000-600.0000] R=120000"


# ---------------------------------------------------------------------------
# The pooled-streams note
# ---------------------------------------------------------------------------


def test_one_stream_per_polarity_pools_nothing():
    streams = [_stream(LOW, "-"), _stream(HIGH.replace("- p", "+ p"), "+")]

    assert status.pooled_streams_note(streams) is None


def test_two_ms1_streams_in_a_polarity_are_named():
    note = status.pooled_streams_note([_stream(LOW, "-"), _stream(HIGH, "-")])

    assert note == (
        f"Polarity - pools 2 MS1 scan streams into one peak list: {LOW}; {HIGH}."
    )


def test_fragmentation_streams_are_not_pooled_with_ms1():
    """Only MS1 scans reach the peak list."""
    streams = [
        _stream(LOW, "-"),
        _stream("FTMS - c NSI d Full ms2 188.93@hcd30", "-", 2),
    ]

    assert status.pooled_streams_note(streams) is None


def test_each_pooled_polarity_gets_its_own_sentence():
    streams = [
        _stream(LOW, "-"),
        _stream(HIGH, "-"),
        _stream("pos low", "+"),
        _stream("pos high", "+"),
    ]

    note = status.pooled_streams_note(streams)

    assert note.count("pools 2 MS1 scan streams") == 2
    assert "Polarity + pools" in note and "Polarity - pools" in note


def test_an_empty_census_pools_nothing():
    """TofDaq files and files converted before the census have none."""
    assert status.pooled_streams_note([]) is None


def test_streams_the_store_stitched_are_not_called_pooled():
    """A file whose peaks were detected per stream and stitched: the detail
    says so, not the opposite."""
    note = status.pooled_streams_note(
        [_stream(LOW, "-"), _stream(HIGH, "-")], stitched=[LOW, HIGH]
    )

    # Each by its scan range, as the sample's views name a segment
    assert note == (
        "Polarity - stitches 2 MS1 scan streams into one spectrum: "
        "m/z 40-160; m/z 128-600."
    )


def test_pooled_streams_are_still_named_by_their_whole_key():
    """Nothing else says which experiments a pooled peak list mixes."""
    note = status.pooled_streams_note([_stream(LOW, "-"), _stream(HIGH, "-")])

    assert note == (
        f"Polarity - pools 2 MS1 scan streams into one peak list: {LOW}; {HIGH}."
    )


def test_a_polarity_is_only_stitched_when_every_stream_of_it_is():
    """Where the store does not hold a list for each stream the census
    names, the polarity is described as it is safest to read it."""
    note = status.pooled_streams_note(
        [_stream(LOW, "-"), _stream(HIGH, "-")], stitched=[LOW]
    )

    assert note.startswith("Polarity - pools 2 MS1 scan streams")


def test_each_polarity_says_what_was_done_with_its_own_streams():
    streams = [
        _stream(LOW, "-"),
        _stream(HIGH, "-"),
        _stream("pos low", "+"),
        _stream("pos high", "+"),
    ]

    note = status.pooled_streams_note(streams, stitched=[LOW, HIGH])

    assert "Polarity - stitches 2 MS1 scan streams into one spectrum" in note
    assert "Polarity + pools 2 MS1 scan streams into one peak list" in note


def _store(keys, stitched=True):
    """What the signal library reads off a peak store with these keys."""
    from mascope_signal.compute import StalePeakStoreError

    def _map(_store):
        if not stitched:
            raise StalePeakStoreError("no map")
        return {"rule": 1}

    return (
        patch.object(status.m_io, "load_array", return_value=object()),
        patch.object(status.m_compute, "peak_store_streams", return_value=keys),
        patch.object(status.m_compute, "peak_store_stitch_map", side_effect=_map),
    )


@pytest.mark.asyncio
async def test_the_streams_a_store_stitched_are_read_off_the_store():
    opened, keys, stitch = _store([LOW, HIGH])
    with opened, keys, stitch:
        assert await status.read_store_stream_keys("x.raw") == [LOW, HIGH]


@pytest.mark.asyncio
async def test_a_pooled_store_names_no_stitched_streams():
    opened, keys, stitch = _store([])
    with opened, keys, stitch:
        assert await status.read_store_stream_keys("x.raw") == []


@pytest.mark.asyncio
async def test_a_per_stream_store_without_a_map_is_not_stitched():
    opened, keys, stitch = _store([LOW, HIGH], stitched=False)
    with opened, keys, stitch:
        assert await status.read_store_stream_keys("x.raw") == []


@pytest.mark.asyncio
async def test_a_file_with_no_store_names_no_stitched_streams():
    """Nothing that reads this may cost a file its processing."""
    with patch.object(status.m_io, "load_array", side_effect=FileNotFoundError("none")):
        assert await status.read_store_stream_keys("x.raw") == []


@pytest.mark.asyncio
async def test_the_note_knows_a_stitched_file_from_its_store():
    props = {"scan_streams": [_stream(LOW, "-"), _stream(HIGH, "-")]}
    opened, keys, stitch = _store([LOW, HIGH])

    with patch.object(status, "read_props", return_value=props), opened, keys, stitch:
        note = await status.read_pooled_streams_note("Orbi_2026.09.21_x.raw")

    assert note.startswith("Polarity - stitches 2 MS1 scan streams")


@pytest.mark.asyncio
async def test_the_note_is_read_from_the_files_props():
    props = {"scan_streams": [_stream(LOW, "-"), _stream(HIGH, "-")]}

    with patch.object(status, "read_props", return_value=props) as read:
        note = await status.read_pooled_streams_note("Orbi_2026.09.21_x.raw")

    read.assert_called_once_with("Orbi_2026.09.21_x.raw")
    assert note.startswith("Polarity - pools 2 MS1 scan streams")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [FileNotFoundError("no .props"), ValueError("not JSON"), KeyError("x")]
)
async def test_a_census_that_cannot_be_read_reports_nothing(failure):
    with patch.object(status, "read_props", side_effect=failure):
        assert await status.read_pooled_streams_note("x.raw") is None


@pytest.mark.asyncio
async def test_props_without_a_census_report_nothing():
    with patch.object(status, "read_props", return_value={"polarity": "-"}):
        assert await status.read_pooled_streams_note("x.raw") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("census", [["not a stream"], "not a list of streams"])
async def test_a_census_of_the_wrong_shape_reports_nothing(census):
    """Registration reads the note too, and must not fail on it."""
    with patch.object(status, "read_props", return_value={"scan_streams": census}):
        assert await status.read_pooled_streams_note("x.raw") is None


@pytest.mark.asyncio
async def test_the_census_read_drops_what_is_not_a_stream():
    """Every caller walks the census without guarding each field."""
    good = {"key": "FTMS - p NSI Full ms", "signature": {"ms_order": 1}}
    with patch.object(
        status, "read_props", return_value={"scan_streams": [good, "junk", None]}
    ):
        assert await status.read_scan_streams("x.raw") == [good]


@pytest.mark.asyncio
async def test_an_unreadable_props_is_told_apart_from_no_census():
    """None is a fault worth reporting; [] is an ordinary pre-census file.

    Every Orbitrap file predating the census records none, and those get
    re-processed in bulk, so collapsing the two made the caller warn about a
    `.props` that was perfectly fine.
    """
    with patch.object(status, "read_props", side_effect=OSError("gone")):
        assert await status.read_scan_streams("x.raw") is None
    with patch.object(status, "read_props", return_value={"polarity": "-"}):
        assert await status.read_scan_streams("x.raw") == []


@pytest.mark.asyncio
async def test_the_note_still_reads_an_unreadable_props_as_nothing():
    """Registration reads it too, and must not fail on it."""
    with patch.object(status, "read_props", side_effect=OSError("gone")):
        assert await status.read_pooled_streams_note("x.raw") is None


# ---------------------------------------------------------------------------
# The detail
# ---------------------------------------------------------------------------


def test_a_detail_joins_the_sentences_it_has():
    assert status.compose_detail("Matched 1 sample.", None, " ", "Note.") == (
        "Matched 1 sample. Note."
    )


def test_a_detail_with_nothing_to_say_is_none():
    assert status.compose_detail(None, "", "  ") is None


def test_a_long_detail_is_clipped():
    detail = status.compose_detail("x" * 5000)

    assert len(detail) == 1000
    assert detail.endswith("...")


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_in_progress_statuses_are_the_ones_a_run_passes_through():
    """Every other status ends a run; a restart must never reset those."""
    assert IN_PROGRESS == {
        ProcessingStatus.CONVERTED,
        ProcessingStatus.QUEUED,
        ProcessingStatus.BOUND,
        ProcessingStatus.CALIBRATED,
    }


def test_statuses_fit_their_column():
    """``sample_file.processing_status`` is a String(24)."""
    assert all(len(value) <= 24 for value in ProcessingStatus)


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("no engine"), OSError("gone")])
async def test_a_status_that_cannot_be_written_is_logged_not_raised(failure):
    """The file is named at INFO and the WARNING names none.

    Error monitoring groups issues by the message, and an outage fails this
    write for every file in flight: one issue, not one per file.
    """
    emit = AsyncMock()
    with (
        patch.object(status, "async_session", side_effect=failure),
        patch.object(status, "emit_record_updated", emit),
        captured_logs() as records,
    ):
        await status.record_processing_status("sf-1", ProcessingStatus.DONE, "Done.")

    emit.assert_not_called()
    lines = [(r["level"].name, r["message"]) for r in records]
    warnings = [message for level, message in lines if level == "WARNING"]
    assert len(warnings) == 1 and "'done'" in warnings[0], lines
    assert "sf-1" not in warnings[0], lines
    assert any(level == "INFO" and "sf-1" in message for level, message in lines)


# -- what a stitched file's ranges read where they overlap --------------------------

REAGENT_KEY = "FTMS - p NSI Full ms [40.0000-138.0000] R=120000"
LOW_KEY = "FTMS - p NSI Full ms [66.0000-124.0000] R=120000"
MID_KEY = "FTMS - p NSI Full ms [132.0000-460.0000] R=120000"
HIGH_KEY = "FTMS - p NSI Full ms [440.0000-1200.0000] R=120000"
STORE_KEYS = [REAGENT_KEY, LOW_KEY, MID_KEY, HIGH_KEY]


def _overlap(first, second, shared, ppm=None, ratio=None):
    """An overlap reading as the store records it: quartiles over the ions
    two ranges share, None where they share none."""
    return {
        "streams": [first, second],
        "shared": shared,
        "ppm": None if ppm is None else [ppm - 0.2, ppm, ppm + 0.2],
        "ratio": None if ratio is None else [ratio * 0.9, ratio, ratio * 1.1],
    }


def test_an_overlap_says_how_far_apart_and_how_high_its_two_ranges_read():
    note = status.overlap_readings_note(
        STORE_KEYS,
        [_overlap(0, 1, 26, -0.417, 1.37), _overlap(2, 3, 33, 0.611, 1.25)],
    )

    assert note == (
        "Where two scan ranges overlap: m/z 66-124 reads the 26 ions it shares "
        "with m/z 40-138 0.42 ppm lower, at 1.37 times the intensity; m/z 440-1200 "
        "reads the 33 ions it shares with m/z 132-460 0.61 ppm higher, at 1.25 "
        "times the intensity."
    )


def test_the_reading_is_the_median_over_the_ions_shared():
    reading = {
        "streams": [0, 1],
        "shared": 3,
        "ppm": [-9.0, 0.5, 9.0],
        "ratio": [0.1, 2.0, 30.0],
    }

    note = status.overlap_readings_note(STORE_KEYS, [reading])

    assert "0.50 ppm higher, at 2.00 times" in note


def test_one_shared_ion_is_one_ion():
    note = status.overlap_readings_note(STORE_KEYS, [_overlap(2, 3, 1, 0.4, 0.8)])

    assert "reads the 1 ion it shares with m/z 132-460" in note


def test_two_ranges_that_share_no_ion_say_nothing():
    """The reagent scan and the mid window overlap over two m/z and hold no
    ion in common: there is nothing to read."""
    readings = [_overlap(0, 2, 0), _overlap(0, 1, 26, -0.4, 1.4)]

    note = status.overlap_readings_note(STORE_KEYS, readings)

    assert "m/z 132-460" not in note
    assert note.count("reads the") == 1


def test_a_file_whose_ranges_share_nothing_has_no_overlap_note():
    assert status.overlap_readings_note(STORE_KEYS, [_overlap(0, 2, 0)]) is None
    assert status.overlap_readings_note(STORE_KEYS, []) is None


def _stitched_store(readings, keys=STORE_KEYS):
    """What the signal library reads off a stitched store with these readings."""
    from types import SimpleNamespace

    store = SimpleNamespace(attrs={"stitch_overlaps": readings})
    return (
        patch.object(status.m_io, "load_array", return_value=store),
        patch.object(status.m_compute, "peak_store_streams", return_value=keys),
    )


@pytest.mark.asyncio
async def test_the_overlap_readings_are_read_off_the_store():
    opened, keys = _stitched_store([_overlap(0, 1, 26, -0.417, 1.37)])
    with opened, keys:
        note = await status.read_overlap_readings_note("x.raw")

    assert note.startswith("Where two scan ranges overlap: m/z 66-124 reads the 26")


@pytest.mark.asyncio
async def test_a_pooled_store_has_no_overlap_readings():
    """Whatever its attributes hold: it has no streams to read against each
    other."""
    opened, keys = _stitched_store([_overlap(0, 1, 26, -0.4, 1.4)], keys=[])
    with opened, keys:
        assert await status.read_overlap_readings_note("x.raw") is None


@pytest.mark.asyncio
async def test_a_stitched_store_that_recorded_no_readings_has_none():
    from types import SimpleNamespace

    with (
        patch.object(status.m_io, "load_array", return_value=SimpleNamespace(attrs={})),
        patch.object(status.m_compute, "peak_store_streams", return_value=STORE_KEYS),
        captured_logs() as records,
    ):
        assert await status.read_overlap_readings_note("x.raw") is None

    # Nothing went wrong: a store with nothing to read is not a failure
    assert not [r["message"] for r in records if "overlap readings" in r["message"]]


@pytest.mark.asyncio
async def test_a_store_that_cannot_be_read_says_so_quietly():
    with (
        patch.object(status.m_io, "load_array", side_effect=OSError("disk")),
        captured_logs() as records,
    ):
        assert await status.read_overlap_readings_note("x.raw") is None

    lines = [(r["level"].name, r["message"]) for r in records]
    assert [level for level, message in lines if "overlap readings" in message] == [
        "DEBUG"
    ], lines


@pytest.mark.asyncio
async def test_a_file_with_no_store_has_no_overlap_readings():
    with patch.object(status.m_io, "load_array", side_effect=FileNotFoundError("x")):
        assert await status.read_overlap_readings_note("x.raw") is None


@pytest.mark.asyncio
async def test_a_stored_files_note_says_its_streams_and_then_what_they_read():
    streams = [_stream(REAGENT_KEY, "-"), _stream(LOW_KEY, "-")]
    with (
        patch.object(status, "read_scan_streams", AsyncMock(return_value=streams)),
        patch.object(
            status,
            "read_store_stream_keys",
            AsyncMock(return_value=[REAGENT_KEY, LOW_KEY]),
        ),
        patch.object(
            status,
            "read_overlap_readings_note",
            AsyncMock(return_value="Where two scan ranges overlap: A."),
        ),
    ):
        note = await status.read_pooled_streams_note("x.raw")

    assert note.startswith("Polarity - stitches 2 MS1 scan streams into one spectrum")
    assert note.endswith(" Where two scan ranges overlap: A.")


# -- which ranges run on a neighbour's calibration -----------------------------------


def _segment(label, source, origin=None, shared=None, note=None):
    return {
        "label": label,
        "source": source,
        "origin": origin,
        "shared_ions": shared,
        "note": note,
    }


def _calibrated(*segments):
    return {"status": "ok", "verified": True, "quality": {"segments": list(segments)}}


def test_a_range_calibrated_across_an_overlap_says_from_which_and_over_how_many():
    note = status.carried_calibration_note(
        _calibrated(
            _segment("m/z 40-138", "anchors"),
            _segment("m/z 66-124", "overlap", "m/z 40-138", 26),
        )
    )

    assert note == (
        "m/z 66-124 has no fit of its own: calibrated from m/z 40-138 across the "
        "26 ions both measure."
    )


def test_a_range_given_a_neighbours_calibration_says_whose():
    note = status.carried_calibration_note(
        _calibrated(
            _segment("m/z 40-138", "anchors"),
            _segment("m/z 132-460", "borrowed", "m/z 40-138"),
        )
    )

    assert note == (
        "m/z 132-460 has no fit of its own: given the calibration of m/z 40-138 "
        "as it is."
    )


def test_every_range_without_a_calibrant_is_named():
    note = status.carried_calibration_note(
        _calibrated(
            _segment("m/z 40-138", "anchors"),
            _segment("m/z 66-124", "overlap", "m/z 40-138", 1),
            _segment("m/z 132-460", "borrowed", "m/z 40-138"),
        )
    )

    assert note.count("has no fit of its own") == 2
    assert "across the 1 ion both measure." in note
    assert "m/z 40-138 has" not in note


def test_the_reason_the_fit_recorded_for_a_range_is_quoted():
    """A range with no fit of its own need not lack calibrants: the ones it
    holds can have disagreed. Which it was is the fit's to say, so that a
    site is not sent to extend a collection that reaches the window."""
    note = status.carried_calibration_note(
        _calibrated(
            _segment(
                "m/z 66-124",
                "overlap",
                "m/z 40-138",
                26,
                note="No calibration peaks found",
            ),
            _segment(
                "m/z 132-460",
                "borrowed",
                "m/z 40-138",
                note="No suitable subset of calibration peaks found; skipping calibration.",
            ),
        )
    )

    assert note == (
        "m/z 66-124 has no fit of its own (no calibration peaks found): calibrated "
        "from m/z 40-138 across the 26 ions both measure. m/z 132-460 has no fit of "
        "its own (no suitable subset of calibration peaks found; skipping "
        "calibration): given the calibration of m/z 40-138 as it is."
    )


def test_a_stitched_files_whole_detail_fits_the_bound():
    """Four ranges, the three overlaps a layout can have, a fit below the
    bar and every analyte window on a neighbour's calibration: what a file
    of the settled layout can have to say at once. Named by their keys, the
    ranges alone took a quarter of the bound and the readings fell off the
    end."""
    streams = [_stream(key, "-") for key in STORE_KEYS]
    carried = status.carried_calibration_note(
        _calibrated(
            _segment("m/z 40-138", "anchors"),
            *(
                _segment(
                    label, "overlap", "m/z 40-138", 26, "No calibration peaks found"
                )
                for label in ("m/z 66-124", "m/z 132-460", "m/z 440-1200")
            ),
        )
    )
    detail = status.compose_detail(
        "Matched 1 sample.",
        "The m/z calibration is below the quality bar: m/z 40-138: Mean m/z error "
        "after calibration is 1.27 ppm (limit 1 ppm).",
        carried,
        status.pooled_streams_note(streams, stitched=STORE_KEYS),
        status.overlap_readings_note(
            STORE_KEYS,
            [
                _overlap(0, 1, 26, -0.417, 1.37),
                _overlap(0, 2, 4, 0.2, 0.9),
                _overlap(2, 3, 33, 0.611, 1.25),
            ],
        ),
    )

    assert len(detail) < status._DETAIL_LIMIT
    assert not detail.endswith("...")
    assert detail.endswith("0.61 ppm higher, at 1.25 times the intensity.")
    assert (
        "Polarity - stitches 4 MS1 scan streams into one spectrum: m/z 40-138; "
        "m/z 66-124; m/z 132-460; m/z 440-1200."
    ) in detail


@pytest.mark.parametrize(
    "record",
    [
        None,
        {"status": "failed"},
        {"status": "ok", "quality": {"n_points": 3}},
        {"status": "ok", "quality": None},
        {
            "status": "ok",
            "quality": {"segments": [{"label": "a", "source": "anchors"}]},
        },
    ],
)
def test_a_file_whose_ranges_all_hold_calibrants_has_nothing_to_say(record):
    assert status.carried_calibration_note(record) is None
