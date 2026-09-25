"""
Unit tests for the scan-stream census backfill script.

The script decides four things: which files still need a census, which of
those are worth opening, what to do with each answer the reader gives, and how
many files one run may read. All four are tested with stand-ins, and the read
itself is tested against the committed KORBI file. Everything here stays
hermetic - no server, no database.
"""

import os
from pathlib import Path

import pytest
from test_utils import captured_logs

from mascope_backend.db.scripts import backfill_scan_stream_census as script
from mascope_backend.method_keys import signature_class


_REPO_ROOT = Path(__file__).resolve().parents[5]
KORBI_POS = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)

# "+" and "-", as ScanFilter.signature and ionization_mode_polarity both
# spell it. A census never contains the word "positive", so a fixture that
# used it would pin agreement with the binding backfill on values real data
# does not hold.
A_CENSUS = [
    {
        "key": "FTMS + p ESI Full ms [50.0000-750.0000]",
        "signature": {"ms_order": 1, "polarity": "+"},
        "scans": 120,
    }
]

#: A census of a shape read_scan_streams drops: no signature.
A_MALFORMED_CENSUS = [{"key": "FTMS + p ESI Full ms [50.0000-750.0000]"}]


def _candidates(*names: str) -> list[dict]:
    return [
        {"sample_file_id": f"sf{i}", "filename": n, "routed": True}
        for i, n in enumerate(names)
    ]


def _always(value):
    return lambda *_args: value


# --- the entry point --------------------------------------------------------


def test_the_script_can_be_run():
    """`mascope db script run` finds a script by its callable main().

    Without one the dev runner reports "Unknown script" and the prod runner
    imports the module, exits 0 and writes nothing - a script that reports
    success and does nothing. No test that calls the internals notices.
    """
    assert callable(script.main)


# --- which files still need a census ----------------------------------------


def test_a_file_converted_before_the_census_existed_needs_one():
    assert script.needs_census({"range": [50, 750]})


def test_an_empty_census_is_retried_rather_than_taken_as_an_answer():
    """A conversion whose reader could not supply one, worth reopening."""
    assert script.needs_census({"scan_streams": []})


def test_a_census_of_the_wrong_shape_needs_one():
    assert script.needs_census({"scan_streams": "not a list"})


def test_a_file_that_already_has_a_census_is_left_alone():
    assert not script.needs_census({"scan_streams": A_CENSUS})


def test_the_script_fills_exactly_what_the_binding_backfill_skips():
    """The two scripts have to agree on what "no census" means.

    ``backfill_method_bindings`` skips a census-bearing file whose signature
    class comes out None. This one exists to make that set empty, so anything
    it declines to fill would be skipped there forever. Both judge the shape
    through ``usable_streams``, so the agreement holds by construction; this
    pins it anyway, including the entry that has no signature - the case the
    two disagreed on while this script had its own shape check.
    """
    for streams in ([], "not a list", A_MALFORMED_CENSUS):
        assert script.needs_census({"scan_streams": streams})
        assert (
            signature_class(streams if isinstance(streams, list) else [], "+", "orbi")
            is None
        )
    assert script.needs_census({})

    assert not script.needs_census({"scan_streams": A_CENSUS})
    assert (
        signature_class(A_CENSUS, "+", "orbi")
        == "FTMS + p ESI Full ms [50.0000-750.0000]"
    )


# --- which of those are worth opening ---------------------------------------


@pytest.mark.asyncio
async def test_the_survey_keeps_the_candidate_order():
    """A bounded run reads the head of this list, so its order is the policy."""
    pending, counts = await script._survey(
        _candidates("c.raw", "b.raw", "a.raw"),
        _always({"range": [50, 750]}),
        _always(True),
    )
    assert pending == ["c.raw", "b.raw", "a.raw"]
    assert counts["surveyed"] == 3


@pytest.mark.asyncio
async def test_the_survey_counts_what_it_does_not_return():
    pending, counts = await script._survey(
        _candidates("done.raw", "todo.raw", "gone.raw", "tried.raw", "noraw.raw"),
        {
            "done.raw": {"scan_streams": A_CENSUS},
            "todo.raw": {"range": [50, 750]},
            "gone.raw": None,
            "tried.raw": {"scan_streams_backfill": {"result": "empty"}},
            "noraw.raw": {"range": [50, 750]},
        }.get,
        lambda name: name != "noraw.raw",
    )
    assert pending == ["todo.raw"]
    assert counts["has_census"] == 1
    assert counts["no_props"] == 1
    assert counts["already_tried"] == 1
    assert counts["no_raw"] == 1


@pytest.mark.asyncio
async def test_a_file_whose_raw_data_was_cleared_is_left_out_without_opening_it():
    """Otherwise it sits at the head of pending on every run.

    Newest-first ordering puts such files back in the same place each time, so
    a capped run would spend its whole budget rediscovering them and never
    reach a file it could fill.
    """
    opened = []

    def has_raw(filename):
        opened.append(filename)
        return False

    pending, counts = await script._survey(
        _candidates("gone.raw"), _always({"range": [50, 750]}), has_raw
    )

    assert pending == []
    assert counts["no_raw"] == 1
    assert opened == ["gone.raw"], "the cheap check, not a reader open"


@pytest.mark.asyncio
async def test_a_file_tried_before_is_skipped_until_the_retry_flag():
    tried = {"scan_streams_backfill": {"at": "2026-09-25T00:00:00Z", "result": "empty"}}

    pending, counts = await script._survey(
        _candidates("tried.raw"), _always(tried), _always(True)
    )
    assert pending == []
    assert counts["already_tried"] == 1

    pending, counts = await script._survey(
        _candidates("tried.raw"), _always(tried), _always(True), retry=True
    )
    assert pending == ["tried.raw"]
    assert counts["already_tried"] == 0


@pytest.mark.asyncio
async def test_the_survey_stops_at_the_cap():
    """Otherwise repeated capped runs re-read every props on the server.

    Thirty capped runs over a filestore of 150k files would be 4.5M JSON
    loads to fill 150k censuses. The stop is between batches, so a run reads
    at most one batch more than it needs.
    """
    read = []

    def read_props(filename):
        read.append(filename)
        return {"range": [50, 750]}

    total = script._SURVEY_BATCH * 3
    pending, counts = await script._survey(
        _candidates(*[f"f{i}.raw" for i in range(total)]),
        read_props,
        _always(True),
        limit=5,
    )

    assert pending == [f"f{i}.raw" for i in range(5)]
    # The cap is honoured between batches, so a run reads at most one batch
    # more than it needs - not every props on the server.
    assert counts["surveyed"] == script._SURVEY_BATCH
    assert len(read) == script._SURVEY_BATCH


# --- what to do with each answer --------------------------------------------


def _recorder():
    written: dict[str, list[dict]] = {}
    attempts: dict[str, str] = {}

    def write(filename, streams):
        written[filename] = streams

    def write_attempt(filename, result):
        attempts[filename] = result

    return written, attempts, write, write_attempt


@pytest.mark.asyncio
async def test_each_readable_file_gets_its_census():
    written, attempts, write, write_attempt = _recorder()

    counts = await script._fill(
        ["a.raw", "b.raw"], _always((A_CENSUS, None, None)), write, write_attempt
    )

    assert written == {"a.raw": A_CENSUS, "b.raw": A_CENSUS}
    assert attempts == {}
    assert counts["written"] == 2


@pytest.mark.asyncio
async def test_missing_raw_data_and_a_reader_failure_are_counted_apart():
    """A reader regression must not read as cleared raw data.

    "could not read 40000 files" is what a backend bug and an emptied archive
    both look like, and only one of them is something to fix.
    """
    written, attempts, write, write_attempt = _recorder()

    counts = await script._fill(
        ["gone.raw", "bad.raw"],
        {
            "gone.raw": (None, "no_raw", None),
            "bad.raw": (None, "reader", ValueError("the reader said no")),
        }.get,
        write,
        write_attempt,
    )

    assert written == {}
    assert counts["no_raw"] == 1
    assert counts["reader"] == 1
    # The reader failure is worth not retrying every run; a file with no raw
    # data is left out by the survey anyway, so it keeps no marker.
    assert attempts == {"bad.raw": "reader"}


@pytest.mark.asyncio
async def test_a_file_reporting_no_streams_is_not_recorded_as_answered():
    """An empty census must not be written.

    Writing it would make the next run skip the file, which is the opposite of
    what an empty answer from a census-bearing instrument deserves - it means
    the reader could not supply one, not that the file holds no scans. The
    attempt is recorded instead, which is what moves a capped run past it.
    """
    written, attempts, write, write_attempt = _recorder()

    counts = await script._fill(
        ["odd.raw"], _always(([], None, None)), write, write_attempt
    )

    assert written == {}
    assert counts["empty"] == 1
    assert attempts == {"odd.raw": "empty"}
    assert script.needs_census({"scan_streams": []})


@pytest.mark.asyncio
async def test_one_failed_write_does_not_end_the_run():
    """A sample deleted after the survey read its props is enough.

    update_props raises FileNotFoundError on its own read, and the run would
    stop with a traceback and no summary - after hours of reads.
    """
    written, attempts, _write, write_attempt = _recorder()

    def write(filename, streams):
        if filename == "gone.raw":
            raise FileNotFoundError(filename)
        written[filename] = streams

    counts = await script._fill(
        ["a.raw", "gone.raw", "b.raw"],
        _always((A_CENSUS, None, None)),
        write,
        write_attempt,
    )

    assert set(written) == {"a.raw", "b.raw"}
    assert counts["written"] == 2
    assert counts["unwritable"] == 1


@pytest.mark.asyncio
async def test_the_readers_own_error_reaches_the_log():
    """The exception has to travel, not be looked up.

    It is caught in a worker thread, so ``opt(exception=True)`` in the event
    loop finds nothing - and this backend's formatter does not merely print
    "NoneType: None" for an empty record, it raises inside the handler and the
    line is lost altogether.
    """
    written, attempts, write, write_attempt = _recorder()
    cause = ValueError("the reader said no")

    with captured_logs() as records:
        await script._fill(
            ["bad.raw"], _always((None, "reader", cause)), write, write_attempt
        )

    refused = [r for r in records if "bad.raw" in r["message"]]
    assert refused, "the failure is reported"
    assert refused[0]["exception"] is not None, "with the reader's own error"
    assert refused[0]["exception"].value is cause


@pytest.mark.asyncio
async def test_writing_a_census_clears_an_earlier_failed_attempt():
    """Otherwise a retried file keeps a note saying the reader refused it."""
    updates = {}

    def update_props(filename, fields):
        updates[filename] = fields

    original = script.m_io.update_props
    script.m_io.update_props = update_props
    try:
        script._write_census("retried.raw", A_CENSUS)
    finally:
        script.m_io.update_props = original

    assert updates["retried.raw"][script.CENSUS_FIELD] == A_CENSUS
    assert updates["retried.raw"][script.ATTEMPT_FIELD] is None


# --- the bound --------------------------------------------------------------


def test_no_limit_by_default(monkeypatch):
    monkeypatch.delenv("CENSUS_LIMIT", raising=False)
    assert script._limit() is None


def test_the_limit_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("CENSUS_LIMIT", "5000")
    assert script._limit() == 5000


@pytest.mark.parametrize("value", ["0", "-1", "lots", "5k", "5e3"])
def test_a_limit_that_is_not_a_positive_number_is_refused(monkeypatch, value):
    """Refused, not ignored.

    An operator who set the variable wanted a bounded run. Falling back to
    reading every raw file on the server is the expensive way to answer a
    typo, and the one they were trying to avoid.
    """
    monkeypatch.setenv("CENSUS_LIMIT", value)
    with pytest.raises(script.BadLimit):
        script._limit()


# --- the read itself --------------------------------------------------------


@pytest.mark.skipif(not KORBI_POS.exists(), reason="KORBI test file not present")
def test_the_census_of_a_real_file_is_read_from_its_raw_data(monkeypatch):
    monkeypatch.setattr(
        script.m_name, "filename_to_datafile_path", lambda name: str(KORBI_POS)
    )

    streams, reason, error = script._read_census("KORBI2_AMB_POS_20260109174345.raw")

    assert reason is None
    assert error is None
    assert streams, "the committed file holds scans, so it holds a stream"
    assert all(s["key"] for s in streams)
    assert any(s["signature"]["ms_order"] == 1 for s in streams)
    # What the whole exercise is for: a class the binding can key on.
    polarity = streams[0]["signature"]["polarity"]
    assert signature_class(streams, polarity, "orbi")


def test_a_missing_raw_file_answers_no_raw(monkeypatch):
    monkeypatch.setattr(
        script.m_name,
        "filename_to_datafile_path",
        lambda name: os.path.join("no", "such", "data.raw"),
    )

    with captured_logs() as records:
        streams, reason, error = script._read_census("missing.raw")

    assert streams is None
    assert reason == "no_raw"
    assert error is None
    # Nothing at WARNING: a server whose older raw data has been cleared would
    # otherwise open one monitoring issue per file.
    assert [r["message"] for r in records if r["level"].name == "WARNING"] == []


def test_a_sample_with_no_raw_data_is_not_offered(monkeypatch):
    monkeypatch.setattr(script.m_name, "get_sample_file_type", lambda name: "orbi_zarr")
    assert not script._has_raw("archived.raw")

    monkeypatch.setattr(script.m_name, "get_sample_file_type", lambda name: "orbi_raw")
    assert script._has_raw("kept.raw")
