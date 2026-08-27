"""What a batch says when some of its samples fail to compute matches.

One unreadable file must not abort the rest of the batch - that isolation was
already in place - but the count it produced was all the user ever saw. The
reason belongs in the batch's own message, because that message is the whole of
what a notification shows.

Every dependency is mocked: no DB, file I/O or Socket.IO.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.match.match_controller import (
    AGGREGATION_FAILURE_REASON,
    FAILURE_REASON_MAX_CHARS,
    _summarize_sample_failures,
    match_compute_batch,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException


_MOD = "mascope_backend.api.controllers.match.match_controller"
_FEATURES = "mascope_backend.api.lib.api_features"


def _wrapped(user_message):
    """What the per-sample compute step actually raises.

    It is an ``@api_controller``, so whatever went wrong inside it reaches the
    batch already put through ``process_exception``: a message written for a
    human, with the internals left behind in the log.
    """
    return ApiException(user_message, {"error_id": "deadbeef"}, 500)


def _make_sample(sample_item_id):
    """A sample eligible for match computation."""
    sample = MagicMock()
    sample.sample_item_id = sample_item_id
    sample.sample_item_name = f"sample {sample_item_id}"
    sample.filename = f"Instrument_{sample_item_id}"
    sample.instrument_function_id = f"if-{sample_item_id}"
    sample.mz_calibration = {"verified": True}
    return sample


def _session_returning(samples):
    scalars = MagicMock()
    scalars.all.return_value = samples
    exec_result = MagicMock()
    exec_result.scalars.return_value = scalars

    session = AsyncMock()
    session.execute = AsyncMock(return_value=exec_result)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _target_isotopes(sample_count):
    """One unmatched target isotope, so every sample has work to do."""
    df = MagicMock()
    df.empty = False
    df.__getitem__.return_value.unique.return_value.tolist.return_value = ["ion-1"]
    return [df] * sample_count


async def _compute(
    samples,
    compute_side_effect,
    aggregate_side_effect=None,
    incomplete_ids=(),
):
    """Run match_compute_batch over ``samples`` with a stubbed per-sample step.

    ``incomplete_ids`` stands in for samples an interrupted earlier run left
    with incomplete aggregates: they are what puts a batch in the aggregation
    scope when no sample of its own computed.
    """
    batch = MagicMock()
    batch.sample_batch_name = "Test Batch"

    with (
        patch(f"{_MOD}.fetch_sample_batch", new_callable=AsyncMock) as fetch_batch,
        patch(f"{_MOD}.async_session") as session,
        patch(f"{_MOD}.default_match_params", new_callable=AsyncMock),
        patch(
            f"{_MOD}.fetch_sample_unmatched_target_isotopes", new_callable=AsyncMock
        ) as targets,
        patch(f"{_MOD}.fetch_existing_main_isotope_references", new_callable=AsyncMock),
        patch(
            f"{_MOD}.compute_and_create_sample_match_isotope_data",
            new_callable=AsyncMock,
        ) as compute,
        patch(
            f"{_MOD}._incomplete_aggregate_sample_ids", new_callable=AsyncMock
        ) as incomplete,
        patch(
            f"{_MOD}.aggregate_and_create_matches", new_callable=AsyncMock
        ) as aggregate,
        patch(f"{_MOD}.update_sample_modified_timestamps", new_callable=AsyncMock),
        patch(f"{_MOD}.send_progress_user_notification", new_callable=AsyncMock),
        patch(f"{_FEATURES}.handle_notifications", new_callable=AsyncMock) as notify,
        patch(f"{_FEATURES}.handle_reloads", new_callable=AsyncMock),
    ):
        fetch_batch.return_value = batch
        session.return_value = _session_returning(samples)
        targets.side_effect = _target_isotopes(len(samples))
        compute.side_effect = compute_side_effect
        incomplete.return_value = list(incomplete_ids)
        if aggregate_side_effect is not None:
            aggregate.side_effect = aggregate_side_effect
        else:
            aggregate.return_value = {"status": "success", "data": {}}

        result = await match_compute_batch(
            sample_batch_id="batch-1",
            independent_transaction=True,
            # The routes always supply one; the per-sample progress
            # notifications the controller builds require it
            process_id="process-1",
        )
    return result, notify.await_args.args[1]


def _matched():
    """A per-sample result carrying matches."""
    isotopes = MagicMock()
    isotopes.empty = False
    return {"match_isotopes": isotopes}


@pytest.mark.asyncio
async def test_a_failing_sample_names_its_reason_in_the_batch_message():
    samples = [_make_sample("s1"), _make_sample("s2"), _make_sample("s3")]

    result, notification = await _compute(
        samples,
        [
            _matched(),
            _wrapped("conflicting sizes for dimension 'time'"),
            _matched(),
        ],
    )

    assert result["status"] == "partial"
    assert result["data"]["failed_samples_count"] == 1
    assert "conflicting sizes for dimension 'time'" in result["message"]
    assert notification.message == result["message"]
    assert notification.status == "warning", "a partial batch is not a success"


@pytest.mark.asyncio
async def test_the_reasons_are_counted_rather_than_listed_per_sample():
    """A count is what the summary needs and all a notification should carry.

    The names behind it are in the server log, one INFO line per sample.
    """
    samples = [_make_sample("s1"), _make_sample("s2"), _make_sample("s3")]

    result, _ = await _compute(
        samples,
        [_matched(), _wrapped("unreadable file"), _wrapped("unreadable file")],
    )

    assert result["data"]["failed_sample_reasons"] == {"unreadable file": 2}
    # Summarized once here so every caller reporting this batch words it alike
    assert result["data"]["failed_samples_summary"] == "unreadable file [2 sample(s)]"


@pytest.mark.asyncio
async def test_an_unwrapped_failure_does_not_put_its_internals_in_the_message():
    """Only what came through the exception pipeline is safe to show.

    Anything the decorated compute step did not wrap - an undecorated fetch,
    the loop's own body - reaches here with a raw ``str()``: a filesystem
    path, a driver message with the statement it failed on. That is dropped,
    the same way the batch aggregate drops it.
    """
    samples = [_make_sample("s1")]
    leaky = FileNotFoundError("/srv/mascope/filestore/InstrumentA/2026.01.01/data.raw")

    result, notification = await _compute(samples, [leaky])

    assert result["data"]["failed_sample_reasons"] == {"Unexpected error.": 1}
    assert "filestore" not in result["message"]
    assert "filestore" not in notification.message


@pytest.mark.asyncio
async def test_a_batch_that_only_failed_is_announced_as_an_error():
    samples = [_make_sample("s1")]

    result, notification = await _compute(samples, [_wrapped("unreadable file")])

    assert result["status"] == "failed"
    assert notification.status == "error"
    assert "unreadable file" in notification.message


@pytest.mark.asyncio
async def test_a_batch_without_failures_says_nothing_about_reasons():
    samples = [_make_sample("s1"), _make_sample("s2")]

    result, notification = await _compute(samples, [_matched(), _matched()])

    assert result["status"] == "success"
    assert result["data"]["failed_sample_reasons"] == {}
    assert "Failures:" not in result["message"]
    assert notification.status == "success"


@pytest.mark.asyncio
async def test_an_overlong_reason_is_trimmed_before_it_reaches_the_message():
    samples = [_make_sample("s1")]

    result, _ = await _compute(samples, [_wrapped("x" * 5000)])

    (reason,) = result["data"]["failed_sample_reasons"]
    assert len(reason) == FAILURE_REASON_MAX_CHARS


@pytest.mark.asyncio
async def test_an_exception_with_no_message_still_names_something():
    samples = [_make_sample("s1")]

    result, _ = await _compute(samples, [_wrapped("   ")])

    assert result["data"]["failed_sample_reasons"] == {"Unexpected error.": 1}


@pytest.mark.asyncio
async def test_a_batch_that_only_failed_to_aggregate_says_so():
    """The one failure that belongs to no sample must still have a reason.

    Every sample is skipped and the aggregation then raises, so the batch
    reports a failure with no failing sample behind it - which used to leave
    the user a red notification whose message said nothing had failed.
    """
    sample = _make_sample("s1")
    sample.mz_calibration = {"verified": False}  # skipped, so nothing computes

    result, notification = await _compute(
        [sample],
        [_matched()],
        aggregate_side_effect=RuntimeError("boom"),
        incomplete_ids=["s1"],
    )

    assert result["status"] == "failed"
    assert result["data"]["failed_samples_count"] == 0
    assert AGGREGATION_FAILURE_REASON in result["message"]
    assert notification.status == "error"
    assert "boom" not in notification.message, "the raw exception stays in the log"


class TestSummarizeSampleFailures:
    """One bad file usually fails a batch the same way, so reasons are grouped."""

    def test_one_shared_reason_is_stated_once_with_its_count(self):
        summary = _summarize_sample_failures({"bad axis": 2})

        assert summary == "bad axis [2 sample(s)]"

    def test_the_most_common_reasons_come_first(self):
        summary = _summarize_sample_failures({"rare": 1, "common": 2})

        assert summary == "common [2 sample(s)]; rare [1 sample(s)]"

    def test_a_long_tail_of_reasons_is_counted_rather_than_listed(self):
        summary = _summarize_sample_failures({"a": 4, "b": 3, "c": 2, "d": 1})

        assert summary.endswith("; and 2 further reason(s)")

    def test_no_failures_summarize_to_nothing(self):
        assert _summarize_sample_failures({}) == ""
