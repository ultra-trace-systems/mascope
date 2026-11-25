"""A start seeds the environments Mascope ships, and serves when it cannot.

The seed is idempotent and runs in the main process before any worker
starts (``app/startup.py``). Its failure is logged and carried, as the
ionization seed's is: a server without its starting environments still
serves, and the next start tries again.
"""

from unittest.mock import AsyncMock

import pytest

from mascope_backend.app import startup


_STEPS = (
    "configure_database_engine",
    "dispose_engine",
    "reset_stuck_processing_batches",
    "reset_running_peak_assignment_runs",
    "reset_running_batch_peak_runs",
    "reset_interrupted_processing",
    "report_unstored_failed_files",
    "purge_read_notifications",
    "create_acquisition_datasets",
    "ensure_system_ionization",
)


@pytest.fixture
def steps(monkeypatch, tmp_path) -> dict[str, AsyncMock]:
    for step in ("gc_filestore", "ensure_deployment_id"):
        monkeypatch.setattr(startup, step, lambda: None)
    mocks = {step: AsyncMock() for step in _STEPS}
    for step, mock in mocks.items():
        monkeypatch.setattr(startup, step, mock)
    monkeypatch.setattr(startup.runtime.env, "path", lambda *_: str(tmp_path / "t"))
    return mocks


@pytest.mark.asyncio
async def test_a_start_seeds_the_shipped_environments(monkeypatch, steps):
    seed = AsyncMock(return_value=[])
    monkeypatch.setattr(startup, "ensure_shipped_environments", seed)

    await startup.init_main_process()

    seed.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_a_start_that_cannot_seed_them_carries_on(monkeypatch, steps):
    seed = AsyncMock(side_effect=RuntimeError("database down"))
    monkeypatch.setattr(startup, "ensure_shipped_environments", seed)

    await startup.init_main_process()

    seed.assert_awaited_once_with()
    steps["ensure_system_ionization"].assert_awaited_once_with()
    steps["dispose_engine"].assert_awaited_once_with()
