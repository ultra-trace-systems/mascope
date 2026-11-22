"""
Tests: an edit of a calibration collection flags its batches "recalibrate".

A collection an ionization mode calibrates on is an input of every file of
that mode's calibration. Changing its compounds leaves those files fitted on
the old calibrants, so ``update_target_collection`` flags the batches holding
them "recalibrate" (``calibrated_batch_ids``), as the ionization mode edit does
when a mode is pointed at another collection. A collection that only matches
keeps "rematch", and so does a batch that has the calibration collection
attached but holds no file of the mode. A batch re-calibration clears the flag.

The sample list says, per sample, whether its TOF fit was made on the
collection version its mode offers a fit now
(``mz_calibration_collection_stale``), read as the calibration node reads the
collection.
"""

import copy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from mascope_backend.api.controllers.calibration import calibration_controller
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_isotopes_sha256,
    calibration_params_factory,
    get_calibration_handler,
)
from mascope_backend.api.controllers.calibration.lib.collection_version import (
    collection_version,
    current_collection_versions,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    MzCalibrationParams,
)
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
    TargetIsotope,
    Workspace,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def collection_sample_view(async_engine):
    """``sample_view``, which ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def ctx(async_session_factory):
    """A workspace and dataset for the batches, and one TOF file for samples."""
    ids = {"workspace": gen_id(16), "dataset": gen_id(16), "file": gen_id(16)}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Calibration edit {ids['workspace']}",
                workspace_status="active",
            )
        )
        await session.flush()
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Calibration edit",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"calibration-edit_{ids['file']}",
                instrument="calibration-edit",
                instrument_type="tof",
                datetime=_NOW.replace(tzinfo=None),
                datetime_utc=_NOW,
                length=60.0,
                range=[50.0, 500.0],
                polarity="-",
            )
        )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.commit()


async def _compound(session, mechanism_id=None, mz=61.9884):
    """A compound, with one ion of ``mechanism_id`` and its isotopes."""
    compound_id = gen_id(16)
    session.add(
        TargetCompound(
            target_compound_id=compound_id,
            target_compound_name=f"Calibrant {compound_id}",
            target_compound_formula="HNO3",
        )
    )
    if mechanism_id is not None:
        ion_id = gen_id(16)
        session.add(
            TargetIon(
                target_ion_id=ion_id,
                target_compound_id=compound_id,
                ionization_mechanism_id=mechanism_id,
                target_ion_formula=f"NO3- {compound_id}",
            )
        )
        # The TOF fit reads the LOW isotopes; the HIGH one is the Orbitrap's
        for resolution, offset in (("LOW", 0.0), ("HIGH", 0.001)):
            session.add(
                TargetIsotope(
                    target_isotope_id=gen_id(16),
                    target_ion_id=ion_id,
                    target_isotope_formula=f"NO3- {compound_id}",
                    mz=mz + offset,
                    relative_abundance=1.0,
                    resolution=resolution,
                )
            )
    await session.flush()
    return compound_id


async def _setup(async_session_factory):
    """A mechanism, a collection with one calibrant, and a mode calibrating on it."""
    ids = {
        "mechanism": gen_id(16),
        "collection": gen_id(16),
        "mode": gen_id(16),
    }
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids["mechanism"],
                ionization_mechanism_polarity="-",
                ionization_mechanism=f"[M]- {ids['mechanism']}",
            )
        )
        session.add(
            TargetCollection(
                target_collection_id=ids["collection"],
                target_collection_name=f"Calibrants {ids['collection']}",
                target_collection_type="CALIBRANTS",
                workspace_id=None,
            )
        )
        await session.flush()
        ids["compound"] = await _compound(session, ids["mechanism"])
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=ids["compound"],
                target_collection_id=ids["collection"],
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"Calibration edit {ids['mode']}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[ids["mechanism"]],
                calibration_collection_id=ids["collection"],
            )
        )
        await session.commit()
    return ids


#: A sample on the shared file, which has no calibration record.
_SHARED_FILE = object()


async def _batch(
    async_session_factory,
    ctx,
    mode_id=None,
    attach=None,
    status="ready",
    record=_SHARED_FILE,
):
    """A batch at ``status`` with one sample under ``mode_id``.

    :param attach: A collection attached to the batch, as a match collection.
    :param record: The sample's own file's calibration record, when given:
        the sample then gets a file of its own.
    """
    batch_id = gen_id(16)
    item_id = gen_id(16)
    file_id = ctx["file"]
    async with async_session_factory() as session:
        if record is not _SHARED_FILE:
            file_id = gen_id(16)
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"calibration-edit_{file_id}",
                    instrument="calibration-edit",
                    instrument_type="tof",
                    datetime=_NOW.replace(tzinfo=None),
                    datetime_utc=_NOW,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-",
                    mz_calibration=copy.deepcopy(record),
                )
            )
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=ctx["dataset"],
                sample_batch_name=f"Calibration edit {batch_id}",
                sample_batch_utc_created=_NOW,
                status=status,
            )
        )
        await session.flush()
        session.add(
            SampleItem(
                sample_item_id=item_id,
                sample_batch_id=batch_id,
                sample_file_id=file_id,
                ionization_mode_id=mode_id,
                sample_item_name="Calibration edit item",
                sample_item_type="SAMPLE",
                sample_item_attributes={},
                polarity="-",
                tic=1000.0,
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=_NOW,
            )
        )
        if attach is not None:
            session.add(
                TargetCollectionInSampleBatch(
                    target_collection_id=attach, sample_batch_id=batch_id
                )
            )
        await session.commit()
    return batch_id, item_id


async def _another_compound(async_session_factory, mechanism_id=None, mz=96.9601):
    async with async_session_factory() as session:
        compound_id = await _compound(session, mechanism_id, mz=mz)
        await session.commit()
    return compound_id


async def _status(async_session_factory, batch_id):
    async with async_session_factory() as session:
        return (await session.get(SampleBatch, batch_id)).status


async def _edit_compounds(client, collection_id, compound_ids):
    response = await client.patch(
        f"/api/target/collections/{collection_id}",
        json={"target_compound_ids": compound_ids},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ============= The edit's flags =============


@pytest.mark.asyncio
async def test_a_calibration_collection_edit_flags_recalibrate(
    owner_client, async_session_factory, ctx
):
    """Its mode's batches go to recalibrate, attached to the collection or not."""
    ids = await _setup(async_session_factory)
    attached, _ = await _batch(
        async_session_factory, ctx, ids["mode"], attach=ids["collection"]
    )
    unattached, _ = await _batch(async_session_factory, ctx, ids["mode"])
    added = await _another_compound(async_session_factory)

    body = await _edit_compounds(
        owner_client, ids["collection"], [ids["compound"], added]
    )

    assert await _status(async_session_factory, attached) == "recalibrate"
    assert await _status(async_session_factory, unattached) == "recalibrate"
    assert "Updated 2/2 batches to status 'recalibrate'" in body["message"]
    assert "'rematch'" not in body["message"]


@pytest.mark.asyncio
async def test_a_batch_with_no_file_of_the_mode_still_rematches(
    owner_client, async_session_factory, ctx
):
    """A batch the collection is attached to, with files of another mode only."""
    ids = await _setup(async_session_factory)
    other = await _setup(async_session_factory)
    elsewhere, _ = await _batch(
        async_session_factory, ctx, other["mode"], attach=ids["collection"]
    )
    calibrated, _ = await _batch(async_session_factory, ctx, ids["mode"])

    body = await _edit_compounds(owner_client, ids["collection"], [])

    assert await _status(async_session_factory, elsewhere) == "rematch"
    assert await _status(async_session_factory, calibrated) == "recalibrate"
    assert "Updated 1/1 batches to status 'recalibrate'" in body["message"]
    assert "Updated 1/1 batches to status 'rematch'" in body["message"]


@pytest.mark.asyncio
async def test_a_matching_only_collection_edit_flags_rematch(
    owner_client, async_session_factory, ctx
):
    """No mode calibrates on it: its batches go to rematch, as before."""
    ids = await _setup(async_session_factory)
    async with async_session_factory() as session:
        targets = gen_id(16)
        session.add(
            TargetCollection(
                target_collection_id=targets,
                target_collection_name=f"Targets {targets}",
                target_collection_type="TARGETS",
                workspace_id=None,
            )
        )
        await session.flush()
        compound = await _compound(session)
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=compound, target_collection_id=targets
            )
        )
        await session.commit()
    batch_id, _ = await _batch(async_session_factory, ctx, ids["mode"], attach=targets)
    added = await _another_compound(async_session_factory)

    await _edit_compounds(owner_client, targets, [compound, added])

    assert await _status(async_session_factory, batch_id) == "rematch"


@pytest.mark.asyncio
async def test_a_sample_under_an_earlier_version_is_flagged_too(
    owner_client, async_session_factory, ctx
):
    """A sample left on an earlier version moves onto the current one when
    re-calibrated, so its batch owes the recalibration as well."""
    ids = await _setup(async_session_factory)
    current = gen_id(16)
    async with async_session_factory() as session:
        earlier = await session.get(IonizationMode, ids["mode"])
        earlier.is_active = False
        await session.flush()
        session.add(
            IonizationMode(
                ionization_mode_id=current,
                ionization_mode_name=earlier.ionization_mode_name,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[ids["mechanism"]],
                calibration_collection_id=ids["collection"],
                version=2,
                supersedes_id=ids["mode"],
            )
        )
        await session.commit()
    batch_id, _ = await _batch(async_session_factory, ctx, ids["mode"])

    await _edit_compounds(owner_client, ids["collection"], [])

    assert await _status(async_session_factory, batch_id) == "recalibrate"


@pytest.mark.asyncio
async def test_a_mode_pointed_elsewhere_does_not_count(
    owner_client, async_session_factory, ctx
):
    """An earlier version calibrated on the collection, the current one does not."""
    ids = await _setup(async_session_factory)
    other = await _setup(async_session_factory)
    async with async_session_factory() as session:
        earlier = await session.get(IonizationMode, ids["mode"])
        earlier.is_active = False
        await session.flush()
        session.add(
            IonizationMode(
                ionization_mode_id=gen_id(16),
                ionization_mode_name=earlier.ionization_mode_name,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[ids["mechanism"]],
                calibration_collection_id=other["collection"],
                version=2,
                supersedes_id=ids["mode"],
            )
        )
        await session.commit()
    batch_id, _ = await _batch(async_session_factory, ctx, ids["mode"])

    await _edit_compounds(owner_client, ids["collection"], [])

    assert await _status(async_session_factory, batch_id) == "ready"


@pytest.mark.asyncio
async def test_a_rename_of_a_calibration_collection_flags_nothing(
    owner_client, async_session_factory, ctx
):
    """Only a change of the compounds changes what a fit is offered."""
    ids = await _setup(async_session_factory)
    batch_id, _ = await _batch(async_session_factory, ctx, ids["mode"])

    response = await owner_client.patch(
        f"/api/target/collections/{ids['collection']}",
        json={"target_collection_name": f"Renamed {ids['collection']}"},
    )

    assert response.status_code == 200, response.text
    assert await _status(async_session_factory, batch_id) == "ready"


@pytest.mark.asyncio
async def test_attaching_a_calibration_collection_only_rematches(
    owner_client, async_session_factory, ctx
):
    """A change of the batches it is attached to changes no fit."""
    ids = await _setup(async_session_factory)
    calibrated, _ = await _batch(async_session_factory, ctx, ids["mode"])
    newcomer, _ = await _batch(async_session_factory, ctx, ids["mode"])

    response = await owner_client.patch(
        f"/api/target/collections/{ids['collection']}",
        json={"sample_batch_ids": [newcomer]},
    )

    assert response.status_code == 200, response.text
    assert await _status(async_session_factory, newcomer) == "rematch"
    assert await _status(async_session_factory, calibrated) == "ready"


@pytest.mark.asyncio
async def test_a_batch_recalibration_clears_the_flag(
    owner_client, async_session_factory, ctx, monkeypatch
):
    """A re-calibration of the batch ends at rematch, which its matches owe."""
    ids = await _setup(async_session_factory)
    batch_id, item_id = await _batch(async_session_factory, ctx, ids["mode"])
    await _edit_compounds(owner_client, ids["collection"], [])
    assert await _status(async_session_factory, batch_id) == "recalibrate"
    calibrate = AsyncMock(
        return_value={"_notification_data": {"affected_sample_batch_ids": [batch_id]}}
    )
    monkeypatch.setattr(
        calibration_controller, "calibration_mz_calibrate_samples", calibrate
    )

    await calibration_controller.calibration_mz_calibrate_batch(
        sample_batch_id=batch_id,
        mz_calibration_params=MzCalibrationParams(refine_window=100),
    )

    assert calibrate.await_args.kwargs["sample_item_ids"] == [item_id]
    assert await _status(async_session_factory, batch_id) == "rematch"


# ============= The list's stale mark =============


def _tof_record(version):
    """A TOF fit's record naming collection ``version``."""
    return {
        "status": "ok",
        "mode": 0,
        "par": [2800.0, -0.5],
        "verified": True,
        "quality": {"n_points": 1, "collection": version, "node_version": 1},
    }


async def _listed_stale(client, batch_id):
    response = await client.get("/api/samples", params={"sample_batch_id": batch_id})
    assert response.status_code == 200, response.text
    (row,) = response.json()["data"]
    return row["mz_calibration_collection_stale"]


@pytest.mark.asyncio
async def test_the_version_is_the_one_the_node_records(async_session_factory):
    """Read as the TOF node reads the collection, it hashes alike."""
    ids = await _setup(async_session_factory)
    filename = "tofwerk"
    params = CalibrationFitParams(
        calibration_collection_id=ids["collection"],
        ionization_mechanism_ids=[ids["mechanism"]],
        polarity="-",
        **MzCalibrationParams(refine_window=100)
        .with_defaults(calibration_params_factory(filename=filename))
        .model_dump(),
    )
    handler = get_calibration_handler(filename, params, notification=None)
    isotopes = await handler._resolve_calibration_isotopes()

    version = await collection_version(ids["collection"], [ids["mechanism"]])

    assert version == {
        "id": ids["collection"],
        "isotopes_sha256": calibration_isotopes_sha256(isotopes),
    }


@pytest.mark.asyncio
async def test_the_list_marks_a_fit_on_an_older_version(
    owner_client, async_session_factory, ctx
):
    """Current until the collection's compounds change, stale after."""
    ids = await _setup(async_session_factory)
    now = await collection_version(ids["collection"], [ids["mechanism"]])
    batch_id, _ = await _batch(
        async_session_factory, ctx, ids["mode"], record=_tof_record(now)
    )
    assert await _listed_stale(owner_client, batch_id) is False

    added = await _another_compound(async_session_factory, ids["mechanism"])
    await _edit_compounds(owner_client, ids["collection"], [ids["compound"], added])

    assert await _listed_stale(owner_client, batch_id) is True


@pytest.mark.asyncio
async def test_the_sample_read_marks_a_fit_on_an_older_version(
    owner_client, async_session_factory, ctx
):
    """GET /samples/{id} carries the mark too: the store swaps it in for a row."""
    ids = await _setup(async_session_factory)
    now = await collection_version(ids["collection"], [ids["mechanism"]])
    _, item_id = await _batch(
        async_session_factory, ctx, ids["mode"], record=_tof_record(now)
    )

    async def read():
        response = await owner_client.get(f"/api/samples/{item_id}")
        assert response.status_code == 200, response.text
        return response.json()["data"]["mz_calibration_collection_stale"]

    assert await read() is False

    added = await _another_compound(async_session_factory, ids["mechanism"])
    await _edit_compounds(owner_client, ids["collection"], [ids["compound"], added])

    assert await read() is True


@pytest.mark.asyncio
async def test_the_list_marks_a_fit_on_a_collection_emptied_since(
    owner_client, async_session_factory, ctx
):
    """A collection that offers a fit nothing now differs from every version."""
    ids = await _setup(async_session_factory)
    now = await collection_version(ids["collection"], [ids["mechanism"]])
    batch_id, _ = await _batch(
        async_session_factory, ctx, ids["mode"], record=_tof_record(now)
    )

    await _edit_compounds(owner_client, ids["collection"], [])

    assert await _listed_stale(owner_client, batch_id) is True


@pytest.mark.asyncio
async def test_each_mode_reads_the_collection_under_its_own_mechanisms(
    async_session_factory,
):
    """Two modes on one collection: the isotopes differ with the mechanisms."""
    ids = await _setup(async_session_factory)
    other = await _setup(async_session_factory)
    second = gen_id(16)
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=second,
                ionization_mode_name=f"Calibration edit {second}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[other["mechanism"]],
                calibration_collection_id=ids["collection"],
            )
        )
        await session.commit()

    async with async_session_factory() as session:
        versions = await current_collection_versions(session, {ids["mode"], second})

    assert versions[ids["mode"]] == await collection_version(
        ids["collection"], [ids["mechanism"]]
    )
    assert versions[ids["mode"]] is not None
    assert versions[second] is None


@pytest.mark.asyncio
async def test_the_list_marks_a_fit_on_another_collection(
    owner_client, async_session_factory, ctx
):
    """The same isotopes from another collection are another version."""
    ids = await _setup(async_session_factory)
    now = await collection_version(ids["collection"], [ids["mechanism"]])
    batch_id, _ = await _batch(
        async_session_factory,
        ctx,
        ids["mode"],
        record=_tof_record({**now, "id": "another collection"}),
    )

    assert await _listed_stale(owner_client, batch_id) is True


@pytest.mark.asyncio
async def test_the_list_compares_with_the_current_version_of_the_mode(
    owner_client, async_session_factory, ctx
):
    """A sample bound under an earlier version reads its current version."""
    ids = await _setup(async_session_factory)
    other = await _setup(async_session_factory)
    earlier_version = await collection_version(ids["collection"], [ids["mechanism"]])
    async with async_session_factory() as session:
        earlier = await session.get(IonizationMode, ids["mode"])
        earlier.is_active = False
        await session.flush()
        session.add(
            IonizationMode(
                ionization_mode_id=gen_id(16),
                ionization_mode_name=earlier.ionization_mode_name,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[other["mechanism"]],
                calibration_collection_id=other["collection"],
                version=2,
                supersedes_id=ids["mode"],
            )
        )
        await session.commit()
    batch_id, _ = await _batch(
        async_session_factory, ctx, ids["mode"], record=_tof_record(earlier_version)
    )

    assert await _listed_stale(owner_client, batch_id) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        None,
        {"mode": "linear", "par": [1.0, 0.0], "quality": {"n_points": 5}},
        {"status": "unfitted", "mode": 0, "par": [2800.0, -0.5]},
        _tof_record({"id": "a collection"}),
    ],
    ids=["none", "orbitrap", "unfitted", "no hash"],
)
async def test_a_record_naming_no_version_is_not_marked(
    owner_client, async_session_factory, ctx, record
):
    ids = await _setup(async_session_factory)
    batch_id, _ = await _batch(async_session_factory, ctx, ids["mode"], record=record)

    assert await _listed_stale(owner_client, batch_id) is None
