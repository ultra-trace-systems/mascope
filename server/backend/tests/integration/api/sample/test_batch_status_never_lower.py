"""
Tests: a ``rematch`` flag never lowers a pending ``recalibrate``.

``update_sample_batch_status(..., never_lower=True)`` leaves a batch with
samples alone when it already waits on a stronger pending status: a
recalibration ends in a rematch, so it covers one. The edit flaggers that ask
for a rematch pass it - the ionization-mode edit
(``tests/integration/api/ionization/test_ionization_mode_update.py``), and,
covered here through their routes, the target collection create, update and
delete, a target ion edit, a compound formula change and deletion, a sample
copy into another batch, and a change of a batch's collections. A rematch
forced on a batch pending a recalibration puts it back at ``recalibrate``,
whether it fails or ends with any outcome. A batch without samples still goes to ``ready``, and the
socket event carries the status each batch holds, not the one asked for.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from mascope_backend.api.controllers.match import match_controller
from mascope_backend.api.controllers.sample.batches.status import service
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_match.params import BaseMatchParams


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1)


@pytest_asyncio.fixture(scope="session")
async def status_dataset(async_session_factory):
    """A workspace, dataset and sample file to host the batches."""
    workspace_id = gen_id()
    dataset_id = gen_id()
    file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name="Batch Status Workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Batch Status Dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=f"test-orbion_{file_id}.raw",
                instrument="test-orbion",
                datetime=_NOW_NAIVE,
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+",
            )
        )
        await session.commit()
    return {"dataset_id": dataset_id, "file_id": file_id}


async def _batch(
    async_session_factory, ctx, status, with_sample=True, ionization_mode_id=None
):
    """A batch at ``status``, with one sample item unless ``with_sample`` is off."""
    batch_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=ctx["dataset_id"],
                sample_batch_name=f"Status Batch {batch_id}",
                sample_batch_utc_created=_NOW,
                status=status,
            )
        )
        if with_sample:
            session.add(
                SampleItem(
                    sample_item_id=gen_id(),
                    sample_batch_id=batch_id,
                    sample_file_id=ctx["file_id"],
                    ionization_mode_id=ionization_mode_id,
                    sample_item_name="Status Item",
                    sample_item_type="SAMPLE",
                    sample_item_attributes={},
                    polarity="+",
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    return batch_id


async def _collection(async_session_factory, batch_id=None):
    """A global TARGETS collection with one compound, optionally on a batch."""
    collection_id = gen_id()
    compound_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCollection(
                target_collection_id=collection_id,
                target_collection_name=f"Status Targets {collection_id}",
                target_collection_type="TARGETS",
                workspace_id=None,
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=compound_id,
                target_compound_name=f"Status Compound {compound_id}",
                target_compound_formula="C6H12O6",
            )
        )
        await session.flush()
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=compound_id,
                target_collection_id=collection_id,
            )
        )
        if batch_id is not None:
            session.add(
                TargetCollectionInSampleBatch(
                    target_collection_id=collection_id,
                    sample_batch_id=batch_id,
                )
            )
        await session.commit()
    return collection_id, compound_id


async def _status(async_session_factory, batch_id):
    async with async_session_factory() as session:
        return (await session.get(SampleBatch, batch_id)).status


# ============= The service =============


@pytest.mark.asyncio
async def test_never_lower_keeps_recalibrate_and_emits_it(
    async_session_factory, status_dataset, monkeypatch
):
    """A guarded rematch keeps 'recalibrate', raises 'ready', and emits each."""
    emit = AsyncMock()
    monkeypatch.setattr(service, "emit_record_updated", emit)
    pending = await _batch(async_session_factory, status_dataset, "recalibrate")
    idle = await _batch(async_session_factory, status_dataset, "ready")

    result = await service.update_sample_batch_status(
        sample_batch_ids=[pending, idle],
        status="rematch",
        independent_transaction=True,
        never_lower=True,
    )

    assert await _status(async_session_factory, pending) == "recalibrate"
    assert await _status(async_session_factory, idle) == "rematch"
    assert result["data"]["updated_batches"] == 1
    emitted = {
        call.kwargs["record_id"]: call.kwargs["record"]["status"]
        for call in emit.await_args_list
    }
    assert emitted == {pending: "recalibrate", idle: "rematch"}


@pytest.mark.asyncio
async def test_unguarded_rematch_still_lowers(async_session_factory, status_dataset):
    """Without the guard the requested status is written, as other callers expect."""
    batch_id = await _batch(async_session_factory, status_dataset, "recalibrate")

    await service.update_sample_batch_status(
        sample_batch_ids=[batch_id], status="rematch"
    )

    assert await _status(async_session_factory, batch_id) == "rematch"


@pytest.mark.asyncio
async def test_never_lower_still_readies_a_batch_without_samples(
    async_session_factory, status_dataset
):
    """A batch with no samples has nothing to recalibrate: it goes to 'ready'."""
    batch_id = await _batch(
        async_session_factory, status_dataset, "recalibrate", with_sample=False
    )

    await service.update_sample_batch_status(
        sample_batch_ids=[batch_id], status="rematch", never_lower=True
    )

    assert await _status(async_session_factory, batch_id) == "ready"


# ============= The target collection flaggers =============


@pytest.mark.asyncio
async def test_creating_a_collection_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset
):
    """Creating a collection on a batch pending a recalibration keeps the flag."""
    batch_id = await _batch(async_session_factory, status_dataset, "recalibrate")
    _, compound_id = await _collection(async_session_factory)

    resp = await owner_client.post(
        "/api/target/collections",
        json={
            "target_collection_name": f"Status Created {batch_id}",
            "target_collection_type": "TARGETS",
            "target_compound_ids": [compound_id],
            "sample_batch_ids": [batch_id],
        },
    )

    assert resp.status_code == 201, resp.text
    assert await _status(async_session_factory, batch_id) == "recalibrate"


@pytest.mark.asyncio
async def test_updating_a_collection_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset
):
    """Changing a batch's collection compounds keeps a pending recalibrate."""
    batch_id = await _batch(async_session_factory, status_dataset, "recalibrate")
    collection_id, compound_id = await _collection(async_session_factory, batch_id)
    _, other_compound_id = await _collection(async_session_factory)

    resp = await owner_client.patch(
        f"/api/target/collections/{collection_id}",
        json={"target_compound_ids": [compound_id, other_compound_id]},
    )

    assert resp.status_code == 200, resp.text
    assert await _status(async_session_factory, batch_id) == "recalibrate"


@pytest.mark.asyncio
async def test_deleting_a_collection_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset
):
    """Deleting a batch's collection keeps a pending recalibrate."""
    batch_id = await _batch(async_session_factory, status_dataset, "recalibrate")
    collection_id, _ = await _collection(async_session_factory, batch_id)

    resp = await owner_client.delete(f"/api/target/collections/{collection_id}")

    assert resp.status_code == 200, resp.text
    assert await _status(async_session_factory, batch_id) == "recalibrate"


@pytest.mark.asyncio
async def test_updating_a_collection_still_flags_a_ready_batch(
    owner_client, async_session_factory, status_dataset
):
    """The guard leaves the ordinary flag alone: a ready batch goes to rematch."""
    batch_id = await _batch(async_session_factory, status_dataset, "ready")
    collection_id, compound_id = await _collection(async_session_factory, batch_id)
    _, other_compound_id = await _collection(async_session_factory)

    resp = await owner_client.patch(
        f"/api/target/collections/{collection_id}",
        json={"target_compound_ids": [compound_id, other_compound_id]},
    )

    assert resp.status_code == 200, resp.text
    assert await _status(async_session_factory, batch_id) == "rematch"


# ============= The other edit flaggers =============


async def _mode(async_session_factory):
    """An ionization mechanism and a mode of it."""
    mechanism_id = gen_id()
    mode_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {mechanism_id}",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Status Mode {mode_id}",
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[mechanism_id],
            )
        )
        await session.commit()
    return mechanism_id, mode_id


async def _ion_chain(async_session_factory, ctx, status):
    """A batch at ``status`` whose sample reads a mode with the ion's mechanism."""
    mechanism_id, mode_id = await _mode(async_session_factory)
    ion_id = gen_id()
    batch_id = await _batch(
        async_session_factory, ctx, status, ionization_mode_id=mode_id
    )
    _, compound_id = await _collection(async_session_factory, batch_id)
    async with async_session_factory() as session:
        session.add(
            TargetIon(
                target_ion_id=ion_id,
                target_compound_id=compound_id,
                ionization_mechanism_id=mechanism_id,
                target_ion_formula="C6H13O6+",
            )
        )
        await session.commit()
    return batch_id, ion_id


_ION_PARAMS = BaseMatchParams(
    probable_match_threshold=0.8,
    possible_match_threshold=0.7,
    mz_tolerance=5,
    isotope_ratio_tolerance=0.2,
    peak_min_intensity=0,
    isotope_abundance_threshold=1e-5,
).model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"), [("recalibrate", "recalibrate"), ("ready", "rematch")]
)
async def test_editing_a_target_ion_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset, before, after
):
    """New match parameters on an ion flag its batches, a pending recalibrate kept."""
    batch_id, ion_id = await _ion_chain(async_session_factory, status_dataset, before)

    resp = await owner_client.patch(
        f"/api/target/ions/{ion_id}",
        json={"match_params": {"test-orbion": _ION_PARAMS}},
    )

    assert resp.status_code == 200, resp.text
    assert await _status(async_session_factory, batch_id) == after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"), [("recalibrate", "recalibrate"), ("ready", "rematch")]
)
async def test_changing_a_compound_formula_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset, before, after
):
    """A formula change flags the compound's batches, a pending recalibrate kept."""
    batch_id = await _batch(async_session_factory, status_dataset, before)
    _, compound_id = await _collection(async_session_factory, batch_id)

    resp = await owner_client.patch(
        "/api/target/compounds",
        json=[
            {
                "target_compound_id": compound_id,
                "target_compound_name": f"Status Compound {compound_id}",
                "target_compound_formula": "C6H14O6",
            }
        ],
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["updated_compounds_results"] == 1, resp.text
    assert await _status(async_session_factory, batch_id) == after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"), [("recalibrate", "recalibrate"), ("ready", "rematch")]
)
async def test_deleting_a_compound_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset, before, after
):
    """Deleting a compound flags its batches, a pending recalibrate kept."""
    batch_id = await _batch(async_session_factory, status_dataset, before)
    _, compound_id = await _collection(async_session_factory, batch_id)

    resp = await owner_client.delete(f"/api/target/compounds/{compound_id}")

    assert resp.status_code == 200, resp.text
    assert await _status(async_session_factory, batch_id) == after


@pytest_asyncio.fixture(scope="module")
async def sample_view(async_engine):
    """``sample_view``, which ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


async def _first_item(async_session_factory, batch_id):
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleItem.sample_item_id).where(
                    SampleItem.sample_batch_id == batch_id
                )
            )
        ).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"), [("recalibrate", "recalibrate"), ("ready", "rematch")]
)
async def test_copying_samples_into_a_batch_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset, sample_view, before, after
):
    """A copy from another batch flags the target, a pending recalibrate kept."""
    _, mode_id = await _mode(async_session_factory)
    source_id = await _batch(
        async_session_factory, status_dataset, "ready", ionization_mode_id=mode_id
    )
    target_id = await _batch(async_session_factory, status_dataset, before)
    item_id = await _first_item(async_session_factory, source_id)

    resp = await owner_client.post(
        "/api/sample/items/copy",
        json={"sample_batch_id": target_id, "sample_item_ids": [item_id]},
    )

    # The copy runs as a background task, which the test client awaits
    assert resp.status_code == 202, resp.text
    async with async_session_factory() as session:
        copied = (
            await session.execute(
                select(func.count()).where(SampleItem.sample_batch_id == target_id)
            )
        ).scalar_one()
    assert copied == 2
    assert await _status(async_session_factory, target_id) == after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"), [("recalibrate", "recalibrate"), ("ready", "rematch")]
)
async def test_changing_a_batch_collections_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset, before, after
):
    """A new collection set flags the batch, a pending recalibrate kept, and the
    response carries the status the batch holds."""
    batch_id = await _batch(async_session_factory, status_dataset, before)
    collection_id, _ = await _collection(async_session_factory, batch_id)
    other_id, _ = await _collection(async_session_factory)

    resp = await owner_client.patch(
        f"/api/sample/batches/{batch_id}",
        json={"target_collection_ids": [collection_id, other_id]},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == after
    assert await _status(async_session_factory, batch_id) == after


# ============= A failed rematch =============


def _failing_removal(async_session_factory, batch_id, flag=None):
    """A removal step that fails, after setting the batch to ``flag`` if given:
    an edit flagging it while the rematch runs."""

    async def _remove(**_):
        if flag is not None:
            async with async_session_factory() as session:
                (await session.get(SampleBatch, batch_id)).status = flag
                await session.commit()
        raise ApiException("Removal failed", "Removal failed", 500)

    return _remove


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"), [("recalibrate", "recalibrate"), ("rematch", "rematch")]
)
async def test_a_failed_rematch_goes_back_to_its_pending_status(
    owner_client, async_session_factory, status_dataset, monkeypatch, before, after
):
    """A rematch forced on a batch pending a recalibration that fails leaves
    it pending the recalibration; one pending a rematch stays at rematch."""
    batch_id = await _batch(async_session_factory, status_dataset, before)
    monkeypatch.setattr(
        match_controller,
        "match_remove_batch",
        _failing_removal(async_session_factory, batch_id),
    )

    resp = await owner_client.post(
        f"/api/match/rematch/batch/{batch_id}", params={"force": True}
    )

    assert resp.status_code == 202, resp.text
    assert await _status(async_session_factory, batch_id) == after


@pytest.mark.asyncio
async def test_a_failed_rematch_keeps_a_recalibrate_flagged_meanwhile(
    owner_client, async_session_factory, status_dataset, monkeypatch
):
    """An edit that asked for a recalibration while the rematch ran keeps it
    when the rematch fails."""
    batch_id = await _batch(async_session_factory, status_dataset, "rematch")
    monkeypatch.setattr(
        match_controller,
        "match_remove_batch",
        _failing_removal(async_session_factory, batch_id, flag="recalibrate"),
    )

    resp = await owner_client.post(f"/api/match/rematch/batch/{batch_id}")

    assert resp.status_code == 202, resp.text
    assert await _status(async_session_factory, batch_id) == "recalibrate"


# ============= A finished rematch =============


def _step(status):
    """A removal or compute step that reports ``status`` over one sample."""

    async def _run(**_):
        return {
            "status": status,
            "data": {"total_samples_count": 1, "computed_samples_count": 1},
        }

    return _run


# Each outcome of the (removal, compute) pair, and the status it maps to for
# a batch that was pending a rematch
_OUTCOMES = [
    pytest.param("skipped", "skipped", "ready", id="skipped"),
    pytest.param("success", "success", "ready", id="success"),
    pytest.param("skipped", "partial", "rematch", id="partial"),
    pytest.param("success", "failed", "rematch", id="failed"),
    pytest.param("failed", "success", "rematch", id="fallback"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("remove_status", "compute_status", "outcome"), _OUTCOMES)
@pytest.mark.parametrize("before", ["recalibrate", "rematch"])
async def test_a_finished_rematch_keeps_a_pending_recalibrate(
    owner_client,
    async_session_factory,
    status_dataset,
    monkeypatch,
    remove_status,
    compute_status,
    outcome,
    before,
):
    """A rematch forced on a batch pending a recalibration ends at
    ``recalibrate`` whatever its outcome; one pending a rematch ends at the
    outcome's status."""
    batch_id = await _batch(async_session_factory, status_dataset, before)
    monkeypatch.setattr(match_controller, "match_remove_batch", _step(remove_status))
    monkeypatch.setattr(match_controller, "match_compute_batch", _step(compute_status))

    resp = await owner_client.post(
        f"/api/match/rematch/batch/{batch_id}", params={"force": True}
    )

    assert resp.status_code == 202, resp.text
    expected = "recalibrate" if before == "recalibrate" else outcome
    assert await _status(async_session_factory, batch_id) == expected
