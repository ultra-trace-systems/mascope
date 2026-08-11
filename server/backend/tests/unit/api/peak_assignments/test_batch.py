"""
Unit tests for the batch-level peak assignment orchestrator.

Verify that ``assign_sample_batch_peaks`` sequences every sample in a batch,
isolates per-sample failures (one bad sample never aborts the rest), and
aggregates a batch status. All external dependencies are mocked - no DB, file
I/O, or Socket.IO required (mirrors test_auto_process.py).
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Module path prefixes for patching
_MOD = "mascope_backend.api.new.peak_assignments.batch"
_NOTIF = "mascope_backend.socket.notifications"
_UTILS = "mascope_backend.api.lib.utils"


def _claim_stub(acquired=True):
    """A stand-in for the cross-process assignment claim (no DB)."""

    @asynccontextmanager
    async def _claim(kind, resource_id):
        yield acquired

    return _claim


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sample(sample_item_id, name=None, blank=False, calibration_verified=True):
    """An eligible sample by default; pass blank/calibration_verified to vary it.

    Eligibility is explicit rather than inherited from MagicMock truthiness, so a
    test that means "this sample is assignable" says so.
    """
    s = MagicMock()
    s.sample_item_id = sample_item_id
    s.sample_item_name = name or sample_item_id
    s.instrument_function_id = None if blank else f"if-{sample_item_id}"
    s.mz_calibration = {"verified": calibration_verified}
    return s


def _make_batch(name="Test Batch"):
    b = MagicMock()
    b.sample_batch_name = name
    return b


def _session_returning(samples):
    """Build an async_session() context manager whose query yields ``samples``."""
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


def _base_patches(samples, batch=None):
    """Patches common to every test."""
    return {
        "fetch_batch": patch(f"{_MOD}.fetch_sample_batch", new_callable=AsyncMock),
        "assign": patch(f"{_MOD}.assign_sample_peaks", new_callable=AsyncMock),
        "claim": patch(f"{_MOD}.assignment_claim", _claim_stub()),
        "async_session": patch(f"{_MOD}.async_session"),
        "progress": patch(
            f"{_MOD}.send_progress_user_notification", new_callable=AsyncMock
        ),
        "handle_notifications": patch(
            f"{_NOTIF}.handle_notifications", new_callable=AsyncMock
        ),
        "handle_reloads": patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    }


def _start(patches, samples, batch=None):
    mocks = {k: p.start() for k, p in patches.items()}
    mocks["fetch_batch"].return_value = batch or _make_batch()
    mocks["async_session"].return_value = _session_returning(samples)
    return mocks


@pytest.fixture(autouse=True)
def _stop_all_patches():
    yield
    patch.stopall()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assigns_every_sample_in_batch():
    """One assignment run is launched per sample; status is success."""
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    samples = [_make_sample(f"si-{i}") for i in range(3)]
    patches = _base_patches(samples)
    mocks = _start(patches, samples)
    mocks["assign"].return_value = {"status": "success"}

    result = await assign_sample_batch_peaks(
        sample_batch_id="batch-1",
        independent_transaction=True,
        user_id=42,
        process_id="proc-1",
    )

    assert mocks["assign"].call_count == 3
    assert result["status"] == "success"
    assert result["data"]["assigned_samples_count"] == 3
    assert result["data"]["failed_samples_count"] == 0
    assert result["_notification_data"]["sample_batch_id"] == "batch-1"


@pytest.mark.asyncio
async def test_isolates_per_sample_failure():
    """A single failing sample does not abort assignment for the rest."""
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    samples = [_make_sample(f"si-{i}") for i in range(3)]
    patches = _base_patches(samples)
    mocks = _start(patches, samples)
    mocks["assign"].side_effect = [
        RuntimeError("corrupt file"),
        {"status": "success"},
        {"status": "success"},
    ]

    result = await assign_sample_batch_peaks(
        sample_batch_id="batch-1",
        independent_transaction=True,
        user_id=42,
        process_id="proc-1",
    )

    # All three samples were attempted despite the first raising
    assert mocks["assign"].call_count == 3
    assert result["status"] == "partial"
    assert result["data"]["assigned_samples_count"] == 2
    assert result["data"]["failed_samples_count"] == 1


@pytest.mark.asyncio
async def test_ineligible_samples_are_filtered_not_attempted():
    """Blank and uncalibrated samples are skipped before the engine is called.

    The engine raises an ApiWarning for a blank sample, so driving one through it
    would record a *failure*. Filtering up front is what makes the skipped count
    mean what it says, and it matches the eligibility rules the targeted batch
    controller applies.
    """
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    samples = [
        _make_sample("si-ok"),
        _make_sample("si-blank", blank=True),
        _make_sample("si-uncalibrated", calibration_verified=False),
    ]
    patches = _base_patches(samples)
    mocks = _start(patches, samples)

    result = await assign_sample_batch_peaks(
        sample_batch_id="batch-1",
        independent_transaction=True,
        user_id=42,
        process_id="proc-1",
    )

    # Only the eligible sample reached the engine
    assert mocks["assign"].call_count == 1
    assert mocks["assign"].call_args.kwargs["sample_item_id"] == "si-ok"
    assert result["data"]["assigned_samples_count"] == 1
    assert result["data"]["skipped_samples_count"] == 2
    assert result["data"]["failed_samples_count"] == 0
    # total counts every sample considered, not just the eligible ones
    assert result["data"]["total_samples_count"] == 3


@pytest.mark.asyncio
async def test_batch_defaults_to_stage_a_only():
    """A batch with no config runs Stage A only.

    Batch cost is per-sample cost times N, so the untargeted stage - the
    documented scaling risk - is something a caller opts into rather than what a
    menu click resolves to.
    """
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    samples = [_make_sample("si-1"), _make_sample("si-2")]
    patches = _base_patches(samples)
    mocks = _start(patches, samples)

    await assign_sample_batch_peaks(
        sample_batch_id="batch-1",
        independent_transaction=True,
        user_id=42,
        process_id="proc-1",
    )

    configs = [call.kwargs["config"] for call in mocks["assign"].call_args_list]
    assert len(configs) == 2
    assert all(cfg.run_untargeted is False for cfg in configs)
    # Resolved once and shared, so every run of the batch records one decision
    assert configs[0] is configs[1]


@pytest.mark.asyncio
async def test_explicit_config_is_honoured():
    """An explicit config overrides the Stage-A default and is passed through."""
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )
    from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig

    samples = [_make_sample("si-1")]
    patches = _base_patches(samples)
    mocks = _start(patches, samples)
    requested = PeakAssignmentConfig(run_untargeted=True, max_untargeted_peaks=50)

    await assign_sample_batch_peaks(
        sample_batch_id="batch-1",
        config=requested,
        independent_transaction=True,
        user_id=42,
        process_id="proc-1",
    )

    passed = mocks["assign"].call_args.kwargs["config"]
    assert passed is requested
    assert passed.run_untargeted is True
    assert passed.max_untargeted_peaks == 50


@pytest.mark.asyncio
async def test_empty_batch_skips_without_assigning():
    """A batch with no samples is skipped and never calls the engine."""
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    patches = _base_patches([])
    mocks = _start(patches, [])

    result = await assign_sample_batch_peaks(
        sample_batch_id="batch-1",
        independent_transaction=True,
        user_id=42,
        process_id="proc-1",
    )

    mocks["assign"].assert_not_called()
    assert result["status"] == "skipped"
    assert result["data"]["total_samples_count"] == 0


@pytest.mark.asyncio
async def test_concurrent_batch_assignment_is_refused():
    """A second assignment of the same batch is refused, not silently queued.

    The gate alone would make a duplicate request wait for the whole first run
    (tens of minutes on a large batch), which is indistinguishable from a hang.
    Refusing immediately is what a double-clicked menu item deserves.
    """
    from mascope_backend.api.new.peak_assignments import batch as batch_module
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    samples = [_make_sample("si-1")]
    patches = _base_patches(samples)
    mocks = _start(patches, samples)

    batch_module._batch_assignments_in_flight.add("batch-1")
    try:
        result = await assign_sample_batch_peaks(
            sample_batch_id="batch-1",
            independent_transaction=True,
            user_id=42,
            process_id="proc-1",
        )
    finally:
        batch_module._batch_assignments_in_flight.discard("batch-1")

    mocks["assign"].assert_not_called()
    assert result["status"] == "skipped"
    assert "already running" in result["message"]


@pytest.mark.asyncio
async def test_in_flight_marker_is_released_after_a_failing_batch():
    """The in-flight marker is released even when the batch raises.

    Otherwise one unexpected error would make the batch permanently
    un-assignable for the lifetime of the worker.
    """
    from mascope_backend.api.new.peak_assignments import batch as batch_module
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    samples = [_make_sample("si-1")]
    patches = _base_patches(samples)
    _start(patches, samples)
    # Raise from the sample loop itself, which sits inside the gate but outside
    # the per-sample try/except, so it escapes to the marker's finally.
    with patch(
        f"{_MOD}._assign_eligible_samples",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        try:
            await assign_sample_batch_peaks(
                sample_batch_id="batch-1",
                independent_transaction=True,
                user_id=42,
                process_id="proc-1",
            )
        except Exception:
            # Whether the controller decorator re-raises or reports is its own
            # concern; this test is only about the marker being released.
            pass

    assert "batch-1" not in batch_module._batch_assignments_in_flight


@pytest.mark.asyncio
async def test_two_concurrent_requests_for_one_batch_refuse_one():
    """The real double-click case: two tasks racing for the same batch.

    The guard has to claim the batch before its first await. Checking early and
    marking late lets both requests past the check, and then the first to finish
    clears the marker while the second is still running. This test drives both
    concurrently rather than pre-seeding the set, so it fails if the claim is not
    atomic.
    """
    import asyncio

    from mascope_backend.api.new.peak_assignments import batch as batch_module
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    samples = [_make_sample("si-1"), _make_sample("si-2")]
    patches = _base_patches(samples)
    mocks = _start(patches, samples)

    # Make each sample slow enough that the two calls genuinely overlap.
    async def slow_assign(**_kwargs):
        await asyncio.sleep(0.05)
        return {"status": "success"}

    mocks["assign"].side_effect = slow_assign

    batch_module._batch_assignments_in_flight.discard("batch-1")
    first, second = await asyncio.gather(
        assign_sample_batch_peaks(
            sample_batch_id="batch-1",
            independent_transaction=True,
            user_id=42,
            process_id="proc-1",
        ),
        assign_sample_batch_peaks(
            sample_batch_id="batch-1",
            independent_transaction=True,
            user_id=42,
            process_id="proc-2",
        ),
    )

    statuses = sorted(r["status"] for r in (first, second))
    assert statuses == ["skipped", "success"], (
        f"expected exactly one refusal, got {statuses}"
    )
    # Only the winning run assigned; the refused one did no work.
    assert mocks["assign"].call_count == len(samples)
    # And the marker is clean afterwards.
    assert "batch-1" not in batch_module._batch_assignments_in_flight


@pytest.mark.asyncio
async def test_claim_held_in_another_worker_refuses_the_batch():
    """A duplicate in a different process is refused, not queued.

    The in-flight set only sees this worker; the cross-process claim is what
    extends the refusal across every worker sharing the database. Here the
    local set is clean but the claim reports another holder.
    """
    from mascope_backend.api.new.peak_assignments.batch import (
        assign_sample_batch_peaks,
    )

    samples = [_make_sample("si-1")]
    patches = _base_patches(samples)
    patches["claim"] = patch(f"{_MOD}.assignment_claim", _claim_stub(acquired=False))
    mocks = _start(patches, samples)

    result = await assign_sample_batch_peaks(
        sample_batch_id="batch-1",
        independent_transaction=True,
        user_id=42,
        process_id="proc-1",
    )

    mocks["assign"].assert_not_called()
    assert result["status"] == "skipped"
    assert "already running" in result["message"]
