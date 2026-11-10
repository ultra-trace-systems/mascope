"""Integration tests: a mode's edited collections reach the batches made before.

An acquisition batch is made with its mode's calibrant and diagnostic
collections, and matching reads the batch's own rows. An edit that gives the
mode another collection makes its next version; the rematch it flags, and a
file re-processed into the day's batch, swap the collections an earlier
version gave the batch for the current version's
(``align_batch_collections`` in ``api/new/ionization/modes/versions.py``).
A collection a person attached stays.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.api.controllers.match import match_controller
from mascope_backend.api.controllers.sample.files.process import service as process
from mascope_backend.api.controllers.sample.files.process.streams import (
    StoreStreams,
)
from mascope_backend.api.new.ionization.modes.versions import (
    align_batch_collections,
    lineage,
    make_version,
)
from mascope_backend.db import (
    Dataset,
    InstrumentFunction,
    IonizationMechanism,
    IonizationMode,
    MatchIsotope,
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


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1, 9, 30)

#: The collections of the world, and their kinds. ``person`` is one a person
#: attaches to a batch by hand; no mode names it.
_COLLECTIONS = {
    "cal_a": "CALIBRANTS",
    "cal_b": "CALIBRANTS",
    "diag_a": "DIAGNOSTICS",
    "diag_b": "DIAGNOSTICS",
    "person": "DIAGNOSTICS",
}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def sample_view(async_engine):
    """The match steps read samples through ``sample_view``, which
    ``create_all`` does not make (``tests/integration/api/match/conftest.py``)."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture(scope="module")
async def world(async_session_factory):
    """A dataset, a file with peaks, a mechanism, and collections of one
    compound each, whose one isotope the match step would read."""
    ids = {
        "workspace": gen_id(),
        "dataset": gen_id(),
        "file": gen_id(),
        "function": gen_id(32),
        "mech": gen_id(),
        **{key: gen_id() for key in _COLLECTIONS},
        **{f"iso_{key}": gen_id() for key in _COLLECTIONS},
    }
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Batch collections {ids['workspace']}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Batch collections",
                dataset_type="ACQUISITION",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            InstrumentFunction(
                instrument_function_id=ids["function"],
                instrument="test-orbion",
                method_file="batch-collections.meth",
                datetime_utc=_NOW,
                peakshape={},
                resolution_function=[1.0],
            )
        )
        await session.flush()
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                instrument_function_id=ids["function"],
                filename=f"test-orbion_{ids['file']}.raw",
                instrument="test-orbion",
                datetime=_NOW_NAIVE,
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+",
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids["mech"],
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ (batch collections {ids['mech']})",
            )
        )
        await session.flush()
        for key, kind in _COLLECTIONS.items():
            compound, ion = gen_id(), gen_id()
            session.add(
                TargetCollection(
                    target_collection_id=ids[key],
                    target_collection_name=f"{key} (batch collections {ids[key]})",
                    target_collection_type=kind,
                    workspace_id=None,
                )
            )
            session.add(
                TargetCompound(
                    target_compound_id=compound,
                    target_compound_name=f"{key} compound {compound}",
                    target_compound_formula="C6H12O6",
                )
            )
            await session.flush()
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=compound, target_collection_id=ids[key]
                )
            )
            session.add(
                TargetIon(
                    target_ion_id=ion,
                    target_compound_id=compound,
                    ionization_mechanism_id=ids["mech"],
                    target_ion_formula="C6H13O6+",
                )
            )
            await session.flush()
            session.add(
                TargetIsotope(
                    target_isotope_id=ids[f"iso_{key}"],
                    target_ion_id=ion,
                    target_isotope_formula="C6H13O6+",
                    mz=181.07,
                    relative_abundance=1.0,
                    resolution="HIGH",
                )
            )
        await session.commit()
    return ids


@pytest_asyncio.fixture(params=[False, True], ids=["mode's own", "and a person's"])
async def batch(request, async_session_factory, world):
    """The day's acquisition batch of a mode, made with the mode's collections
    and holding one sample; with ``person`` attached by hand as well when the
    param says so."""
    name = f"Collections {gen_id(6)}"
    ctx = {
        "mode_id": gen_id(),
        "batch_id": gen_id(),
        "item_id": gen_id(),
        "name": name,
        "token": f"tok{gen_id(6)}",
        "person": request.param,
    }
    attached = ["cal_a", "diag_a"] + (["person"] if request.param else [])
    async with async_session_factory() as session:
        sample_file = await session.get(SampleFile, world["file"])
        session.add(
            IonizationMode(
                ionization_mode_id=ctx["mode_id"],
                ionization_mode_name=name,
                ionization_mode_token=ctx["token"],
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[world["mech"]],
                calibration_collection_id=world["cal_a"],
                diagnostic_collection_id=world["diag_a"],
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ctx["batch_id"],
                dataset_id=world["dataset"],
                sample_batch_name=process.acquisition_batch_name(sample_file, name),
                sample_batch_type="ACQUISITION",
                polarity="+",
                status="ready",
                sample_batch_utc_created=_NOW,
            )
        )
        await session.flush()
        for key in attached:
            session.add(
                TargetCollectionInSampleBatch(
                    target_collection_id=world[key], sample_batch_id=ctx["batch_id"]
                )
            )
        session.add(
            SampleItem(
                sample_item_id=ctx["item_id"],
                sample_batch_id=ctx["batch_id"],
                sample_file_id=world["file"],
                sample_item_name="Collections item",
                sample_item_type="ACQUISITION",
                sample_item_attributes={},
                polarity="+",
                tic=1000.0,
                t0=0.0,
                t1=60.0,
                ionization_mode_id=ctx["mode_id"],
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()
    yield ctx
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ctx["batch_id"])
        )
        versions = await lineage(session, ctx["mode_id"])
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(versions)
            )
        )
        await session.commit()


async def _edit(admin_client, world, batch, **changes) -> str:
    """Edit the batch's mode through the route, as a person would."""
    body = {
        "ionization_mode_name": batch["name"],
        "ionization_mode_token": batch["token"],
        "ionization_mode_polarity": "+",
        "ionization_mechanism_ids": [world["mech"]],
        "calibration_collection_id": world["cal_a"],
        "diagnostic_collection_id": world["diag_a"],
        **changes,
    }
    resp = await admin_client.patch(
        f"/api/ionization/modes/{batch['mode_id']}", json=body
    )
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["data"]["ionization_mode_id"]
    assert new_id != batch["mode_id"]
    return new_id


async def _collections(async_session_factory, world, batch_id) -> set[str]:
    """The batch's collections, by their key in the world."""
    names = {collection_id: key for key, collection_id in world.items()}
    async with async_session_factory() as session:
        rows = await session.execute(
            select(TargetCollectionInSampleBatch.target_collection_id).where(
                TargetCollectionInSampleBatch.sample_batch_id == batch_id
            )
        )
        return {names[collection_id] for collection_id in rows.scalars()}


async def _status(async_session_factory, batch_id) -> str:
    async with async_session_factory() as session:
        return (await session.get(SampleBatch, batch_id)).status


async def _set_status(async_session_factory, batch_id, status) -> None:
    async with async_session_factory() as session:
        (await session.get(SampleBatch, batch_id)).status = status
        await session.commit()


def _with_person(batch, keys: set[str]) -> set[str]:
    return keys | ({"person"} if batch["person"] else set())


# --- a rematch ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rematch_matches_against_the_current_versions_collections(
    admin_client, async_session_factory, world, batch, monkeypatch
):
    """The edit flags the batch; its rematch gives it the new diagnostic
    collection, takes off the old one's matches as orphans, and matches the
    sample against the collections it has just set."""
    async with async_session_factory() as session:
        session.add(
            MatchIsotope(
                match_isotope_id=gen_id(32),
                target_isotope_id=world["iso_diag_a"],
                sample_item_id=batch["item_id"],
                sample_peak_id=gen_id(20),
                sample_peak_mz=181.07,
                sample_peak_intensity=1.0,
                sample_peak_intensity_relative=1.0,
                sample_peak_tof=0.0,
                match_abundance_error=0.0,
                match_mz_error=0.0,
                match_score=0.9,
                match_isotope_utc_created=_NOW,
            )
        )
        await session.commit()
    await _edit(admin_client, world, batch, diagnostic_collection_id=world["diag_b"])
    assert await _status(async_session_factory, batch["batch_id"]) == "rematch"

    isotope_keys = {world[f"iso_{key}"]: key for key in _COLLECTIONS}
    matched: set[str] = set()

    async def compute(sample, target_isotopes_df, *args, **kwargs):
        matched.update(isotope_keys[i] for i in target_isotopes_df["target_isotope_id"])
        return {"match_isotopes": pd.DataFrame()}

    monkeypatch.setattr(
        match_controller, "compute_and_create_sample_match_isotope_data", compute
    )
    monkeypatch.setattr(
        match_controller, "send_progress_user_notification", AsyncMock()
    )
    await match_controller.rematch_batch(batch["batch_id"], process_id=gen_id(8))

    assert await _collections(
        async_session_factory, world, batch["batch_id"]
    ) == _with_person(batch, {"cal_a", "diag_b"})
    assert matched == _with_person(batch, {"cal_a", "diag_b"})
    async with async_session_factory() as session:
        left = (
            (
                await session.execute(
                    select(MatchIsotope.target_isotope_id).where(
                        MatchIsotope.sample_item_id == batch["item_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert left == []


@pytest.mark.asyncio
async def test_a_mode_left_with_no_diagnostic_collection_takes_it_off(
    async_session_factory, world, batch
):
    """The route keeps a collection it is sent none for, so the version that
    drops it is minted here, as an edit would mint it."""
    async with async_session_factory() as session:
        current = await session.get(IonizationMode, batch["mode_id"])
        current.is_active = False
        await session.flush()
        session.add(make_version(current, {"diagnostic_collection_id": None}))
        await session.commit()

    async with async_session_factory() as session:
        assert await align_batch_collections(session, batch["batch_id"])
        await session.commit()
    assert await _collections(
        async_session_factory, world, batch["batch_id"]
    ) == _with_person(batch, {"cal_a"})


@pytest.mark.asyncio
async def test_a_collection_the_current_version_still_names_stays(
    admin_client, async_session_factory, world, batch
):
    """The old diagnostic collection is the new calibrant one: it stays, and
    the new diagnostic collection joins it."""
    await _edit(
        admin_client,
        world,
        batch,
        calibration_collection_id=world["diag_a"],
        diagnostic_collection_id=world["diag_b"],
    )

    async with async_session_factory() as session:
        assert await align_batch_collections(session, batch["batch_id"])
        await session.commit()
    assert await _collections(
        async_session_factory, world, batch["batch_id"]
    ) == _with_person(batch, {"diag_a", "diag_b"})


@pytest.mark.asyncio
async def test_a_collection_a_person_took_off_stays_off(
    admin_client, async_session_factory, world, batch
):
    """Nothing is added where no earlier version's collection is attached: a
    person who took the diagnostic collection off meant it."""
    async with async_session_factory() as session:
        await session.execute(
            delete(TargetCollectionInSampleBatch).where(
                TargetCollectionInSampleBatch.sample_batch_id == batch["batch_id"],
                TargetCollectionInSampleBatch.target_collection_id == world["diag_a"],
            )
        )
        await session.commit()
    await _edit(admin_client, world, batch, diagnostic_collection_id=world["diag_b"])

    async with async_session_factory() as session:
        assert not await align_batch_collections(session, batch["batch_id"])
    assert await _collections(
        async_session_factory, world, batch["batch_id"]
    ) == _with_person(batch, {"cal_a"})


@pytest.mark.asyncio
async def test_a_batch_of_a_mode_never_edited_is_left_alone(
    async_session_factory, world, batch
):
    async with async_session_factory() as session:
        assert not await align_batch_collections(session, batch["batch_id"])
    assert await _collections(
        async_session_factory, world, batch["batch_id"]
    ) == _with_person(batch, {"cal_a", "diag_a"})


# --- re-processing into the day's batch ----------------------------------------


async def _file_into_the_days_batch(async_session_factory, world, mode_id, monkeypatch):
    """Run the pipeline's filing of the world's file under ``mode_id``, up to
    the items it would create."""
    monkeypatch.setattr(
        process, "read_store_streams", AsyncMock(return_value=StoreStreams())
    )
    create_items = AsyncMock(return_value={"data": []})
    monkeypatch.setattr(process, "create_sample_items", create_items)
    async with async_session_factory() as session:
        sample_file = await session.get(SampleFile, world["file"])
        mode = await session.get(IonizationMode, mode_id)
    _, batches, _, _ = await process.create_acquisition_batches_and_items(
        sample_file=sample_file,
        dataset_id=world["dataset"],
        ionization_modes=[mode],
        provenance={},
    )
    return batches


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"), [("ready", "rematch"), ("recalibrate", "recalibrate")]
)
async def test_a_file_reprocessed_into_the_days_batch_brings_the_new_collections(
    admin_client, async_session_factory, world, batch, monkeypatch, before, after
):
    """The batch was rematched before its rematch brought collections along,
    so it holds the old ones and waits for nothing. Re-processing a file into
    it swaps them and flags it for a rematch, or keeps it waiting for a
    recalibration that ends in one."""
    new_id = await _edit(
        admin_client, world, batch, diagnostic_collection_id=world["diag_b"]
    )
    await _set_status(async_session_factory, batch["batch_id"], before)

    batches = await _file_into_the_days_batch(
        async_session_factory, world, new_id, monkeypatch
    )

    assert [b["sample_batch_id"] for b in batches] == [batch["batch_id"]]
    assert await _collections(
        async_session_factory, world, batch["batch_id"]
    ) == _with_person(batch, {"cal_a", "diag_b"})
    assert await _status(async_session_factory, batch["batch_id"]) == after


@pytest.mark.asyncio
async def test_a_batch_whose_only_file_is_reprocessed_takes_the_files_mode(
    admin_client, async_session_factory, world, batch, monkeypatch
):
    """Re-processing deletes a file's samples before it files new ones, so a
    batch holding that one file has no sample left to say which mode it is
    of: the mode the file is being filed under says it."""
    new_id = await _edit(
        admin_client, world, batch, diagnostic_collection_id=world["diag_b"]
    )
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id == batch["item_id"])
        )
        await session.commit()

    await _file_into_the_days_batch(async_session_factory, world, new_id, monkeypatch)

    assert await _collections(
        async_session_factory, world, batch["batch_id"]
    ) == _with_person(batch, {"cal_a", "diag_b"})


@pytest.mark.asyncio
async def test_a_file_filed_into_a_batch_already_current_flags_nothing(
    async_session_factory, world, batch, monkeypatch
):
    await _file_into_the_days_batch(
        async_session_factory, world, batch["mode_id"], monkeypatch
    )

    assert await _collections(
        async_session_factory, world, batch["batch_id"]
    ) == _with_person(batch, {"cal_a", "diag_a"})
    assert await _status(async_session_factory, batch["batch_id"]) == "ready"
