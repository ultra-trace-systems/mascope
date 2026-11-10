"""
Integration tests: an item with no TIC is read, listed and edited.

An item stores no TIC when its file gave no finite one (a read with no scan,
or a TOF file that recorded no signal), and migration ``566299655056`` set the
NaN TICs stored before to NULL (``docs/maintaining.md``). The item routes read
the table through ``SampleItemRead``, take an edit through
``SampleItemUpdate``, both on ``SampleItemBase``, as a copy reads its source:
each has to hold a NULL TIC, or the route answers 400 (a whole batch's
listing with it) or 422 where develop answered 200.

Run against the real routes and a real database, because what is pinned is
the model a stored row is read into.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.db import (
    Dataset,
    IonizationMode,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def no_tic_sample_view(async_engine):
    """``sample_view``, which ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def seeded(async_session_factory, test_users):
    """A batch the editor may edit, holding an item with no TIC and one with."""
    ids = {
        name: gen_id(16)
        for name in (
            "workspace",
            "dataset",
            "batch",
            "file",
            "mode",
            "no_tic",
            "with_tic",
        )
    }
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"No TIC {ids['workspace']}",
                workspace_status="active",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=ids["workspace"],
                user_id=test_users["editor"].id,
                workspace_role="editor",
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="No TIC",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="No TIC",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"no-tic-tof_{ids['file']}",
                instrument="no-tic-tof",
                datetime=_NOW.replace(tzinfo=None),
                datetime_utc=_NOW,
                length=60.0,
                range=[100.0, 500.0],
                polarity="-",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"No TIC {ids['mode']}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[],
            )
        )
        await session.flush()
        for name, tic in (("no_tic", None), ("with_tic", 1000.0)):
            session.add(
                SampleItem(
                    sample_item_id=ids[name],
                    sample_batch_id=ids["batch"],
                    sample_file_id=ids["file"],
                    sample_item_name=f"No TIC {name}",
                    sample_item_type="SAMPLE",
                    sample_item_attributes={},
                    polarity="-",
                    ionization_mode_id=ids["mode"],
                    tic=tic,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        # The workspace takes its dataset, batch and items with it; the file
        # and the mode are their own.
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == ids["file"])
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == ids["mode"]
            )
        )
        await session.commit()


async def _stored_tic(async_session_factory, sample_item_id: str) -> float | None:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleItem.tic).where(
                    SampleItem.sample_item_id == sample_item_id
                )
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_an_item_with_no_tic_is_read(editor_client, seeded):
    response = await editor_client.get(f"/api/sample/items/{seeded['no_tic']}")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["tic"] is None


@pytest.mark.asyncio
async def test_a_batch_holding_an_item_with_no_tic_is_listed(editor_client, seeded):
    response = await editor_client.get(
        "/api/sample/items", params={"sample_batch_id": seeded["batch"]}
    )

    assert response.status_code == 200, response.text
    listed = {row["sample_item_id"]: row["tic"] for row in response.json()["data"]}
    assert listed == {seeded["no_tic"]: None, seeded["with_tic"]: 1000.0}


@pytest.mark.asyncio
async def test_an_item_with_no_tic_is_edited_as_the_listing_sent_it(
    editor_client, seeded, async_session_factory
):
    """The app's edit sends back the row the listing gave it, ``tic: null``
    included."""
    response = await editor_client.patch(
        f"/api/sample/items/{seeded['no_tic']}",
        json={
            "sample_item": {
                "sample_batch_id": seeded["batch"],
                "sample_file_id": seeded["file"],
                "sample_item_name": "No TIC renamed",
                "sample_item_type": "SAMPLE",
                "sample_item_attributes": {},
                "polarity": "-",
                "ionization_mode_id": seeded["mode"],
                "tic": None,
                "t0": 0.0,
                "t1": 60.0,
            }
        },
    )

    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["sample_item_name"] == "No TIC renamed"
    assert updated["tic"] is None
    assert await _stored_tic(async_session_factory, seeded["no_tic"]) is None
