"""
Unit tests for the scan-stream census backfill script.

The script decides three things: which files carry no census, what to do with
each answer the reader gives, and how many files one run may read. All three
are tested with stand-ins for the reader and the props, and the read itself is
tested once against the committed KORBI file. Everything here stays hermetic -
no server, no database.
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

A_CENSUS = [
    {
        "key": "FTMS + p ESI Full ms [50.0000-750.0000]",
        "signature": {"ms_order": 1, "polarity": "positive"},
        "scans": 120,
    }
]


def _candidates(*names: str) -> list[dict]:
    return [{"sample_file_id": f"sf{i}", "filename": n} for i, n in enumerate(names)]


# --- which files carry no census -------------------------------------------


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
    it declines to fill would be skipped there forever.
    """
    for props in ({}, {"scan_streams": []}, {"scan_streams": "not a list"}):
        streams = props.get("scan_streams")
        assert script.needs_census(props)
        assert (
            signature_class(
                streams if isinstance(streams, list) else [], "positive", "orbi"
            )
            is None
        )

    assert not script.needs_census({"scan_streams": A_CENSUS})
    assert (
        signature_class(A_CENSUS, "positive", "orbi")
        == "FTMS + p ESI Full ms [50.0000-750.0000]"
    )


# --- the survey -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_survey_keeps_the_candidate_order():
    """A bounded run reads the head of this list, so its order is the policy."""
    pending, counts = await script._survey(
        _candidates("c.raw", "b.raw", "a.raw"),
        lambda name: {"range": [50, 750]},
    )
    assert pending == ["c.raw", "b.raw", "a.raw"]
    assert counts == {"has_census": 0, "no_props": 0}


@pytest.mark.asyncio
async def test_the_survey_counts_what_it_does_not_return():
    pending, counts = await script._survey(
        _candidates("done.raw", "todo.raw", "gone.raw"),
        {
            "done.raw": {"scan_streams": A_CENSUS},
            "todo.raw": {"range": [50, 750]},
            "gone.raw": None,
        }.get,
    )
    assert pending == ["todo.raw"]
    assert counts == {"has_census": 1, "no_props": 1}


# --- the fill ---------------------------------------------------------------


def _recorder():
    written: dict[str, list[dict]] = {}

    def write(filename: str, streams: list[dict]) -> None:
        written[filename] = streams

    return written, write


@pytest.mark.asyncio
async def test_each_readable_file_gets_its_census():
    written, write = _recorder()

    counts = await script._fill(
        ["a.raw", "b.raw"], lambda name: A_CENSUS, write, dry_run=False
    )

    assert written == {"a.raw": A_CENSUS, "b.raw": A_CENSUS}
    assert counts == {"written": 2, "unreadable": 0, "empty": 0}


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_read_is_counted_not_written():
    written, write = _recorder()

    counts = await script._fill(["gone.raw"], lambda name: None, write, dry_run=False)

    assert written == {}
    assert counts == {"written": 0, "unreadable": 1, "empty": 0}


@pytest.mark.asyncio
async def test_a_file_reporting_no_streams_is_not_recorded_as_answered():
    """An empty census must not be written.

    Writing it would make the next run skip the file, which is the opposite of
    what an empty answer from a census-bearing instrument deserves - it means
    the reader could not supply one, not that the file has no scans.
    """
    written, write = _recorder()

    counts = await script._fill(["odd.raw"], lambda name: [], write, dry_run=False)

    assert written == {}
    assert counts == {"written": 0, "unreadable": 0, "empty": 1}
    assert script.needs_census({"scan_streams": []})


@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing_and_still_reports_what_it_would():
    written, write = _recorder()

    counts = await script._fill(
        ["a.raw", "b.raw"], lambda name: A_CENSUS, write, dry_run=True
    )

    assert written == {}
    assert counts["written"] == 2


# --- the bound --------------------------------------------------------------


def test_no_limit_by_default(monkeypatch):
    monkeypatch.delenv("CENSUS_LIMIT", raising=False)
    assert script._limit() is None


def test_the_limit_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("CENSUS_LIMIT", "5000")
    assert script._limit() == 5000


@pytest.mark.parametrize("value", ["0", "-1", "lots"])
def test_a_limit_that_is_not_a_positive_number_is_reported_and_ignored(
    monkeypatch, value
):
    """Never silently a cap of zero: that reads as "nothing left to do"."""
    monkeypatch.setenv("CENSUS_LIMIT", value)
    with captured_logs() as records:
        assert script._limit() is None
    assert any("CENSUS_LIMIT" in r["message"] for r in records)


# --- the read itself --------------------------------------------------------


@pytest.mark.skipif(not KORBI_POS.exists(), reason="KORBI test file not present")
def test_the_census_of_a_real_file_is_read_from_its_raw_data(monkeypatch):
    monkeypatch.setattr(
        script.m_name, "filename_to_datafile_path", lambda name: str(KORBI_POS)
    )

    streams = script._read_census("KORBI2_AMB_POS_20260109174345.raw")

    assert streams, "the committed file holds scans, so it holds a stream"
    assert all(s["key"] for s in streams)
    assert any(s["signature"]["ms_order"] == 1 for s in streams)
    # What the whole exercise is for: a class the binding can key on.
    polarity = streams[0]["signature"]["polarity"]
    assert signature_class(streams, polarity, "orbi")


def test_an_unreadable_file_is_reported_and_answers_none(monkeypatch):
    monkeypatch.setattr(
        script.m_name,
        "filename_to_datafile_path",
        lambda name: os.path.join("no", "such", "data.raw"),
    )

    with captured_logs() as records:
        assert script._read_census("missing.raw") is None

    assert any("missing.raw" in r["message"] for r in records)
    # INFO, not WARNING: a server whose older raw data has been cleared would
    # otherwise open one monitoring issue per file.
    assert [r["message"] for r in records if r["level"].name == "WARNING"] == []
