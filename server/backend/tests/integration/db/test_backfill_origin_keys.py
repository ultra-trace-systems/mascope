"""
Tests: the origin key backfill gives a key to the copies whose window says
which part they are, and counts the rest.

A copy made before the key has no reference to its source; its own file,
polarity and window are what is left. One whose window is still an
acquisition sample's of its file and polarity is that part. The rest are
counted as edited, or as having no acquisition sample at all, and left
without a key. A key already set is never touched.

The integration database is shared by every test of the run, so what is
counted is compared from before each test's rows were seeded.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.controllers.sample.lib.origin_key import origin_key
from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.admin.sample_item.backfill_origin_keys import (
    backfill_origin_keys,
    count_origin_keys,
)
from mascope_backend.db.id import gen_id


#: Each copy: its file, polarity and window, and the key it already has. A
#: window moved at one end alone is as edited as one moved at both.
_COPIES = {
    "unedited": ("measured", "-", 0.0, 60.0, None),
    "unedited, other polarity": ("measured", "+", 0.0, 30.0, None),
    "edited": ("measured", "-", 10.0, 20.0, None),
    "start edited": ("measured", "-", 5.0, 60.0, None),
    "end edited": ("measured", "-", 0.0, 50.0, None),
    "of a polarity never acquired": ("one polarity", "+", 0.0, 60.0, None),
    "of a file never acquired": ("unacquired", "-", 0.0, 60.0, None),
    "keyed already": ("measured", "-", 0.0, 60.0, "k" * 64),
}

#: The acquisition record: the parts its batches hold.
_ACQUIRED = [
    ("measured", "-", 0.0, 60.0),
    ("measured", "+", 0.0, 30.0),
    ("one polarity", "-", 0.0, 60.0),
]


@pytest_asyncio.fixture
async def seeded(async_session_factory):
    before = await count_origin_keys()
    workspace_id, dataset_id = gen_id(), gen_id()
    acquired_batch, copies_batch = gen_id(), gen_id()
    files = {name: gen_id() for name in ("measured", "one polarity", "unacquired")}
    copies = {name: gen_id() for name in _COPIES}
    async with async_session_factory() as session:
        session.add(Workspace(workspace_id=workspace_id, workspace_name=gen_id()))
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name=gen_id(),
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=acquired_batch,
                dataset_id=dataset_id,
                sample_batch_name=gen_id(),
                sample_batch_type="ACQUISITION",
                polarity="-",
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=copies_batch,
                dataset_id=dataset_id,
                sample_batch_name=gen_id(),
            )
        )
        for sample_file_id in files.values():
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"instrument-a_{sample_file_id}.raw",
                    instrument="instrument-a",
                    instrument_type="orbi",
                    datetime=datetime(2026, 11, 4, 12, 0),
                    datetime_utc=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
                    length=60.0,
                    range=[40.0, 600.0],
                    polarity="+-",
                )
            )
        await session.flush()

        def item(batch_id, file_name, polarity, t0, t1, key=None, item_id=None):
            return SampleItem(
                sample_item_id=item_id or gen_id(),
                sample_batch_id=batch_id,
                sample_file_id=files[file_name],
                sample_item_name=gen_id(),
                sample_item_type="UNKNOWN",
                polarity=polarity,
                t0=t0,
                t1=t1,
                origin_key=key,
            )

        session.add_all([item(acquired_batch, *part) for part in _ACQUIRED])
        session.add_all(
            [
                item(copies_batch, *fields, item_id=copies[name])
                for name, fields in _COPIES.items()
            ]
        )
        await session.commit()
    yield {"before": before, "files": files, "copies": copies}
    async with async_session_factory() as session:
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.commit()


async def _keys(async_session_factory, copies: dict) -> dict:
    by_id = {item_id: name for name, item_id in copies.items()}
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(SampleItem.sample_item_id, SampleItem.origin_key).where(
                    SampleItem.sample_item_id.in_(by_id)
                )
            )
        ).all()
    return {by_id[item_id]: key for item_id, key in rows}


@pytest.mark.asyncio
async def test_a_dry_run_counts_and_writes_nothing(seeded, async_session_factory):
    result = await backfill_origin_keys(dry_run=True)

    before = seeded["before"]
    assert {name: result[name] - before[name] for name in before} == {
        "set": 2,
        "edited": 3,
        "no_source": 2,
    }
    assert result["status"] == "dry_run"
    assert result["updated"] == 0
    assert await _keys(async_session_factory, seeded["copies"]) == {
        name: key for name, (*_, key) in _COPIES.items()
    }


@pytest.mark.asyncio
async def test_a_run_keys_the_unedited_copies_page_by_page(
    seeded, async_session_factory
):
    result = await backfill_origin_keys(batch_size=1)

    files = seeded["files"]

    def part(name):
        file_name, polarity, t0, t1, _ = _COPIES[name]
        return origin_key(files[file_name], polarity, t0, t1)

    assert result["updated"] == result["set"]
    assert await _keys(async_session_factory, seeded["copies"]) == {
        "unedited": part("unedited"),
        "unedited, other polarity": part("unedited, other polarity"),
        "edited": None,
        "start edited": None,
        "end edited": None,
        "of a polarity never acquired": None,
        "of a file never acquired": None,
        "keyed already": "k" * 64,
    }
    # Nothing is left for another run to set
    assert (await count_origin_keys())["set"] == 0
