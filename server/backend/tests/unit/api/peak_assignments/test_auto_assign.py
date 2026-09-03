"""
Unit tests for the auto-processing peak-assignment hook.

``auto_assign_sample_peaks`` is what the sample auto-processing pipeline calls.
These verify the properties that matter for the hot path: it does nothing
unless the feature is enabled, it can be switched off for ingest alone, it
leaves a sample denser than the ingest ceiling for an explicit run, it runs the
engine Stage-A only (``run_untargeted=False``), and it never lets an assignment
failure escape into the processing lifecycle.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


_SVC = "mascope_backend.api.new.peak_assignments.service"


def _hook(enabled=True, on_ingest=True, ceiling=0, n_peaks=0, ledger="sample"):
    """Patch everything the hook reads; return the stack and its mocks.

    The two settings and the engine are always patched, because the defaults
    would otherwise reach the runtime config and the database. ``ceiling`` is
    the ingest ceiling the hook sees, ``n_peaks`` what the peak file reports.
    """
    stack = ExitStack()
    mocks = {
        "assign": stack.enter_context(
            patch(f"{_SVC}.assign_sample_peaks", new_callable=AsyncMock)
        ),
        "fold": stack.enter_context(
            patch(f"{_SVC}.fold_sample_peaks_without_run", new_callable=AsyncMock)
        ),
        "fetch": stack.enter_context(
            patch(
                f"{_SVC}.fetch_sample",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(sample_item_name="dense-1"),
            )
        ),
        "count": stack.enter_context(
            patch(f"{_SVC}.count_sample_peaks", return_value=n_peaks)
        ),
    }
    stack.enter_context(patch(f"{_SVC}.peak_assignment_enabled", return_value=enabled))
    stack.enter_context(
        patch(f"{_SVC}.peak_assignment_on_ingest", return_value=on_ingest)
    )
    stack.enter_context(
        patch(f"{_SVC}.peak_assignment_ingest_max_peaks", return_value=ceiling)
    )
    stack.enter_context(
        patch(f"{_SVC}.peak_assignment_ingest_ledger", return_value=ledger)
    )
    return stack, mocks


@pytest.mark.asyncio
async def test_does_nothing_when_feature_disabled():
    """With the feature off, ingest must not create assignment runs unasked.

    This is the guarantee that a deployment which has switched peak-centric
    assignment off processes samples exactly as it did before the feature
    landed. The flag is on by default, so this pins the opted-out behaviour
    rather than the default one.
    """
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(enabled=False)
    with stack:
        await auto_assign_sample_peaks(sample_item_id="si-1", user_id=42)

    mocks["assign"].assert_not_called()


@pytest.mark.asyncio
async def test_does_nothing_when_ingest_assignment_is_off():
    """`peak_assignment_on_ingest = false` keeps the feature and drops only the
    per-sample ingest run - so nothing is read and nothing is launched."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(on_ingest=False, ceiling=100)
    with stack:
        await auto_assign_sample_peaks(sample_item_id="si-1", user_id=42)

    mocks["assign"].assert_not_called()
    mocks["fetch"].assert_not_called()
    mocks["count"].assert_not_called()


@pytest.mark.asyncio
async def test_runs_stage_a_only():
    """The auto hook drives the engine database-first only, nested under parent."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook()
    with stack:
        await auto_assign_sample_peaks(
            sample_item_id="si-1", user_id=42, parent_id="proc-1"
        )

    assign = mocks["assign"]
    assign.assert_called_once()
    kwargs = assign.call_args.kwargs
    # Stage-A only: untargeted (Stage B) enumeration is off on the hot path
    assert kwargs["config"].run_untargeted is False
    # Runs nested so the parent orchestrator owns reloads / avoids toast spam
    assert kwargs["independent_transaction"] is False
    assert kwargs["parent_id"] == "proc-1"
    assert kwargs["user_id"] == 42


@pytest.mark.asyncio
async def test_a_sample_denser_than_the_ceiling_is_left_for_an_explicit_run():
    """One very dense acquisition is hundreds of megabytes of ledger; above the
    ceiling the hook logs and returns before any run row exists."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(ceiling=1000, n_peaks=1001)
    with stack:
        await auto_assign_sample_peaks(sample_item_id="si-1", user_id=42)

    mocks["count"].assert_called_once()
    mocks["assign"].assert_not_called()


@pytest.mark.asyncio
async def test_a_sample_at_the_ceiling_is_still_assigned():
    """The ceiling is inclusive: exactly that many peaks is fine."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(ceiling=1000, n_peaks=1000)
    with stack:
        await auto_assign_sample_peaks(sample_item_id="si-1", user_id=42)

    mocks["assign"].assert_called_once()


@pytest.mark.asyncio
async def test_a_zero_ceiling_skips_the_count_entirely():
    """0 disables the ceiling, and with it the extra peak-file read."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(ceiling=0, n_peaks=10**9)
    with stack:
        await auto_assign_sample_peaks(sample_item_id="si-1", user_id=42)

    mocks["fetch"].assert_not_called()
    mocks["count"].assert_not_called()
    mocks["assign"].assert_called_once()


@pytest.mark.asyncio
async def test_swallows_engine_failure():
    """An assignment failure is logged and never propagated to auto-processing."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook()
    with stack:
        mocks["assign"].side_effect = RuntimeError("boom")
        # Must not raise
        await auto_assign_sample_peaks(sample_item_id="si-1", user_id=42)

    mocks["assign"].assert_called_once()


@pytest.mark.asyncio
async def test_swallows_a_failed_peak_count_too():
    """The count is a file read on the processing path; a failure there is the
    engine's failure to report, not the pipeline's to die of."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(ceiling=1000)
    with stack:
        mocks["count"].side_effect = OSError("no peak file")
        await auto_assign_sample_peaks(sample_item_id="si-1", user_id=42)

    mocks["assign"].assert_not_called()


# --- the batch-ledger mode ------------------------------------------------------


@pytest.mark.asyncio
async def test_the_batch_ledger_mode_folds_without_a_run():
    """Under ``peak_assignment_ingest_ledger = "batch"`` the hook folds the
    sample straight into the batch peaks and creates no run."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(ledger="batch")
    with stack:
        await auto_assign_sample_peaks(
            sample_item_id="si-1", user_id=42, parent_id="proc-1"
        )

    mocks["fold"].assert_awaited_once_with("si-1")
    mocks["assign"].assert_not_called()


@pytest.mark.asyncio
async def test_the_sample_ledger_mode_never_folds_without_a_run():
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(ledger="sample")
    with stack:
        await auto_assign_sample_peaks(sample_item_id="si-1")

    mocks["assign"].assert_called_once()
    mocks["fold"].assert_not_called()


@pytest.mark.asyncio
async def test_the_batch_ledger_mode_still_honours_the_ceiling():
    """The ceiling guards the fold as it guards a run: a pathological sample is
    hundreds of thousands of members as much as it is of rows."""
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(ledger="batch", ceiling=100, n_peaks=101)
    with stack:
        await auto_assign_sample_peaks(sample_item_id="si-1")

    mocks["fold"].assert_not_called()
    mocks["assign"].assert_not_called()


@pytest.mark.asyncio
async def test_swallows_a_failed_batch_ledger_fold():
    from mascope_backend.api.new.peak_assignments.service import (
        auto_assign_sample_peaks,
    )

    stack, mocks = _hook(ledger="batch")
    mocks["fold"].side_effect = RuntimeError("fold failed")
    with stack:
        await auto_assign_sample_peaks(sample_item_id="si-1")  # must not raise
