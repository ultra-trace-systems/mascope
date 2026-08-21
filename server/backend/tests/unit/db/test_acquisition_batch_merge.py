"""
The duplicate-ACQUISITION-batch merge run by migration a7f3c2e9b514.

`test_stairway` proves the migration applies and rolls back, but it runs
against an empty database, so the merge itself - four statements that repoint
sample items, carry target-collection links across, flag the survivor and
DELETE the losers - is never exercised with the data it exists for. It deletes
rows on customer databases, so it is tested here against real Postgres.

The migration's own SQL constants are imported rather than copied: a test that
restates the SQL would keep passing while the migration drifted away from it.

Everything happens inside one transaction that is rolled back, because the
merge is deliberately global - it keys on every ACQUISITION batch in the
database - and committing it would eat other tests' fixtures.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from test_utils import gen_test_id

from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    Workspace,
)


def _load_migration():
    """Import the migration module by path (alembic/versions is not a package)."""
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "20260821_a7f3c2e9b514_add_acquisition_batch_natural_key.py"
    )
    spec = importlib.util.spec_from_file_location("_acq_batch_natural_key", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


def _batch(dataset_id: str, name: str, polarity: str, created, batch_id=None):
    """One ACQUISITION batch row."""
    return SampleBatch(
        sample_batch_id=batch_id or gen_test_id(),
        dataset_id=dataset_id,
        sample_batch_name=name,
        sample_batch_type="ACQUISITION",
        status="ready",
        polarity=polarity,
        sample_batch_utc_created=created,
    )


@pytest.mark.asyncio
async def test_merge_keeps_the_oldest_and_repoints_its_samples(async_session_factory):
    """Duplicates collapse onto the oldest batch, carrying their samples over.

    Also covers the parts a naive merge gets wrong: a target-collection link
    the keeper already holds cannot be repointed onto it (composite primary
    key), the keeper is flagged for re-matching because its batch peaks no
    longer describe its members, and a batch that differs only in polarity is
    a different natural key and must survive untouched.
    """
    name = "2031-07-01 H3O acquisition"
    workspace_id, dataset_id = gen_test_id(), gen_test_id()
    keep_id, dup_id = "aaaa000000000001", "bbbb000000000002"
    shared_tc, dup_only_tc = gen_test_id(), gen_test_id()
    file_id = gen_test_id()

    async with async_session_factory() as session:
        # The merge keys on every ACQUISITION batch in the database, so this
        # transaction is never committed - see the module docstring.
        session.add_all(
            [
                Workspace(
                    workspace_id=workspace_id,
                    workspace_name=f"merge-test {workspace_id}",
                    workspace_status="active",
                ),
                Dataset(
                    dataset_id=dataset_id,
                    workspace_id=workspace_id,
                    dataset_name="2031",
                    dataset_type="ACQUISITION",
                    instrument="test-orbi-merge",
                ),
                TargetCollection(
                    target_collection_id=shared_tc,
                    target_collection_name=f"shared {shared_tc}",
                    target_collection_type="TARGETS",
                ),
                TargetCollection(
                    target_collection_id=dup_only_tc,
                    target_collection_name=f"dup-only {dup_only_tc}",
                    target_collection_type="TARGETS",
                ),
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"merge-test-{file_id}.raw",
                    instrument="test-orbi-merge",
                    datetime=datetime(2031, 7, 1, 12, 0),
                    datetime_utc=datetime(2031, 7, 1, 12, 0, tzinfo=timezone.utc),
                    length=1.0,
                    range=[100.0, 200.0],
                    polarity="+",
                ),
            ]
        )
        await session.flush()

        # Pre-migration state: the index does not exist yet, so duplicates can
        # be inserted. DDL is transactional in Postgres, so the rollback below
        # puts the index back.
        await session.execute(text(f"DROP INDEX {MIGRATION.INDEX_NAME}"))

        session.add_all(
            [
                _batch(
                    dataset_id,
                    name,
                    "+",
                    datetime(2031, 7, 1, 8, 0, tzinfo=timezone.utc),
                    keep_id,
                ),
                _batch(
                    dataset_id,
                    name,
                    "+",
                    datetime(2031, 7, 1, 9, 0, tzinfo=timezone.utc),
                    dup_id,
                ),
                # Same name, other polarity: a different natural key.
                _batch(
                    dataset_id,
                    name,
                    "-",
                    datetime(2031, 7, 1, 8, 30, tzinfo=timezone.utc),
                    "cccc000000000003",
                ),
            ]
        )
        await session.flush()

        session.add_all(
            [
                SampleItem(
                    sample_item_id=gen_test_id(),
                    sample_batch_id=dup_id,
                    sample_file_id=file_id,
                    sample_item_name="orphan-to-be-rescued",
                    sample_item_type="ACQUISITION",
                ),
                # Held by both: repointing it would violate the composite PK.
                TargetCollectionInSampleBatch(
                    target_collection_id=shared_tc, sample_batch_id=keep_id
                ),
                TargetCollectionInSampleBatch(
                    target_collection_id=shared_tc, sample_batch_id=dup_id
                ),
                # Held only by the duplicate: must move to the keeper.
                TargetCollectionInSampleBatch(
                    target_collection_id=dup_only_tc, sample_batch_id=dup_id
                ),
            ]
        )
        await session.flush()

        for statement in (
            MIGRATION._REPOINT_ITEMS_SQL,
            MIGRATION._REPOINT_COLLECTIONS_SQL,
            MIGRATION._FLAG_KEEPERS_SQL,
            MIGRATION._DELETE_DUPLICATES_SQL,
        ):
            await session.execute(text(statement))
        session.expire_all()

        surviving = (
            (
                await session.execute(
                    select(SampleBatch).where(SampleBatch.dataset_id == dataset_id)
                )
            )
            .scalars()
            .all()
        )
        assert {b.sample_batch_id for b in surviving} == {
            keep_id,
            "cccc000000000003",
        }, "the merge kept the wrong batches"

        keeper = next(b for b in surviving if b.sample_batch_id == keep_id)
        assert keeper.status == "rematch", (
            "a batch that absorbed samples must be re-matched: its batch peaks "
            "were derived over the members it had before"
        )
        other_polarity = next(b for b in surviving if b.polarity == "-")
        assert other_polarity.status == "ready", (
            "the other-polarity batch is a different natural key and was not "
            "merged, so nothing should have flagged it"
        )

        items = (
            (
                await session.execute(
                    select(SampleItem).where(SampleItem.sample_batch_id == keep_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(items) == 1, "the duplicate's sample item was not carried over"

        links = (
            (
                await session.execute(
                    select(TargetCollectionInSampleBatch.target_collection_id).where(
                        TargetCollectionInSampleBatch.sample_batch_id == keep_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(links) == {shared_tc, dup_only_tc}, (
            "the duplicate's unique target collection did not move to the keeper"
        )

        await session.rollback()
