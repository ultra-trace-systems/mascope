"""
Tests: what the rebuild of peak stores their files no longer read back does
to the database.

The rest of ``rebuild_stale_peak_stores`` is tested hermetically
(``tests/unit/db/test_rebuild_stale_peak_stores.py`` and
``tests/unit/libraries/signal/test_stale_store_rebuild.py``). What only a
database can show: that it offers Orbitrap files and no others; that a
file's samples are found in every batch that holds one - the pipeline's and
every copy a person made, which a re-processing refuses to touch and the
rebuild keeps; and what the run's own steps write, around a stand-in for the
detection itself: the batches' status, the samples' matches, their TICs and
the batches' aligned peaks.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

import mascope_signal.ms2_store as m_ms2_store
from mascope_backend.api.controllers.sample.items import sample_items_controller
from mascope_backend.api.new.ms2 import inventory
from mascope_backend.db import (
    Dataset,
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
from mascope_backend.db.scripts import rebuild_stale_peak_stores as script


@pytest_asyncio.fixture
async def files(async_session_factory):
    """Adds sample files, each with the samples asked for, and cleans up.

    A pipeline sample is the shape ``_pipeline_item`` reads: an ACQUISITION
    item, in an ACQUISITION batch, of an ACQUISITION dataset, in a system
    workspace. A person's is an analysis sample in a batch of their own
    workspace's dataset.
    """
    made: dict[str, list] = {"files": [], "items": [], "workspaces": []}
    batches: dict[str, SampleBatch] = {}

    async def batch_of(kind: str) -> SampleBatch:
        if kind not in batches:
            pipeline = kind == "pipeline"
            workspace = Workspace(
                workspace_id=gen_id(),
                workspace_name=f"Stale store rebuild {kind} {gen_id(6)}",
                is_system=pipeline,
            )
            dataset = Dataset(
                dataset_id=gen_id(),
                workspace_id=workspace.workspace_id,
                dataset_name=f"Dataset {gen_id(6)}",
                dataset_type="ACQUISITION" if pipeline else "ANALYSIS",
                instrument=f"instrument-{gen_id(8)}",
            )
            batch = SampleBatch(
                sample_batch_id=gen_id(),
                dataset_id=dataset.dataset_id,
                sample_batch_name=f"Batch {gen_id(6)}",
                sample_batch_type="ACQUISITION" if pipeline else "ANALYSIS",
            )
            async with async_session_factory() as session:
                session.add_all([workspace, dataset, batch])
                await session.commit()
            made["workspaces"].append(workspace.workspace_id)
            batches[kind] = batch
        return batches[kind]

    async def add(instrument_type: str = "orbi", samples: tuple[str, ...] = ()):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sample_file = SampleFile(
            sample_file_id=gen_id(),
            filename=f"{gen_id(10)}.raw",
            instrument=f"instrument-{gen_id(8)}",
            instrument_type=instrument_type,
            datetime=when.replace(tzinfo=None),
            datetime_utc=when,
            length=60.0,
            range=[40.0, 600.0],
            polarity="-",
        )
        async with async_session_factory() as session:
            session.add(sample_file)
            await session.commit()
        made["files"].append(sample_file.sample_file_id)

        for kind in samples:
            batch = await batch_of(kind)
            item = SampleItem(
                sample_item_id=gen_id(),
                sample_batch_id=batch.sample_batch_id,
                sample_file_id=sample_file.sample_file_id,
                sample_item_name=f"Item {gen_id(6)}",
                sample_item_type="ACQUISITION" if kind == "pipeline" else "ANALYSIS",
            )
            async with async_session_factory() as session:
                session.add(item)
                await session.commit()
            made["items"].append(item.sample_item_id)

        return sample_file

    yield add

    async with async_session_factory() as session:
        if made["items"]:
            await session.execute(
                delete(SampleItem).where(SampleItem.sample_item_id.in_(made["items"]))
            )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(made["files"]))
        )
        if made["workspaces"]:
            await session.execute(
                delete(Workspace).where(Workspace.workspace_id.in_(made["workspaces"]))
            )
        await session.commit()


@pytest.mark.asyncio
async def test_only_orbitrap_files_are_offered(files):
    """A TOF file's scans are not selected by the reader whose rule changed."""
    orbitrap = await files("orbi")
    tof = await files("tof")

    offered = {row["sample_file_id"]: row for row in await script._candidates()}

    assert offered[orbitrap.sample_file_id] == {
        "sample_file_id": orbitrap.sample_file_id,
        "filename": orbitrap.filename,
        "instrument": orbitrap.instrument,
    }
    assert tof.sample_file_id not in offered


@pytest.mark.asyncio
async def test_every_sample_of_a_file_is_found_in_every_batch(
    files, async_session_factory
):
    """The pipeline's sample and a person's copy of it, in a workspace of
    their own: both had their matches made on the store being replaced."""
    copied = await files(samples=("pipeline", "person"))
    other = await files(samples=("pipeline",))

    samples, batches = await script._samples_of(copied.sample_file_id)

    async with async_session_factory() as session:
        items = (
            await session.execute(
                select(SampleItem.sample_item_id, SampleItem.sample_batch_id).where(
                    SampleItem.sample_file_id == copied.sample_file_id
                )
            )
        ).all()
    assert sorted(samples) == sorted(item for item, _ in items)
    assert sorted(batches) == sorted({batch for _, batch in items})
    assert len(batches) == 2

    others, _ = await script._samples_of(other.sample_file_id)
    assert not set(others) & set(samples)


@pytest.mark.asyncio
async def test_a_file_with_no_samples_has_none_to_rematch(files):
    alone = await files()

    assert await script._samples_of(alone.sample_file_id) == ([], [])


# --- the run's own steps --------------------------------------------------------

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: The TIC the stand-in reader gives each polarity of the file.
_READ_TIC = {"+": 111.0, "-": 222.0}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def sample_view(async_engine):
    """A match removal reads its sample through ``sample_view``, which
    ``create_all`` does not make (``tests/integration/api/match/conftest.py``
    says why), so it is made here as there."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def matched(async_session_factory):
    """One raw Orbitrap file with matched samples in two batches of one
    workspace: one batch ``ready``, the other waiting on a recalibration.

    The file's samples are a positive and a negative one in the ready batch,
    a positive copy in the other, and a positive one that stores no TIC.
    Each has a stored match on a target isotope of the collection both
    batches hold, so a removal that only takes orphaned matches leaves it.
    """
    ids = SimpleNamespace(
        workspace=gen_id(),
        dataset=gen_id(),
        ready=gen_id(),
        recalibrate=gen_id(),
        file=gen_id(),
        mechanism=gen_id(),
        mode=gen_id(),
        collection=gen_id(),
        compound=gen_id(),
        ion=gen_id(),
        isotope=gen_id(),
    )
    ids.filename = f"test-orbion_{ids.file}.raw"
    # (item, batch, polarity, stored TIC)
    ids.items = [
        (gen_id(), ids.ready, "+", 1000.0),
        (gen_id(), ids.ready, "-", 2000.0),
        (gen_id(), ids.recalibrate, "+", 1000.0),
        (gen_id(), ids.ready, "+", None),
    ]
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids.workspace,
                workspace_name=f"Stale store rebuild WS {ids.workspace}",
                workspace_description="Stale store rebuild test workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids.dataset,
                workspace_id=ids.workspace,
                dataset_name=f"Stale store rebuild DS {ids.dataset}",
                dataset_description="Stale store rebuild test dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        for batch_id, status in (
            (ids.ready, "ready"),
            (ids.recalibrate, "recalibrate"),
        ):
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=ids.dataset,
                    sample_batch_name=f"Batch {status} {batch_id}",
                    status=status,
                    sample_batch_utc_created=_NOW,
                )
            )
        session.add(
            SampleFile(
                sample_file_id=ids.file,
                filename=ids.filename,
                instrument="test-orbion",
                instrument_type="orbi",
                datetime=_NOW.replace(tzinfo=None),
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+-",
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {ids.mechanism}",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids.mode,
                ionization_mode_name="Stale store rebuild mode",
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[ids.mechanism],
            )
        )
        await session.flush()
        for item_id, batch_id, polarity, tic in ids.items:
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=ids.file,
                    ionization_mode_id=ids.mode,
                    sample_item_name=f"Item {item_id}",
                    sample_item_type="ANALYSIS",
                    sample_item_attributes={},
                    polarity=polarity,
                    tic=tic,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        session.add(
            TargetCollection(
                target_collection_id=ids.collection,
                target_collection_name=f"Stale store rebuild {ids.collection}",
                workspace_id=ids.workspace,
            )
        )
        for batch_id in (ids.ready, ids.recalibrate):
            session.add(
                TargetCollectionInSampleBatch(
                    target_collection_id=ids.collection, sample_batch_id=batch_id
                )
            )
        session.add(
            TargetCompound(
                target_compound_id=ids.compound,
                target_compound_name=f"Stale store rebuild {ids.compound}",
                target_compound_formula="C6H12O6",
            )
        )
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=ids.compound,
                target_collection_id=ids.collection,
            )
        )
        session.add(
            TargetIon(
                target_ion_id=ids.ion,
                target_compound_id=ids.compound,
                ionization_mechanism_id=ids.mechanism,
                target_ion_formula="C6H13O6+",
            )
        )
        session.add(
            TargetIsotope(
                target_isotope_id=ids.isotope,
                target_ion_id=ids.ion,
                target_isotope_formula="C6H13O6+",
                mz=181.07,
                relative_abundance=1.0,
                resolution="HIGH",
            )
        )
        await session.flush()
        for item_id, *_ in ids.items:
            session.add(
                MatchIsotope(
                    match_isotope_id=gen_id(32),
                    target_isotope_id=ids.isotope,
                    sample_item_id=item_id,
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

    yield ids

    item_ids = [item_id for item_id, *_ in ids.items]
    async with async_session_factory() as session:
        for statement in (
            delete(MatchIsotope).where(MatchIsotope.sample_item_id.in_(item_ids)),
            delete(SampleItem).where(SampleItem.sample_item_id.in_(item_ids)),
            delete(SampleFile).where(SampleFile.sample_file_id == ids.file),
            delete(TargetCollectionInSampleBatch).where(
                TargetCollectionInSampleBatch.target_collection_id == ids.collection
            ),
            delete(TargetCompoundInTargetCollection).where(
                TargetCompoundInTargetCollection.target_collection_id == ids.collection
            ),
            delete(TargetIsotope).where(TargetIsotope.target_isotope_id == ids.isotope),
            delete(TargetIon).where(TargetIon.target_ion_id == ids.ion),
            delete(TargetCompound).where(
                TargetCompound.target_compound_id == ids.compound
            ),
            delete(TargetCollection).where(
                TargetCollection.target_collection_id == ids.collection
            ),
            delete(SampleBatch).where(
                SampleBatch.sample_batch_id.in_([ids.ready, ids.recalibrate])
            ),
            delete(Dataset).where(Dataset.dataset_id == ids.dataset),
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == ids.mode),
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == ids.mechanism
            ),
            delete(Workspace).where(Workspace.workspace_id == ids.workspace),
        ):
            await session.execute(statement)
        await session.commit()


@pytest.fixture
def filestore(monkeypatch):
    """The run's reads and writes of the filestore, as stand-ins that record
    them: the file's TIC per polarity, its props, its MS2 store and the
    batches' aligned peaks."""
    seen = SimpleNamespace(caches=[], ms2=[], inventory=[], props={})

    async def sample_item_tic(filename, polarity):
        return _READ_TIC[polarity]

    async def write_inventory(sample_file_id, filename):
        seen.inventory.append(sample_file_id)
        return 0

    monkeypatch.setattr(sample_items_controller, "_sample_item_tic", sample_item_tic)
    monkeypatch.setattr(script.m_io, "delete_batch_cache", seen.caches.append)
    monkeypatch.setattr(script.m_io, "read_props", lambda filename: seen.props)
    monkeypatch.setattr(m_ms2_store, "refresh_ms2_store", seen.ms2.append)
    monkeypatch.setattr(inventory, "write_ms2_inventory", write_inventory)
    return seen


def _real_steps(rebuilt):
    """The run's steps against the database, around a stand-in detection
    and a check that finds the rebuilt store current."""

    async def rebuild(filename):
        rebuilt.append(filename)

    return script.Steps(
        samples_of=script._samples_of,
        queue_rematch=script._queue_rematch,
        remove_matches=script._remove_matches,
        refresh_tic=script._refresh_tic,
        refresh_ms2=script._refresh_ms2,
        rebuild=rebuild,
        check_one=lambda filename: script.Checked(script.CURRENT),
    )


@pytest.mark.asyncio
async def test_a_rebuild_queues_rematch_removes_matches_and_reads_the_tic_again(
    matched, filestore, async_session_factory
):
    rebuilt = []
    stale = [
        {
            "sample_file_id": matched.file,
            "filename": matched.filename,
            "instrument": "test-orbion",
            "stored": 8,
            "reads": 7,
        }
    ]

    outcome = await script.rebuild_all(stale, _real_steps(rebuilt))

    assert rebuilt == [matched.filename]
    assert outcome.counts["rebuilt"] == 1
    item_ids = [item_id for item_id, *_ in matched.items]
    async with async_session_factory() as session:
        statuses = dict(
            (
                await session.execute(
                    select(SampleBatch.sample_batch_id, SampleBatch.status).where(
                        SampleBatch.sample_batch_id.in_(
                            [matched.ready, matched.recalibrate]
                        )
                    )
                )
            ).all()
        )
        matches = (
            await session.execute(
                select(MatchIsotope.match_isotope_id).where(
                    MatchIsotope.sample_item_id.in_(item_ids)
                )
            )
        ).all()
        tics = dict(
            (
                await session.execute(
                    select(SampleItem.sample_item_id, SampleItem.tic).where(
                        SampleItem.sample_item_id.in_(item_ids)
                    )
                )
            ).all()
        )

    # A batch waiting on a recalibration keeps it: its recalibration
    # rematches it too
    assert statuses == {matched.ready: "rematch", matched.recalibrate: "recalibrate"}
    # Every match of every sample, those the batches' collections still
    # hold included
    assert matches == []
    # Read again per polarity, and only where a TIC was stored
    assert tics == {
        item_id: (None if tic is None else _READ_TIC[polarity])
        for item_id, _batch, polarity, tic in matched.items
    }
    assert outcome.counts["tic_items"] == 3
    # Each batch's aligned peaks name the peak ids being replaced
    assert sorted(filestore.caches) == sorted([matched.ready, matched.recalibrate])
    # A file with no MS2 store is left without one
    assert filestore.ms2 == []
    assert filestore.inventory == []
    assert outcome.counts["ms2_stores"] == 0


@pytest.mark.asyncio
async def test_a_polarity_whose_tic_reads_as_none_keeps_the_stored_one(
    matched, filestore, monkeypatch, async_session_factory
):
    """A read with no finite TIC writes nothing over the stored value: the
    file's other polarity is still read again."""

    async def sample_item_tic(filename, polarity):
        return None if polarity == "-" else _READ_TIC[polarity]

    monkeypatch.setattr(sample_items_controller, "_sample_item_tic", sample_item_tic)

    updated = await script._refresh_tic(matched.file, matched.filename)

    item_ids = [item_id for item_id, *_ in matched.items]
    async with async_session_factory() as session:
        tics = dict(
            (
                await session.execute(
                    select(SampleItem.sample_item_id, SampleItem.tic).where(
                        SampleItem.sample_item_id.in_(item_ids)
                    )
                )
            ).all()
        )
    expected = {
        item_id: (_READ_TIC["+"] if tic is not None and polarity == "+" else tic)
        for item_id, _batch, polarity, tic in matched.items
    }
    assert tics == expected
    assert updated == sum(
        1
        for _, _, polarity, tic in matched.items
        if tic is not None and polarity == "+"
    )
    # The fixture stores a TIC on an item of each polarity, or this pins nothing
    assert {polarity for _, _, polarity, tic in matched.items if tic is not None} == {
        "+",
        "-",
    }


@pytest.mark.asyncio
async def test_a_file_with_an_ms2_store_has_it_written_again(matched, filestore):
    filestore.props["ms2_store"] = {"layout": "mascope.ms2_store", "version": 3}

    assert await script._refresh_ms2(matched.file, matched.filename) is True

    assert filestore.ms2 == [matched.filename]
    assert filestore.inventory == [matched.file]
