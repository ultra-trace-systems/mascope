"""
Reload events for the peak assignment ledger are gated on the opt-in flag.

The ingest controllers declare a ``peak_assignment`` reload in their static
decorator args, so the declaration itself cannot be conditional. The gate
lives in ``handle_reloads``: with the feature off no assignment can be
written, so emitting the event on every ingest would be pure noise for a
deployment that never opted in.
"""

from unittest.mock import AsyncMock

import pytest

from mascope_backend.api.lib import utils


@pytest.mark.asyncio
async def test_peak_assignment_reload_skipped_when_disabled(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(utils, "emit_record_reload", emit)
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")

    await utils.handle_reloads(
        context="test",
        reload_events=[
            ("match", "sample_batch_id"),
            ("peak_assignment", "sample_batch_id"),
        ],
        kwargs={"sample_batch_id": "batch-1"},
        result=None,
    )

    emitted = [call.kwargs["record_type"] for call in emit.call_args_list]
    assert emitted == ["match"]


@pytest.mark.asyncio
async def test_peak_assignment_reload_emitted_when_enabled(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(utils, "emit_record_reload", emit)
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")

    await utils.handle_reloads(
        context="test",
        reload_events=[("peak_assignment", "sample_batch_id")],
        kwargs={"sample_batch_id": "batch-1"},
        result=None,
    )

    emitted = [call.kwargs["record_type"] for call in emit.call_args_list]
    assert emitted == ["peak_assignment"]
