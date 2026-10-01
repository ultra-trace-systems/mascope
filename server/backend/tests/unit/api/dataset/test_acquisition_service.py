"""
Unit tests for the acquisition dataset service (get-or-create semantics).

The nightly reproducibility run exposed a get-or-create race: concurrent
auto-processing pipelines both looked up the year-dataset, both found
nothing, and both inserted - after which every later lookup died with
MultipleResultsFound and all subsequent files of that instrument/year were
dropped. These tests pin the fix:

- concurrent calls converge on a single dataset row (the partial unique
  index `uq_dataset_acquisition_natural_key` + IntegrityError recovery)
- lookups tolerate duplicates that predate the unique index (oldest wins)

Runs against the real unit-test database; Socket.IO emits are mocked.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, text
from test_utils import gen_test_id

from mascope_backend.db import Dataset, Workspace


_SVC = "mascope_backend.api.controllers.dataset.acquisition.service"

# Names must pass validate_instrument_name (letters/digits/hyphens and a
# resolvable instrument type - "orbi" here).
RACE_INSTRUMENT = "test-orbi-race"
DUP_INSTRUMENT = "test-orbi-dup"


def _emit_patches():
    """Mock the Socket.IO emit helpers used by the acquisition service."""
    return (
        patch(f"{_SVC}.emit_record_created", new_callable=AsyncMock),
        patch(f"{_SVC}.emit_record_reload", new_callable=AsyncMock),
    )


async def _cleanup_instrument(async_session_factory, instrument: str) -> None:
    """Remove datasets + the system workspace a test created for `instrument`."""
    async with async_session_factory() as session:
        await session.execute(delete(Dataset).where(Dataset.instrument == instrument))
        await session.execute(
            delete(Workspace).where(Workspace.workspace_name.ilike(f"%{instrument}"))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_calls_create_a_single_dataset(async_session_factory):
    """Concurrent get-or-create calls all return the same, single dataset.

    Racing coroutines all pass the existence check before any insert
    commits; the losers must recover from the unique-index violation by
    re-selecting the winner's row instead of erroring or duplicating.
    """
    from mascope_backend.api.controllers.dataset.acquisition.service import (
        get_acquisition_dataset,
    )

    created_patch, reload_patch = _emit_patches()
    try:
        with created_patch, reload_patch:
            results = await asyncio.gather(
                *(
                    get_acquisition_dataset(instrument=RACE_INSTRUMENT, year=2031)
                    for _ in range(5)
                )
            )

        dataset_ids = {r["data"]["dataset_id"] for r in results}
        assert len(dataset_ids) == 1, (
            f"concurrent get-or-create produced {len(dataset_ids)} datasets"
        )

        async with async_session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Dataset).where(
                            Dataset.instrument == RACE_INSTRUMENT,
                            Dataset.dataset_name == "2031",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].dataset_id in dataset_ids
    finally:
        await _cleanup_instrument(async_session_factory, RACE_INSTRUMENT)


@pytest.mark.asyncio
async def test_lookup_tolerates_preexisting_duplicates(async_session_factory):
    """Databases poisoned before the unique index existed self-heal on read.

    The unique index is dropped for the duration of the test to recreate the
    pre-migration state, two duplicate year-datasets are inserted, and the
    lookup must return the oldest instead of raising MultipleResultsFound.
    """
    from mascope_backend.api.controllers.dataset.acquisition.service import (
        get_acquisition_dataset,
    )

    index_ddl = (
        "CREATE UNIQUE INDEX uq_dataset_acquisition_natural_key "
        "ON dataset (workspace_id, instrument, dataset_name) "
        "WHERE dataset_type = 'ACQUISITION'"
    )

    created_patch, reload_patch = _emit_patches()
    try:
        with created_patch, reload_patch:
            # First call creates workspace + dataset normally.
            first = await get_acquisition_dataset(instrument=DUP_INSTRUMENT, year=2031)
            oldest_id = first["data"]["dataset_id"]
            workspace_id = first["data"]["workspace_id"]

            # Simulate the pre-index poisoned state: a second, newer duplicate.
            async with async_session_factory() as session:
                await session.execute(
                    text("DROP INDEX uq_dataset_acquisition_natural_key")
                )
                session.add(
                    Dataset(
                        dataset_id=gen_test_id(),
                        workspace_id=workspace_id,
                        dataset_name="2031",
                        dataset_type="ACQUISITION",
                        instrument=DUP_INSTRUMENT,
                        dataset_utc_created=datetime(2032, 1, 1, tzinfo=timezone.utc),
                    )
                )
                await session.commit()

            result = await get_acquisition_dataset(instrument=DUP_INSTRUMENT, year=2031)

        # The oldest duplicate wins, consistently for every caller.
        assert result["data"]["dataset_id"] == oldest_id
    finally:
        await _cleanup_instrument(async_session_factory, DUP_INSTRUMENT)
        async with async_session_factory() as session:
            await session.execute(text(index_ddl))
            await session.commit()


@pytest.mark.asyncio
async def test_a_server_without_instruments_is_not_warned_about(monkeypatch):
    """Every start creates the acquisition datasets, and a server no file has
    reached yet has no instrument to create them for - a normal state, which
    at WARNING reached error monitoring on every start."""
    from mascope_backend.api.controllers.dataset.acquisition import service

    warnings = []
    monkeypatch.setattr(service.runtime.logger, "warning", warnings.append)
    with patch(
        f"{_SVC}.get_instruments",
        new_callable=AsyncMock,
        return_value={"data": []},
    ):
        result = await service.create_acquisition_datasets()

    assert result["results"] == 0
    assert warnings == []
