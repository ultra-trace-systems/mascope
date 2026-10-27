"""
Tests: PATCH /api/sample/items/{id} checks a new batch and a new file.

The update body always carries the item's batch and file. A batch it changes
gets the checks /api/sample/items/move makes (editor on the destination, no
locked item, no locked destination), and a file it changes the read check of
that file. Into another workspace is in ``test_token_writes.py``; here are the
cases inside the editor's own workspace, where only the new checks can refuse.

The controller is replaced by a recorder: what the update writes is not the
subject, and the session-scoped fixtures stay as they are.

Fixtures used:
- ``ws_alpha`` and everything in it (every test user is a member)
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import SampleBatch, SampleFile, SampleItem
from mascope_backend.db.id import gen_id


_SAMPLES = "mascope_backend.api.routes.sample.items.sample_items_routes"
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def other_batches(async_session_factory, alpha_dataset):
    """Two more batches in ws_alpha: one open, one locked."""
    ids = {"open": gen_id(), "locked": gen_id()}
    async with async_session_factory() as session:
        for key, batch_id in ids.items():
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=alpha_dataset,
                    sample_batch_name=f"Relink {key}",
                    sample_batch_utc_created=_NOW,
                    locked=1 if key == "locked" else 0,
                )
            )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id.in_(ids.values()))
        )
        await session.commit()


@pytest_asyncio.fixture
async def own_items(async_session_factory, alpha_batch):
    """Two items in alpha_batch, one open and one locked, on a file of their own.

    The file is readable through these items alone.
    """
    file_id = gen_id()
    ids = {"open": gen_id(), "locked": gen_id()}
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=f"test-orbion_{file_id}.raw",
                instrument="test-orbion",
                datetime=_NOW.replace(tzinfo=None),
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+",
            )
        )
        await session.flush()
        for key, item_id in ids.items():
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=alpha_batch,
                    sample_file_id=file_id,
                    sample_item_name=f"Relink {key}",
                    sample_item_type="ANALYSIS",
                    sample_item_attributes={},
                    polarity="+",
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                    locked=1 if key == "locked" else 0,
                )
            )
        await session.commit()
    yield {**ids, "file": file_id}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id.in_(ids.values()))
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == file_id)
        )
        await session.commit()


@pytest.fixture
def calls(monkeypatch):
    """Replace the update controller with a recorder; return its calls."""
    recorded: list[dict] = []

    async def controller(**kwargs):
        recorded.append(kwargs)
        return {"data": {}}

    monkeypatch.setattr(f"{_SAMPLES}.update_sample_item", controller)
    return recorded


def _body(batch: str, file: str) -> dict:
    return {
        "sample_item": {
            "sample_batch_id": batch,
            "sample_file_id": file,
            "sample_item_name": "Relinked",
            "sample_item_type": "SAMPLE",
            "sample_item_attributes": {},
            "tic": 1000.0,
            "polarity": "+",
            "ionization_mode_id": "mode",
            "t0": 0.0,
            "t1": 60.0,
        }
    }


@pytest.mark.asyncio
async def test_an_editor_moves_an_item_to_a_batch_they_edit(
    editor_client, own_items, other_batches, calls
):
    resp = await editor_client.patch(
        f"/api/sample/items/{own_items['open']}",
        json=_body(other_batches["open"], own_items["file"]),
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_locked_batch_takes_no_item(
    owner_client, own_items, other_batches, calls
):
    """Refused to the owner too: a locked batch is no destination, as for /move."""
    resp = await owner_client.patch(
        f"/api/sample/items/{own_items['open']}",
        json=_body(other_batches["locked"], own_items["file"]),
    )
    assert resp.status_code == 403, resp.text
    assert not calls


@pytest.mark.asyncio
async def test_a_locked_item_stays_in_its_batch(
    owner_client, own_items, alpha_batch, other_batches, calls
):
    """The owner may edit a locked item, but not move it out of its batch."""
    item = f"/api/sample/items/{own_items['locked']}"

    in_place = await owner_client.patch(
        item, json=_body(alpha_batch, own_items["file"])
    )
    moved = await owner_client.patch(
        item, json=_body(other_batches["open"], own_items["file"])
    )

    assert in_place.status_code == 200, in_place.text
    assert moved.status_code == 403, moved.text
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_an_editor_relinks_an_item_to_a_file_they_read(
    editor_client, own_items, alpha_batch, alpha_item, sample_file, calls
):
    """``sample_file`` is readable through alpha_item, in the editor's workspace."""
    resp = await editor_client.patch(
        f"/api/sample/items/{own_items['open']}",
        json=_body(alpha_batch, sample_file),
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_an_item_is_not_relinked_to_a_file_that_is_not_there(
    editor_client, own_items, alpha_batch, calls
):
    resp = await editor_client.patch(
        f"/api/sample/items/{own_items['open']}",
        json=_body(alpha_batch, gen_id()),
    )
    assert resp.status_code == 403, resp.text
    assert not calls
