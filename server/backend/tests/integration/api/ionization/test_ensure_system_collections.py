"""
Tests: a start seeds the calibrant and diagnostic collections of the chemistries
Mascope ships, and points each seeded mode at its own.

The seed runs at every start, on servers whose collections are their own data,
so what it leaves alone matters as much as what it creates: a mode a site
pointed at a collection of its own keeps it, a seeded collection the site
edited stays as it was edited, and a second start changes nothing. A seeded
collection still holding what an earlier release seeded is the seed's, and is
brought up to date the way a person's edit would be.

These run against a database of their own, as a first start finds it, for the
reason ``test_ensure_system_mechanisms`` gives: the shared integration database
carries rows other tests left.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

import mascope_backend.db as db_module
import mascope_backend.db.admin.ionization.ensure_system_modes as seed_module
from mascope_backend.api.new.ionization.modes.schema import IonizationModeUpdate
from mascope_backend.api.new.ionization.modes.service import update_ionization_mode
from mascope_backend.api.new.ionization.modes.util import (
    fetch_listable_ionization_modes,
)
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    MethodBinding,
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
from mascope_backend.db.admin.ionization.ensure_system_modes import (
    ensure_system_ionization,
    ensure_system_ionization_collections,
)
from mascope_backend.ionization_catalogue import (
    SYSTEM_MODES,
    SeededCollection,
    SeededCompound,
    seeded_collections,
)


_NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
_SEEDED = {collection.collection_id: collection for collection in seeded_collections()}
_BROMIDE_CALIBRANTS = _SEEDED["sysBromideCal"]
_BROMIDE_MODE = "sysBromide"


@pytest_asyncio.fixture(scope="module")
async def seed_db_factory(async_engine_factory):
    """A database of its own: the schema, and nothing a start has written."""
    engine = await async_engine_factory("integration_seeded_collections")
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def fresh_db(seed_db_factory, bind_db, monkeypatch):
    """The application pointed at that database, emptied of earlier tests' rows.

    Requests ``bind_db`` so that it runs first: this rebinds what it bound.
    """
    async with seed_db_factory() as session:
        for model in (
            MethodBinding,
            TargetCollectionInSampleBatch,
            SampleItem,
            SampleFile,
            SampleBatch,
            Dataset,
            Workspace,
            TargetCompoundInTargetCollection,
            TargetCollection,
            TargetIsotope,
            TargetIon,
            TargetCompound,
            IonizationMode,
            IonizationMechanism,
        ):
            await session.execute(delete(model))
        await session.commit()
    monkeypatch.setattr(db_module, "ASYNC_SESSION_MAKER", seed_db_factory)
    return seed_db_factory


async def _collections(factory) -> dict[str, TargetCollection]:
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(TargetCollection).options(
                        selectinload(TargetCollection.target_compound).selectinload(
                            TargetCompoundInTargetCollection.target_compound
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    return {row.target_collection_id: row for row in rows}


def _content(row: TargetCollection) -> tuple:
    """What a collection holds, as a person reads it in the editor."""
    return (
        row.target_collection_name,
        row.target_collection_description,
        row.target_collection_type,
        row.workspace_id,
        sorted(
            (
                link.target_compound.target_compound_name,
                link.target_compound.target_compound_formula,
            )
            for link in row.target_compound
        ),
    )


def _seeded_content(collection: SeededCollection) -> tuple:
    return (
        collection.name,
        collection.description,
        collection.collection_type,
        None,
        sorted((compound.name, compound.formula) for compound in collection.compounds),
    )


async def _modes(factory) -> dict[str, IonizationMode]:
    async with factory() as session:
        rows = (await session.execute(select(IonizationMode))).scalars().all()
    return {row.ionization_mode_id: row for row in rows}


async def _snapshot(factory) -> tuple:
    """Every row the collection pass writes or points at."""
    collections = await _collections(factory)
    modes = await _modes(factory)
    async with factory() as session:
        compounds = sorted(
            (
                await session.execute(
                    select(
                        TargetCompound.target_compound_id,
                        TargetCompound.target_compound_name,
                        TargetCompound.target_compound_formula,
                    )
                )
            ).all()
        )
        ions = await session.scalar(select(func.count()).select_from(TargetIon))
        isotopes = await session.scalar(select(func.count()).select_from(TargetIsotope))
    return (
        {key: _content(row) for key, row in collections.items()},
        {
            key: (mode.calibration_collection_id, mode.diagnostic_collection_id)
            for key, mode in modes.items()
        },
        compounds,
        ions,
        isotopes,
    )


async def _seed_mechanisms_and_modes():
    await seed_module.ensure_system_ionization_mechanisms()
    await seed_module.ensure_system_ionization_modes()


async def _site_collection(factory, collection_id="siteCalibrants") -> str:
    """A calibrants collection of the site's own, with one compound."""
    async with factory() as session:
        session.add(
            TargetCompound(
                target_compound_id="siteCompound",
                target_compound_name="our calibrant",
                target_compound_formula="C10H16O3",
            )
        )
        session.add(
            TargetCollection(
                target_collection_id=collection_id,
                target_collection_name="Our bromide calibrants",
                target_collection_type="CALIBRANTS",
                workspace_id=None,
            )
        )
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id="siteCompound",
                target_collection_id=collection_id,
            )
        )
        await session.commit()
    return collection_id


@pytest.mark.asyncio
async def test_a_first_start_seeds_both_collections_of_every_profile(fresh_db):
    counts = await ensure_system_ionization()

    assert counts["collections"]["created"] == len(_SEEDED)
    assert counts["collections"]["pointed"] == len(_SEEDED)
    collections = await _collections(fresh_db)
    modes = await _modes(fresh_db)
    for collection_id, seeded in _SEEDED.items():
        # Global, like the mode that reads it: an acquisition batch of any
        # instrument's workspace may carry it.
        assert _content(collections[collection_id]) == _seeded_content(seeded)
        mode = next(m for m in modes.values() if m.system_key == seeded.system_key)
        assert getattr(mode, seeded.mode_field) == collection_id

    # The chemistries without a reagent carry none.
    with_collections = {seeded.system_key for seeded in _SEEDED.values()}
    for system_key, mode_id, *_ in SYSTEM_MODES:
        if system_key not in with_collections:
            assert modes[mode_id].calibration_collection_id is None
            assert modes[mode_id].diagnostic_collection_id is None


@pytest.mark.asyncio
async def test_the_compounds_carry_their_ions_and_isotopes(fresh_db):
    """Built as a compound added in the editor is, so the fit can read them:
    under the mode's own mechanisms, the bromide series and nothing else."""
    await ensure_system_ionization()
    mode = (await _modes(fresh_db))[_BROMIDE_MODE]

    async with fresh_db() as session:
        formulas = set(
            (
                await session.execute(
                    select(TargetIon.target_ion_formula)
                    .join(
                        TargetCompoundInTargetCollection,
                        TargetCompoundInTargetCollection.target_compound_id
                        == TargetIon.target_compound_id,
                    )
                    .join(
                        TargetIsotope,
                        TargetIsotope.target_ion_id == TargetIon.target_ion_id,
                    )
                    .where(
                        TargetCompoundInTargetCollection.target_collection_id
                        == _BROMIDE_CALIBRANTS.collection_id,
                        TargetIon.ionization_mechanism_id.in_(
                            mode.ionization_mechanism_ids
                        ),
                    )
                )
            ).scalars()
        )
    assert formulas == {"Br-", "Br2-", "Br3-"}


@pytest.mark.asyncio
async def test_a_second_start_changes_nothing(fresh_db):
    await ensure_system_ionization()
    before = await _snapshot(fresh_db)

    counts = await ensure_system_ionization_collections()

    assert await _snapshot(fresh_db) == before
    assert counts["held"] == len(_SEEDED)
    assert counts["created"] == counts["updated"] == counts["pointed"] == 0


@pytest.mark.asyncio
async def test_a_mode_pointed_at_the_sites_own_collection_keeps_it(fresh_db):
    """The site's collection wins, and is not touched either."""
    await _seed_mechanisms_and_modes()
    site = await _site_collection(fresh_db)
    async with fresh_db() as session:
        await session.execute(
            update(IonizationMode)
            .where(IonizationMode.ionization_mode_id == _BROMIDE_MODE)
            .values(calibration_collection_id=site)
        )
        await session.commit()
    before = _content((await _collections(fresh_db))[site])

    counts = await ensure_system_ionization_collections()

    modes = await _modes(fresh_db)
    collections = await _collections(fresh_db)
    assert modes[_BROMIDE_MODE].calibration_collection_id == site
    assert _content(collections[site]) == before
    # Its own still seeded, for the site to point back at, and the mode's
    # other collection pointed as on any server.
    assert _BROMIDE_CALIBRANTS.collection_id in collections
    assert modes[_BROMIDE_MODE].diagnostic_collection_id == "sysBromideDia"
    assert counts["pointed"] == len(_SEEDED) - 1


async def _remove_a_compound(factory):
    async with factory() as session:
        link = (
            await session.execute(
                select(TargetCompoundInTargetCollection)
                .join(TargetCompound)
                .where(
                    TargetCompoundInTargetCollection.target_collection_id
                    == _BROMIDE_CALIBRANTS.collection_id,
                    TargetCompound.target_compound_name == "[Br3]-",
                )
            )
        ).scalar_one()
        await session.delete(link)
        await session.commit()


async def _add_a_compound(factory):
    async with factory() as session:
        session.add(
            TargetCompound(
                target_compound_id="siteAdded",
                target_compound_name="our extra anchor",
                target_compound_formula="Br4",
            )
        )
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id="siteAdded",
                target_collection_id=_BROMIDE_CALIBRANTS.collection_id,
            )
        )
        await session.commit()


async def _rename(factory):
    async with factory() as session:
        await session.execute(
            update(TargetCollection)
            .where(
                TargetCollection.target_collection_id
                == _BROMIDE_CALIBRANTS.collection_id
            )
            .values(target_collection_name="Our bromide anchors")
        )
        await session.commit()


async def _edit_a_formula(factory):
    async with factory() as session:
        await session.execute(
            update(TargetCompound)
            .where(TargetCompound.target_compound_name == "[Br3]-")
            .values(target_compound_formula="Br3")
        )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edit", "edited"),
    [
        (_remove_a_compound, 1),
        (_add_a_compound, 1),
        (_rename, 1),
        # The compound is the diagnostics' too, and an edit of a compound in
        # place is an edit of every collection that holds it.
        (_edit_a_formula, 2),
    ],
    ids=["compound-removed", "compound-added", "renamed", "formula-edited"],
)
async def test_a_seeded_collection_the_site_edited_is_left_as_it_is(
    fresh_db, edit, edited
):
    await ensure_system_ionization()
    await edit(fresh_db)
    before = await _snapshot(fresh_db)

    counts = await ensure_system_ionization_collections()

    assert await _snapshot(fresh_db) == before
    assert counts["edited"] == edited
    assert counts["held"] == len(_SEEDED) - edited


#: What an earlier release seeded the bromide calibrants with: the series
#: without its trimer, under another description.
_EARLIER = SeededCollection(
    collection_id=_BROMIDE_CALIBRANTS.collection_id,
    system_key=_BROMIDE_CALIBRANTS.system_key,
    mode_field=_BROMIDE_CALIBRANTS.mode_field,
    name=_BROMIDE_CALIBRANTS.name,
    description="Shipped by Mascope: an earlier wording.",
    collection_type=_BROMIDE_CALIBRANTS.collection_type,
    compounds=(SeededCompound("[Br]-", "()"), SeededCompound("[Br2]-", "Br")),
)


async def _seed_as_an_earlier_release(monkeypatch):
    """The bromide calibrants as that release wrote them, every other as now."""
    earlier = tuple(
        _EARLIER if seeded.collection_id == _EARLIER.collection_id else seeded
        for seeded in seeded_collections()
    )
    with monkeypatch.context() as patched:
        patched.setattr(seed_module, "seeded_collections", lambda: earlier)
        await ensure_system_ionization()


async def _batch_serving(factory, collection_id: str) -> str:
    """A batch the collection serves, settled after its last match.

    With a sample in it: a batch with none has nothing to match, and stays
    ready whatever its collections do.
    """
    async with factory() as session:
        session.add(
            Workspace(
                workspace_id="wsAcquisition",
                workspace_name="Acquisition test instrument",
                is_system=True,
            )
        )
        session.add(
            Dataset(
                dataset_id="dsAcquisition",
                workspace_id="wsAcquisition",
                dataset_name="2026",
                dataset_type="ACQUISITION",
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id="batchBromide",
                dataset_id="dsAcquisition",
                sample_batch_name="2026-10-27 Bromide, negative acquisition",
                sample_batch_type="ACQUISITION",
                polarity="-",
                status="ready",
            )
        )
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=collection_id, sample_batch_id="batchBromide"
            )
        )
        session.add(
            SampleFile(
                sample_file_id="fileBromide",
                filename="SeedTest_2026.10.27_12h00m00s_bromide.raw",
                instrument="SeedTest",
                datetime=_NOW.replace(tzinfo=None),
                datetime_utc=_NOW,
                length=60.0,
                range=[50.0, 500.0],
                polarity="-",
                instrument_type="tof",
            )
        )
        await session.flush()
        session.add(
            SampleItem(
                sample_item_id="itemBromide",
                sample_batch_id="batchBromide",
                sample_file_id="fileBromide",
                sample_item_name="2026-10-27 12:00:00",
                sample_item_type="ACQUISITION",
                sample_item_attributes={},
                polarity="-",
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()
    return "batchBromide"


@pytest.mark.asyncio
async def test_a_collection_an_earlier_release_seeded_is_brought_up_to_date(
    fresh_db, monkeypatch
):
    """As a person's edit would be: the batches it serves are re-matched."""
    await _seed_as_an_earlier_release(monkeypatch)
    batch = await _batch_serving(fresh_db, _EARLIER.collection_id)
    monkeypatch.setattr(seed_module, "SEEDED_BEFORE", frozenset({_EARLIER.digest}))

    counts = await ensure_system_ionization_collections()

    collections = await _collections(fresh_db)
    assert _content(collections[_EARLIER.collection_id]) == _seeded_content(
        _BROMIDE_CALIBRANTS
    )
    assert counts["updated"] == 1
    assert counts["held"] == len(_SEEDED) - 1
    async with fresh_db() as session:
        status = await session.scalar(
            select(SampleBatch.status).where(SampleBatch.sample_batch_id == batch)
        )
    assert status == "rematch"


@pytest.mark.asyncio
async def test_without_its_digest_an_earlier_seed_reads_as_an_edit(
    fresh_db, monkeypatch
):
    """The record of what earlier releases seeded is what tells them apart."""
    await _seed_as_an_earlier_release(monkeypatch)

    counts = await ensure_system_ionization_collections()

    collections = await _collections(fresh_db)
    assert _content(collections[_EARLIER.collection_id]) == _seeded_content(_EARLIER)
    assert counts["edited"] == 1


@pytest.mark.asyncio
async def test_a_deleted_seeded_collection_comes_back(fresh_db):
    """Deleting it lets go of the mode, so the next start seeds it and points
    the mode at it again, as it creates a deleted mechanism again."""
    await ensure_system_ionization()
    async with fresh_db() as session:
        await session.execute(
            delete(TargetCollection).where(
                TargetCollection.target_collection_id
                == _BROMIDE_CALIBRANTS.collection_id
            )
        )
        await session.commit()
    assert (await _modes(fresh_db))[_BROMIDE_MODE].calibration_collection_id is None

    counts = await ensure_system_ionization_collections()

    collections = await _collections(fresh_db)
    assert _content(collections[_BROMIDE_CALIBRANTS.collection_id]) == (
        _seeded_content(_BROMIDE_CALIBRANTS)
    )
    assert (await _modes(fresh_db))[_BROMIDE_MODE].calibration_collection_id == (
        _BROMIDE_CALIBRANTS.collection_id
    )
    assert counts["created"] == counts["pointed"] == 1


async def _edit_mode(mode_id: str, **changes) -> str:
    """Edit a mode as the settings pane does, echoing the rest of it back.

    :return: The id of the version the edit leaves current.
    """
    async with db_module.async_session() as session:
        mode = await session.get(IonizationMode, mode_id)
    fields = {
        "ionization_mode_name": mode.ionization_mode_name,
        "ionization_mode_polarity": mode.ionization_mode_polarity,
        "ionization_mechanism_ids": mode.ionization_mechanism_ids,
    }
    edited = await update_ionization_mode(
        mode_id, IonizationModeUpdate(**fields, **changes)
    )
    return edited["data"]["ionization_mode_id"]


@pytest.mark.asyncio
async def test_a_choice_made_while_the_pass_runs_stands(fresh_db, monkeypatch):
    """A site pointing a mode at its own collection while the pass runs keeps
    it: the pass reads the mode's current version again when it points it -
    the one the site's edit made - and points it only where it still points
    at none. The version the edit replaced is left as it was."""
    await _seed_mechanisms_and_modes()
    site = await _site_collection(fresh_db)
    create = seed_module._create_collection
    current: list[str] = []

    async def create_while_the_site_chooses(collection):
        await create(collection)
        if collection.collection_id == _BROMIDE_CALIBRANTS.collection_id:
            current.append(
                await _edit_mode(_BROMIDE_MODE, calibration_collection_id=site)
            )

    monkeypatch.setattr(
        seed_module, "_create_collection", create_while_the_site_chooses
    )

    counts = await ensure_system_ionization_collections()

    modes = await _modes(fresh_db)
    (edited,) = current
    assert modes[edited].calibration_collection_id == site
    assert modes[edited].diagnostic_collection_id == "sysBromideDia"
    assert modes[edited].version == 2
    assert modes[_BROMIDE_MODE].calibration_collection_id is None
    assert modes[_BROMIDE_MODE].diagnostic_collection_id is None
    assert counts["pointed"] == len(_SEEDED) - 1
    assert counts["versioned"] == 0


@pytest.mark.asyncio
async def test_a_collection_that_fails_is_not_pointed_at_and_comes_next_start(
    fresh_db, monkeypatch
):
    """One collection failing costs the others nothing, and leaves its mode
    pointing at nothing rather than at a collection that is not there."""
    await _seed_mechanisms_and_modes()
    create = seed_module._create_collection

    async def fail_the_bromide_calibrants(collection):
        if collection.collection_id == _BROMIDE_CALIBRANTS.collection_id:
            raise RuntimeError("the database went away")
        await create(collection)

    with monkeypatch.context() as patched:
        patched.setattr(seed_module, "_create_collection", fail_the_bromide_calibrants)
        counts = await ensure_system_ionization_collections()

    assert counts["failed"] == 1
    assert counts["created"] == counts["pointed"] == len(_SEEDED) - 1
    assert _BROMIDE_CALIBRANTS.collection_id not in await _collections(fresh_db)
    assert (await _modes(fresh_db))[_BROMIDE_MODE].calibration_collection_id is None

    counts = await ensure_system_ionization_collections()

    assert counts["created"] == counts["pointed"] == 1
    assert (await _modes(fresh_db))[_BROMIDE_MODE].calibration_collection_id == (
        _BROMIDE_CALIBRANTS.collection_id
    )


@pytest.mark.asyncio
async def test_a_chemistry_that_was_not_seeded_gets_no_collections(fresh_db):
    """Its name taken by a mode of the site's, the bromide chemistry has no
    mode here to read its collections."""
    async with fresh_db() as session:
        session.add(
            IonizationMode(
                ionization_mode_id="siteBromide",
                ionization_mode_name="Bromide, negative",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[],
            )
        )
        await session.commit()

    counts = await ensure_system_ionization()

    collections = await _collections(fresh_db)
    assert "sysBromideCal" not in collections
    assert "sysBromideDia" not in collections
    assert counts["collections"]["no_mode"] == 2
    assert (await _modes(fresh_db))["siteBromide"].calibration_collection_id is None


@pytest.mark.asyncio
async def test_a_chemistry_only_a_superseded_version_holds_gets_no_collections(
    fresh_db,
):
    """A version an edit replaced keeps its chemistry's key, and reads nothing:
    with no current version of the chemistry, there is no mode to read its
    collections."""
    await _seed_mechanisms_and_modes()
    async with fresh_db() as session:
        await session.execute(
            update(IonizationMode)
            .where(IonizationMode.ionization_mode_id == _BROMIDE_MODE)
            .values(is_active=False)
        )
        await session.commit()

    counts = await ensure_system_ionization_collections()

    collections = await _collections(fresh_db)
    assert "sysBromideCal" not in collections
    assert "sysBromideDia" not in collections
    assert counts["no_mode"] == 2
    assert counts["created"] == counts["pointed"] == len(_SEEDED) - 2


@pytest.mark.asyncio
async def test_a_mode_an_edit_replaced_has_its_current_version_pointed(fresh_db):
    """A site that set its reagent's purity before this release holds the
    chemistry's second version. That one is pointed, in place, since no sample
    is bound under it; the first is left as the samples' record."""
    await _seed_mechanisms_and_modes()
    current = await _edit_mode(_BROMIDE_MODE, label_purity=0.99)

    counts = await ensure_system_ionization_collections()

    modes = await _modes(fresh_db)
    assert modes[current].calibration_collection_id == "sysBromideCal"
    assert modes[current].diagnostic_collection_id == "sysBromideDia"
    assert (modes[current].version, modes[current].is_active) == (2, True)
    assert modes[_BROMIDE_MODE].calibration_collection_id is None
    assert modes[_BROMIDE_MODE].diagnostic_collection_id is None
    assert len(modes) == len(SYSTEM_MODES) + 1
    assert counts["pointed"] == len(_SEEDED)
    assert counts["versioned"] == 0


async def _routed_to_bromide(factory) -> None:
    """A file processed under the bromide chemistry before it carried
    collections, and the method binding that routes its method to it."""
    batch = await _batch_serving(factory, "siteCalibrants")
    async with factory() as session:
        await session.execute(
            update(SampleItem)
            .where(SampleItem.sample_batch_id == batch)
            .values(ionization_mode_id=_BROMIDE_MODE)
        )
        session.add(
            MethodBinding(
                method_binding_id="bindingBromide",
                binding_key="seed-test-bromide",
                instrument="SeedTest",
                method_key="m.meth",
                signature_class="TOF -",
                ionization_mode_id=_BROMIDE_MODE,
                candidate_mode_id=_BROMIDE_MODE,
                chemistry_keys=["bromide"],
                state="learned",
                source="token",
                first_seen=_NOW,
                last_seen=_NOW,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_mode_samples_are_bound_under_takes_its_collections_as_a_new_version(
    fresh_db,
):
    """What the samples were processed under is not changed under them: the
    collections go on the next version, as an edit's would, and new files of
    the method route to it. The samples keep their version until they are
    processed again, and the seed flags no batch: it is not the site's edit."""
    await _seed_mechanisms_and_modes()
    await _site_collection(fresh_db)
    await _routed_to_bromide(fresh_db)

    counts = await ensure_system_ionization_collections()

    modes = await _modes(fresh_db)
    first = modes[_BROMIDE_MODE]
    (successor,) = [mode for mode in modes.values() if mode.supersedes_id]
    assert (first.is_active, first.version) == (False, 1)
    assert first.calibration_collection_id is None
    assert first.diagnostic_collection_id is None
    assert (successor.is_active, successor.version) == (True, 2)
    assert successor.supersedes_id == _BROMIDE_MODE
    assert successor.system_key == first.system_key
    assert successor.ionization_mechanism_ids == first.ionization_mechanism_ids
    # One version for both collections.
    assert successor.calibration_collection_id == "sysBromideCal"
    assert successor.diagnostic_collection_id == "sysBromideDia"
    assert counts["pointed"] == len(_SEEDED)
    assert counts["versioned"] == 1

    async with fresh_db() as session:
        bound = await session.scalar(
            select(SampleItem.ionization_mode_id).where(
                SampleItem.sample_item_id == "itemBromide"
            )
        )
        binding = await session.get(MethodBinding, "bindingBromide")
        status = await session.scalar(
            select(SampleBatch.status).where(
                SampleBatch.sample_batch_id == "batchBromide"
            )
        )
    assert bound == _BROMIDE_MODE
    assert binding.ionization_mode_id == successor.ionization_mode_id
    assert binding.candidate_mode_id == successor.ionization_mode_id
    assert status == "ready"

    # The second start finds the new version pointed, and mints none.
    again = await ensure_system_ionization_collections()
    assert again["pointed"] == again["versioned"] == 0
    assert len(await _modes(fresh_db)) == len(modes)


@pytest.mark.asyncio
async def test_the_listing_still_leaves_the_shipped_chemistries_out(fresh_db):
    """Their own collections are not the site's, so they do not adopt them; a
    collection of the site's does, as it did."""
    await ensure_system_ionization()
    assert {
        mode.ionization_mode_id for mode in await fetch_listable_ionization_modes()
    } == set()

    site = await _site_collection(fresh_db)
    async with fresh_db() as session:
        await session.execute(
            update(IonizationMode)
            .where(IonizationMode.ionization_mode_id == _BROMIDE_MODE)
            .values(diagnostic_collection_id=site)
        )
        await session.commit()

    assert {
        mode.ionization_mode_id for mode in await fetch_listable_ionization_modes()
    } == {_BROMIDE_MODE}
