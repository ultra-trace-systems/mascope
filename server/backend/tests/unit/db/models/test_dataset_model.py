"""
Unit tests for the Dataset SQLAlchemy model.
Tests model creation, validation, and relationships.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_utils import gen_test_id

from mascope_backend.db import Dataset, SampleBatch, Workspace


@pytest.mark.asyncio
async def test_create_dataset(session, db_test_workspace):
    """Test creating a dataset with valid data."""
    dataset = Dataset(
        dataset_id=gen_test_id(),
        workspace_id=db_test_workspace.workspace_id,
        dataset_name="Create Test Dataset",
        dataset_description="Testing dataset creation",
        dataset_utc_created=datetime.now(timezone.utc),
    )
    session.add(dataset)
    await session.flush()

    result = await session.get(Dataset, dataset.dataset_id)

    assert result is not None
    assert result.dataset_id == dataset.dataset_id
    assert result.dataset_name == "Create Test Dataset"
    assert result.dataset_description == "Testing dataset creation"
    assert result.dataset_utc_created is not None


@pytest.mark.asyncio
async def test_dataset_name_required(session, db_test_workspace):
    """Test that dataset_name is required."""
    dataset = Dataset(
        dataset_id=gen_test_id(),
        workspace_id=db_test_workspace.workspace_id,
        dataset_description="A test dataset without name",
    )
    session.add(dataset)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_dataset_relationship(session, db_test_dataset, db_test_sample_batch):
    """Test dataset relationship with sample batches."""
    assert db_test_sample_batch.dataset_id == db_test_dataset.dataset_id

    stmt = select(SampleBatch).where(
        SampleBatch.dataset_id == db_test_dataset.dataset_id
    )
    result = await session.execute(stmt)
    batches = result.scalars().all()

    assert len(batches) >= 1

    test_batch = next(
        (
            b
            for b in batches
            if b.sample_batch_id == db_test_sample_batch.sample_batch_id
        ),
        None,
    )
    assert test_batch is not None
    assert test_batch.sample_batch_name == "DB Test Batch"


@pytest.mark.asyncio
async def test_cascade_delete(session, db_test_workspace):
    """Test that deleting a dataset cascades to sample batches."""
    dataset = Dataset(
        dataset_id=gen_test_id(),
        workspace_id=db_test_workspace.workspace_id,
        dataset_name="Cascade Test Dataset",
        dataset_utc_created=datetime.now(timezone.utc),
    )
    session.add(dataset)
    await session.flush()

    sample_batch = SampleBatch(
        sample_batch_id=gen_test_id(),
        dataset_id=dataset.dataset_id,
        sample_batch_name="Cascade Test Batch",
        sample_batch_utc_created=datetime.now(timezone.utc),
    )
    session.add(sample_batch)
    await session.flush()

    batch_exists = await session.get(SampleBatch, sample_batch.sample_batch_id)
    assert batch_exists is not None

    await session.delete(dataset)
    await session.flush()

    # passive_deletes=True hands the cascade to the database (ON DELETE
    # CASCADE) rather than the ORM, so the child is never removed from the
    # session's identity map. Expunge it and re-query so the assertion reflects
    # the real DB state instead of the stale cached object.
    session.expunge(sample_batch)
    result = await session.get(SampleBatch, sample_batch.sample_batch_id)
    assert result is None


@pytest.mark.asyncio
async def test_duplicate_dataset_name_rejected(session, db_test_workspace):
    """Two datasets in one workspace cannot share a name.

    `uq_dataset_workspace_name_ci` is what makes the controller's
    read-then-write name check safe: without it two concurrent creates both
    pass the check and both insert.
    """
    session.add(
        Dataset(
            dataset_id=gen_test_id(),
            workspace_id=db_test_workspace.workspace_id,
            dataset_name="Unique Name Test",
            dataset_utc_created=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    session.add(
        Dataset(
            dataset_id=gen_test_id(),
            workspace_id=db_test_workspace.workspace_id,
            dataset_name="Unique Name Test",
            dataset_utc_created=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_duplicate_dataset_name_rejected_case_insensitively(
    session, db_test_workspace
):
    """A name differing only in case is the same name.

    Two such datasets read as one entry in the workspace list, which is the
    bug the index exists to prevent - so the index is on the canonical key
    `lower(btrim(dataset_name))`, not on the raw column.
    """
    session.add(
        Dataset(
            dataset_id=gen_test_id(),
            workspace_id=db_test_workspace.workspace_id,
            dataset_name="Case Variant Test",
            dataset_utc_created=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    session.add(
        Dataset(
            dataset_id=gen_test_id(),
            workspace_id=db_test_workspace.workspace_id,
            dataset_name="cAsE vArIaNt tEsT",
            dataset_utc_created=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_duplicate_dataset_name_rejected_across_padding(
    session, db_test_workspace
):
    """Surrounding whitespace does not make a name a different name.

    The canonical key btrims before it lowers, so a padded row and a stripped
    one collide in the database exactly as they collide in the workspace list.
    `DatasetCreate` strips new names, but rows written before that validator
    existed can still carry padding - and a pair that differs only by a
    trailing space is precisely the look-alike this index exists to end.
    """
    session.add(
        Dataset(
            dataset_id=gen_test_id(),
            workspace_id=db_test_workspace.workspace_id,
            dataset_name="Padded Name Test ",
            dataset_utc_created=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    session.add(
        Dataset(
            dataset_id=gen_test_id(),
            workspace_id=db_test_workspace.workspace_id,
            dataset_name="  padded name test",
            dataset_utc_created=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_duplicate_dataset_name_allowed_in_another_workspace(
    session, db_test_workspace
):
    """The name is unique per workspace, not globally.

    Two teams naming their dataset "Blanks" in their own workspaces is
    ordinary use, not a collision.
    """
    other_workspace = Workspace(
        # Workspace names are globally unique (ix_workspace_name_ci), so this
        # one cannot reuse the fixture's literal.
        workspace_id=gen_test_id(),
        workspace_name=f"DB Test Workspace {gen_test_id(8)}",
        workspace_utc_created=datetime.now(timezone.utc),
    )
    session.add(other_workspace)
    await session.flush()

    for workspace_id in (db_test_workspace.workspace_id, other_workspace.workspace_id):
        session.add(
            Dataset(
                dataset_id=gen_test_id(),
                workspace_id=workspace_id,
                dataset_name="Per Workspace Name Test",
                dataset_utc_created=datetime.now(timezone.utc),
            )
        )
    await session.flush()

    stmt = select(Dataset).where(Dataset.dataset_name == "Per Workspace Name Test")
    assert len((await session.execute(stmt)).scalars().all()) == 2


@pytest.mark.asyncio
async def test_acquisition_datasets_are_outside_the_name_index(
    session, db_test_workspace
):
    """`uq_dataset_workspace_name_ci` does not cover ACQUISITION rows.

    A schema-level test: it pins what the index constrains, not what the API
    permits. ACQUISITION datasets are named after the calendar year and
    created one per instrument by `get_acquisition_dataset`, so a single
    instrument workspace legitimately holds several "2027" rows - the index is
    partial precisely so it does not reject them. That path inserts them
    directly and never goes through `create_dataset`, whose name check is
    stricter than this index (see the test below).
    """
    for instrument in ("instrument-a", "instrument-b"):
        session.add(
            Dataset(
                dataset_id=gen_test_id(),
                workspace_id=db_test_workspace.workspace_id,
                dataset_name="2027",
                dataset_type="ACQUISITION",
                instrument=instrument,
                dataset_utc_created=datetime.now(timezone.utc),
            )
        )
    await session.flush()

    stmt = select(Dataset).where(
        Dataset.workspace_id == db_test_workspace.workspace_id,
        Dataset.dataset_name == "2027",
    )
    assert len((await session.execute(stmt)).scalars().all()) == 2


@pytest.mark.asyncio
async def test_acquisition_name_does_not_block_a_user_dataset(
    session, db_test_workspace
):
    """The index alone would let a user dataset reuse an ACQUISITION name.

    Schema level again, and deliberately *not* a claim about the product: the
    API refuses this with a 409, because `_assert_name_available` considers
    every dataset in the workspace while the index skips ACQUISITION rows.
    What is pinned here is that the database does not also refuse it - the
    index has to stay partial, or an ANALYSIS dataset named "2027" would turn
    the next year's rollover in that instrument workspace into an
    IntegrityError `get_acquisition_dataset` cannot recover from, and nothing
    but a migration could unstick it.
    """
    session.add(
        Dataset(
            dataset_id=gen_test_id(),
            workspace_id=db_test_workspace.workspace_id,
            dataset_name="2027",
            dataset_type="ACQUISITION",
            instrument="instrument-a",
            dataset_utc_created=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    analysis = Dataset(
        dataset_id=gen_test_id(),
        workspace_id=db_test_workspace.workspace_id,
        dataset_name="2027",
        dataset_type="ANALYSIS",
        dataset_utc_created=datetime.now(timezone.utc),
    )
    session.add(analysis)
    await session.flush()

    assert await session.get(Dataset, analysis.dataset_id) is not None
