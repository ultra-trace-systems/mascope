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
async def test_the_census_read_reports_nothing_when_the_props_cannot_be_read():
    with patch.object(status, "read_props", side_effect=OSError("gone")):
        assert await status.read_scan_streams("x.raw") == []


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
