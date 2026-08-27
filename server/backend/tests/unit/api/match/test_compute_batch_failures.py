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
    FAILURE_REASON_MAX_CHARS,
    _summarize_sample_failures,
    match_compute_batch,
)


_MOD = "mascope_backend.api.controllers.match.match_controller"
_FEATURES = "mascope_backend.api.lib.api_features"


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


async def _compute(samples, compute_side_effect):
    """Run match_compute_batch over ``samples`` with a stubbed per-sample step."""
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
        incomplete.return_value = []
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
        [_matched(), ValueError("conflicting sizes for dimension 'time'"), _matched()],
    )

    assert result["status"] == "partial"
    assert result["data"]["failed_samples_count"] == 1
    assert "conflicting sizes for dimension 'time'" in result["message"]
    assert notification.message == result["message"]
    assert notification.status == "warning", "a partial batch is not a success"


@pytest.mark.asyncio
async def test_the_failure_records_name_the_samples_behind_the_count():
    samples = [_make_sample("s1"), _make_sample("s2")]

    result, _ = await _compute(samples, [_matched(), RuntimeError("unreadable file")])

    assert result["data"]["failed_samples"] == [
        {
            "sample_item_id": "s2",
            "sample_item_name": "sample s2",
            "filename": "Instrument_s2",
            "reason": "unreadable file",
        }
    ]
    # Summarized once here so every caller reporting this batch words it alike
    assert result["data"]["failed_samples_summary"] == "unreadable file [1 sample(s)]"


@pytest.mark.asyncio
async def test_a_batch_that_only_failed_is_announced_as_an_error():
    samples = [_make_sample("s1")]

    result, notification = await _compute(samples, [RuntimeError("unreadable file")])

    assert result["status"] == "failed"
    assert notification.status == "error"
    assert "unreadable file" in notification.message


@pytest.mark.asyncio
async def test_a_batch_without_failures_says_nothing_about_reasons():
    samples = [_make_sample("s1"), _make_sample("s2")]

    result, notification = await _compute(samples, [_matched(), _matched()])

    assert result["status"] == "success"
    assert result["data"]["failed_samples"] == []
    assert "Failures:" not in result["message"]
    assert notification.status == "success"


@pytest.mark.asyncio
async def test_an_overlong_reason_is_trimmed_before_it_reaches_the_message():
    samples = [_make_sample("s1")]

    result, _ = await _compute(samples, [RuntimeError("x" * 5000)])

    reason = result["data"]["failed_samples"][0]["reason"]
    assert len(reason) == FAILURE_REASON_MAX_CHARS


@pytest.mark.asyncio
async def test_an_exception_with_no_message_is_named_by_its_type():
    samples = [_make_sample("s1")]

    result, _ = await _compute(samples, [TimeoutError()])

    assert result["data"]["failed_samples"][0]["reason"] == "TimeoutError"


class TestSummarizeSampleFailures:
    """One bad file usually fails a batch the same way, so reasons are grouped."""

    @staticmethod
    def _records(*reasons):
        return [{"reason": reason} for reason in reasons]

    def test_one_shared_reason_is_stated_once_with_its_count(self):
        summary = _summarize_sample_failures(self._records("bad axis", "bad axis"))

        assert summary == "bad axis [2 sample(s)]"

    def test_the_most_common_reasons_come_first(self):
        summary = _summarize_sample_failures(self._records("rare", "common", "common"))

        assert summary == "common [2 sample(s)]; rare [1 sample(s)]"

    def test_a_long_tail_of_reasons_is_counted_rather_than_listed(self):
        summary = _summarize_sample_failures(self._records("a", "b", "c", "d"))

        assert summary.endswith("; and 2 further reason(s)")
