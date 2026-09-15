"""
Unit tests for the daily ACQUISITION batch get-or-create.

Files converted in one watcher scan share a day and an ionization mode, so they
resolve to a single batch name and their pipelines can all read "absent" and all
insert. The duplicates split the day's samples across two batches, each with its
own calibration and match state. An in-process lock cannot fix it - production
runs several uvicorn workers and each converted file arrives as its own
load-balanced request - so mutual exclusion lives in the database. These tests
pin that:

- concurrent calls converge on a single batch row (the partial unique index
  `uq_sample_batch_acquisition_natural_key` + IntegrityError recovery)
- two ionization modes that share a name still get one batch per polarity
- lookups tolerate duplicates that predate the unique index (oldest wins)

Runs against the real unit-test database; Socket.IO emits are mocked.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from test_utils import gen_test_id

from mascope_backend.api.controllers.sample.batches.sample_batches_controller import (
    get_or_create_acquisition_batch,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SampleBatchCreate,
)
from mascope_backend.db import Dataset, SampleBatch, Workspace


_CTRL = "mascope_backend.api.controllers.sample.batches.sample_batches_controller"

INDEX_NAME = "uq_sample_batch_acquisition_natural_key"
INDEX_DDL = (
    f"CREATE UNIQUE INDEX {INDEX_NAME} "
    "ON sample_batch (dataset_id, sample_batch_name, polarity) "
    "WHERE sample_batch_type = 'ACQUISITION'"
)


@pytest_asyncio.fixture
async def acquisition_dataset(async_session_factory):
    """An ACQUISITION dataset (and its workspace) to hang batches off.

    `_insert_sample_batch` refuses an ACQUISITION batch whose dataset is
    missing or of another type, so the real rows have to exist. Torn down
    afterwards; batches cascade with the dataset.
    """
    workspace_id = gen_test_id()
    dataset_id = gen_test_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"acq-batch-test {workspace_id}",
                workspace_status="active",
                is_system=True,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="2031",
                dataset_type="ACQUISITION",
                instrument="test-orbi-batch",
                dataset_utc_created=datetime(2031, 1, 1, tzinfo=timezone.utc),
            )
        )
        await session.commit()

    yield dataset_id

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleBatch).where(SampleBatch.dataset_id == dataset_id)
        )
        await session.execute(delete(Dataset).where(Dataset.dataset_id == dataset_id))
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        await session.commit()


def _batch_create(dataset_id: str, name: str, polarity: str) -> SampleBatchCreate:
    """Build the request the auto-processing pipeline builds."""
    return SampleBatchCreate(
        dataset_id=dataset_id,
        sample_batch_name=name,
        sample_batch_description="Auto-generated daily acquisition batch",
        sample_batch_type="ACQUISITION",
        polarity=polarity,
        target_collection_ids=[],
    )


async def _batches_named(async_session_factory, dataset_id: str, name: str) -> list:
    """Every ACQUISITION batch with this name in the dataset, oldest first."""
    async with async_session_factory() as session:
        return list(
            (
                await session.execute(
                    select(SampleBatch)
                    .where(
                        SampleBatch.dataset_id == dataset_id,
                        SampleBatch.sample_batch_name == name,
                    )
                    .order_by(SampleBatch.sample_batch_utc_created.asc().nulls_last())
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_concurrent_calls_create_a_single_batch(
    async_session_factory, acquisition_dataset
):
    """Concurrent get-or-create calls all return the same, single batch.

    Racing coroutines all pass the existence check before any insert commits;
    the losers must recover from the unique-index violation by re-selecting the
    winner's row instead of erroring or duplicating.
    """
    name = "2031-03-04 H3O acquisition"

    with patch(f"{_CTRL}.emit_record_created", new_callable=AsyncMock):
        results = await asyncio.gather(
            *(
                get_or_create_acquisition_batch(
                    sample_batch=_batch_create(acquisition_dataset, name, "+")
                )
                for _ in range(5)
            )
        )

    batch_ids = {r["data"]["sample_batch_id"] for r in results}
    assert len(batch_ids) == 1, (
        f"concurrent get-or-create produced {len(batch_ids)} batches"
    )
    # Exactly one caller may claim the insert; the rest adopted its row.
    assert sum(1 for r in results if r["created"]) == 1

    rows = await _batches_named(async_session_factory, acquisition_dataset, name)
    assert len(rows) == 1
    assert rows[0].sample_batch_id in batch_ids


@pytest.mark.asyncio
async def test_same_name_different_polarity_get_separate_batches(
    async_session_factory, acquisition_dataset
):
    """Modes that share a display name still get one batch per polarity.

    `ionization_mode_name` carries no uniqueness - only
    `ionization_mode_token` does - so an admin naming the positive and
    negative variant alike renders one batch name for both. Filing the second
    polarity under the first one's batch would give it the wrong polarity and
    the wrong target collections.
    """
    name = "2031-03-04 PTR acquisition"

    with patch(f"{_CTRL}.emit_record_created", new_callable=AsyncMock):
        positive = await get_or_create_acquisition_batch(
            sample_batch=_batch_create(acquisition_dataset, name, "+")
        )
        negative = await get_or_create_acquisition_batch(
            sample_batch=_batch_create(acquisition_dataset, name, "-")
        )

    assert positive["created"] and negative["created"]
    assert positive["data"]["sample_batch_id"] != negative["data"]["sample_batch_id"], (
        "a shared mode name collapsed both polarities onto one batch"
    )
    assert positive["data"]["polarity"] == "+"
    assert negative["data"]["polarity"] == "-"

    rows = await _batches_named(async_session_factory, acquisition_dataset, name)
    assert {row.polarity for row in rows} == {"+", "-"}


@pytest.mark.asyncio
async def test_lookup_tolerates_preexisting_duplicates(
    async_session_factory, acquisition_dataset
):
    """Databases poisoned before the unique index existed self-heal on read.

    The index is dropped for the duration of the test to recreate the
    pre-migration state, a newer duplicate is inserted, and the lookup must
    return the oldest - deterministically, so every worker converges on the
    same row rather than continuing to split the day's samples.
    """
    name = "2031-03-05 H3O acquisition"

    try:
        with patch(f"{_CTRL}.emit_record_created", new_callable=AsyncMock):
            first = await get_or_create_acquisition_batch(
                sample_batch=_batch_create(acquisition_dataset, name, "+")
            )
            oldest_id = first["data"]["sample_batch_id"]

            async with async_session_factory() as session:
                await session.execute(text(f"DROP INDEX {INDEX_NAME}"))
                session.add(
                    SampleBatch(
                        sample_batch_id=gen_test_id(),
                        dataset_id=acquisition_dataset,
                        sample_batch_name=name,
                        sample_batch_type="ACQUISITION",
                        polarity="+",
                        sample_batch_utc_created=datetime(
                            2032, 1, 1, tzinfo=timezone.utc
                        ),
                    )
                )
                await session.commit()

            result = await get_or_create_acquisition_batch(
                sample_batch=_batch_create(acquisition_dataset, name, "+")
            )

        # The oldest duplicate wins, consistently for every caller.
        assert result["created"] is False
        assert result["data"]["sample_batch_id"] == oldest_id
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleBatch).where(
                    SampleBatch.dataset_id == acquisition_dataset,
                    SampleBatch.sample_batch_name == name,
                )
            )
            await session.execute(text(INDEX_DDL))
            await session.commit()


@pytest.mark.asyncio
async def test_non_acquisition_batch_is_refused(acquisition_dataset):
    """The helper is ACQUISITION-only: nothing else has this natural key.

    ANALYSIS batches are user-named, carry no uniqueness, and the partial
    index does not cover them - so a caller that got here with one would get
    silent get-or-create semantics the data model does not support.
    """
    with pytest.raises(ApiException) as excinfo:
        await get_or_create_acquisition_batch(
            sample_batch=SampleBatchCreate(
                dataset_id=acquisition_dataset,
                sample_batch_name="a user batch",
                sample_batch_type="ANALYSIS",
                polarity="+-",
                target_collection_ids=[],
            )
        )
    # The ValueError reaches the caller as a 400, with its own text intact.
    assert excinfo.value.status_code == 400
    assert "ACQUISITION" in excinfo.value.user_message
