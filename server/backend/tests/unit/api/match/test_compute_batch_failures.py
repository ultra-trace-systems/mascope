"""What a batch says when some of its samples fail to compute matches.

One unreadable file must not abort the rest of the batch - that isolation was
already in place - but the count it produced was all the user ever saw. The
reason belongs in the batch's own message, because that message is the whole of
what a notification shows.

Every dependency is mocked: no DB, file I/O or Socket.IO.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from mascope_backend.api.controllers.match.match_controller import (
    AGGREGATION_FAILURE_REASON,
    FAILURE_REASON_MAX_CHARS,
    STALE_PEAK_STORE_REASON,
    _is_stale_peak_store,
    _summarize_sample_failures,
    match_compute_batch,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_signal.compute import StalePeakStoreError


_MOD = "mascope_backend.api.controllers.match.match_controller"
_FEATURES = "mascope_backend.api.lib.api_features"

_USER = SimpleNamespace(id=7, username="tester", role_id="editor")


def _wrapped(user_message):
    """What the per-sample compute step actually raises.

    It is an ``@api_controller``, so whatever went wrong inside it reaches the
    batch already put through ``process_exception``: a message written for a
    human, with the internals left behind in the log.
    """
    return ApiException(user_message, {"error_id": "deadbeef"}, 500)


def _wrapped_stale():
    """A stale peak store as it actually arrives: wrapped, with a cause.

    The signal library raises it deep inside the compute step, whose
    ``@api_controller`` re-raises an ApiException ``from`` it - so the class
    only survives on the chain.
    """
    outer = _wrapped("The peak store holds 24 scan(s) and the sample file now reads 23")
    outer.__cause__ = StalePeakStoreError("their scan counts differ")
    return outer


def _make_sample(sample_item_id, sample_file_id=None):
    """A sample eligible for match computation.

    ``sample_file_id`` defaults to one of its own; pass a shared one for two
    samples cut from the same acquisition.
    """
    sample = MagicMock()
    sample.sample_item_id = sample_item_id
    sample.sample_item_name = f"sample {sample_item_id}"
    sample.sample_file_id = sample_file_id or f"sf-{sample_item_id}"
    sample.filename = f"Instrument_{sample.sample_file_id}"
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
    user=_USER,
    converter_available=True,
):
    """Run match_compute_batch over ``samples`` with a stubbed per-sample step.

    ``incomplete_ids`` stands in for samples an interrupted earlier run left
    with incomplete aggregates: they are what puts a batch in the aggregation
    scope when no sample of its own computed.

    Returns the batch result, the notification it was announced with, and the
    peak detection requests it made.
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
        patch(
            f"{_MOD}.ensure_converter_available", new_callable=AsyncMock
        ) as converter,
        patch(f"{_MOD}.get_access_token", new_callable=AsyncMock) as token,
        patch(f"{_MOD}.request_peak_detection", new_callable=AsyncMock) as rebuild,
    ):
        token.return_value = "token-1"
        if not converter_available:
            converter.side_effect = HTTPException(
                status_code=503, detail="File converter service is not available."
            )
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
            user=user,
        )
    return result, notify.await_args.args[1], rebuild.await_args_list


def _matched():
    """A per-sample result carrying matches."""
    isotopes = MagicMock()
    isotopes.empty = False
    return {"match_isotopes": isotopes}


@pytest.mark.asyncio
async def test_a_failing_sample_names_its_reason_in_the_batch_message():
    samples = [_make_sample("s1"), _make_sample("s2"), _make_sample("s3")]

    result, notification, _ = await _compute(
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

    result, _, _ = await _compute(
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

    result, notification, _ = await _compute(samples, [leaky])

    assert result["data"]["failed_sample_reasons"] == {"Unexpected error.": 1}
    assert "filestore" not in result["message"]
    assert "filestore" not in notification.message


@pytest.mark.asyncio
async def test_a_batch_that_only_failed_is_announced_as_an_error():
    samples = [_make_sample("s1")]

    result, notification, _ = await _compute(samples, [_wrapped("unreadable file")])

    assert result["status"] == "failed"
    assert notification.status == "error"
    assert "unreadable file" in notification.message


@pytest.mark.asyncio
async def test_a_batch_without_failures_says_nothing_about_reasons():
    samples = [_make_sample("s1"), _make_sample("s2")]

    result, notification, _ = await _compute(samples, [_matched(), _matched()])

    assert result["status"] == "success"
    assert result["data"]["failed_sample_reasons"] == {}
    assert "Failures:" not in result["message"]
    assert notification.status == "success"


@pytest.mark.asyncio
async def test_an_overlong_reason_is_trimmed_before_it_reaches_the_message():
    samples = [_make_sample("s1")]

    result, _, _ = await _compute(samples, [_wrapped("x" * 5000)])

    (reason,) = result["data"]["failed_sample_reasons"]
    assert len(reason) == FAILURE_REASON_MAX_CHARS


@pytest.mark.asyncio
async def test_an_exception_with_no_message_still_names_something():
    samples = [_make_sample("s1")]

    result, _, _ = await _compute(samples, [_wrapped("   ")])

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

    result, notification, _ = await _compute(
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


class TestStalePeakStores:
    """A refresh that meets peak data older than the reader repairs it itself.

    Re-running peak detection is the only thing that rebuilds such a store,
    and it is a per-file operation - so a batch that finds them asks the file
    converter for the rebuilds rather than telling the user to click through
    hundreds of files. The converter rematches each file's samples when it
    finishes, so they come back matched on their own.
    """

    @pytest.mark.asyncio
    async def test_a_stale_store_is_named_and_its_file_queued(self):
        samples = [_make_sample("s1"), _make_sample("s2")]

        result, notification, rebuilds = await _compute(
            samples, [_matched(), _wrapped_stale()]
        )

        assert result["data"]["failed_sample_reasons"] == {STALE_PEAK_STORE_REASON: 1}
        assert result["data"]["peak_rebuilds_queued"] == 1
        assert [call.kwargs["sample_file_id"] for call in rebuilds] == ["sf-s2"]
        assert "Peak detection has been queued" in notification.message

    @pytest.mark.asyncio
    async def test_samples_sharing_a_file_queue_it_once(self):
        """The store belongs to the file, so one rebuild serves every sample."""
        samples = [
            _make_sample("s1", sample_file_id="sf-shared"),
            _make_sample("s2", sample_file_id="sf-shared"),
        ]

        result, _, rebuilds = await _compute(
            samples, [_wrapped_stale(), _wrapped_stale()]
        )

        assert result["data"]["failed_samples_count"] == 2
        assert result["data"]["peak_rebuilds_queued"] == 1
        assert len(rebuilds) == 1
        assert rebuilds[0].kwargs["filename"] == "Instrument_sf-shared"

    @pytest.mark.asyncio
    async def test_an_ordinary_failure_queues_nothing(self):
        samples = [_make_sample("s1")]

        result, _, rebuilds = await _compute(samples, [_wrapped("unreadable file")])

        assert rebuilds == []
        assert result["data"]["peak_rebuilds_queued"] == 0
        assert "Peak detection has been queued" not in result["message"]

    @pytest.mark.asyncio
    async def test_a_run_with_no_user_reports_but_queues_nothing(self):
        """A service-driven refresh has nobody to act for.

        It is also the path the converter itself calls back on after a
        rebuild, so queueing from there is how a rebuild loop would start.
        """
        samples = [_make_sample("s1")]

        result, _, rebuilds = await _compute(samples, [_wrapped_stale()], user=None)

        assert rebuilds == []
        assert result["data"]["peak_rebuilds_queued"] == 0
        assert STALE_PEAK_STORE_REASON in result["message"], "still reported"

    @pytest.mark.asyncio
    async def test_an_unavailable_converter_does_not_fail_the_refresh(self):
        """The batch has already done its work; losing that is the worse loss."""
        samples = [_make_sample("s1"), _make_sample("s2")]

        result, notification, rebuilds = await _compute(
            samples, [_matched(), _wrapped_stale()], converter_available=False
        )

        assert rebuilds == []
        assert result["status"] == "partial", "the batch still reports its own result"
        assert result["data"]["computed_samples_count"] == 1
        assert "Peak detection has been queued" not in notification.message


class TestIsStalePeakStore:
    """The failure arrives wrapped, so the class is looked for on the chain."""

    def test_the_exception_itself(self):
        assert _is_stale_peak_store(StalePeakStoreError("stale"))

    def test_a_cause_one_level_down(self):
        assert _is_stale_peak_store(_wrapped_stale())

    def test_a_context_rather_than_a_cause(self):
        """A bare `raise` inside an except block sets only __context__."""
        outer = _wrapped("something else")
        outer.__context__ = StalePeakStoreError("stale")
        assert _is_stale_peak_store(outer)

    def test_an_unrelated_failure(self):
        assert not _is_stale_peak_store(_wrapped("unreadable file"))

    def test_a_cyclic_chain_terminates(self):
        """A hand-built chain can point back at itself."""
        first = _wrapped("one")
        second = _wrapped("two")
        first.__cause__ = second
        second.__cause__ = first
        assert not _is_stale_peak_store(first)


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
