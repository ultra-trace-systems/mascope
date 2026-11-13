"""Integration tests for seeding a batch ledger's anchors from its target ions.

A batch whose ``seed_batch_peaks`` is on gets an anchor at each target ion's
monoisotopic m/z before the first sample of a partition folds, marked
``seeded``, and the sample's peaks snap to it; a batch with the option off, or
with no collection, folds as it always has. A rebuild seeds a ledger that
already exists only when asked, and then without moving an anchor. See
``batch_peaks.seed_anchors`` and ``batch_peaks_controller.target_ion_seed_mzs``.
"""

from collections import defaultdict
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.api.new.peak_assignments import (
    batch_peaks_controller,
    service,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    backfill_sample_batch_peaks,
    fold_sample_into_batch_peaks,
    rebuild_batch_ledger,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    get_batch_peak_ledger,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SEARCH,
    complete_run,
    start_run,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    Dataset,
    IonizationMechanism,
    IonizationMode,
    PeakAssignment,
    PeakAssignmentRun,
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


pytestmark = pytest.mark.asyncio

GLUCOSE_MZ = 181.0707
NAPHTHOL_MZ = 145.0648

#: The mechanisms the setup names - one of the other polarity, and a multimer
#: no run searches - and one it does not.
_MECHANISMS = {
    "mh": ("[M+H]+", "+"),
    "mna": ("[M+Na]+", "+"),
    "mmh": ("[M-H]-", "-"),
    "m2h": ("[2M+H]+", "+"),
}
_SETUP = ("mh", "mmh", "m2h")

#: (compound, ion formula, mechanism key, [(isotope formula, mz, abundance,
#: resolution)]). Only the [M+H]+ ions' HIGH monoisotopic lines are seeds on
#: an Orbitrap: [M+Na]+ is not the setup's, [M-H]- is the other polarity,
#: [2M+H]+ is a channel no run reads, a LOW line is a TOF's, and a 13C line is
#: no ion's m/z.
_IONS = [
    (
        "C6H12O6",
        "C6H13O6+",
        "mh",
        [
            ("C6H13O6+", GLUCOSE_MZ, 1.0, "HIGH"),
            ("[13C]C5H13O6+", 182.0741, 0.065, "HIGH"),
            ("C6H13O6+/[13C]C5H13O6+", 181.0711, 1.0, "LOW"),
        ],
    ),
    ("C6H12O6", "C6H12NaO6+", "mna", [("C6H12NaO6+", 203.0526, 1.0, "HIGH")]),
    ("C6H12O6", "C6H11O6-", "mmh", [("C6H11O6-", 179.0561, 1.0, "HIGH")]),
    ("C6H12O6", "C12H25O12+", "m2h", [("C12H25O12+", 361.1341, 1.0, "HIGH")]),
    ("C10H8O", "C10H9O+", "mh", [("C10H9O+", NAPHTHOL_MZ, 1.0, "HIGH")]),
]

#: A compound of a collection the batch does not have.
_OTHER_ION = ("C7H6O2", "C7H7O2+", "mh", [("C7H7O2+", 123.0441, 1.0, "HIGH")])


def _ppm(mz: float, ppm: float) -> float:
    return mz * (1.0 + ppm / 1e6)


# (peak_id, mz, neutral, ion, tier, fit, intensity, mz_err_ppm). Sample A's
# two formula rows read 1 ppm high, so its offset is 1 ppm; its glucose peak
# sits 2.5 ppm high, 1.5 ppm off the ion once corrected - inside the 2 ppm
# window an anchor carries without a resolution function.
_SPECS = {
    "A": [
        (
            "A1",
            _ppm(GLUCOSE_MZ, 2.5),
            "C6H12O6",
            "C6H13O6+",
            "assigned",
            0.95,
            5e3,
            1.0,
        ),
        ("A2", 200.0500, "C10H8O2", "C10H9O2+", "candidate", 0.6, 800.0, 1.0),
        ("A3", 250.1000, None, None, "unassigned", None, 300.0, None),
    ],
    "B": [
        ("B1", _ppm(GLUCOSE_MZ, 1.0), "C6H12O6", "C6H13O6+", "assigned", 0.9, 4e3, 1.0),
        ("B2", 300.2000, "C12H10", "C12H11+", "assigned", 0.85, 2e3, 1.0),
    ],
}


async def _mechanisms(session, made: dict) -> dict:
    found = {}
    for key, (notation, polarity) in _MECHANISMS.items():
        mechanism_id = (
            await session.execute(
                select(IonizationMechanism.ionization_mechanism_id).where(
                    IonizationMechanism.ionization_mechanism == notation
                )
            )
        ).scalar()
        if mechanism_id is None:
            mechanism_id = gen_id()
            made[IonizationMechanism].append(mechanism_id)
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=mechanism_id,
                    ionization_mechanism_polarity=polarity,
                    ionization_mechanism=notation,
                )
            )
        found[key] = mechanism_id
    await session.flush()
    return found


async def _collection(session, workspace_id, ions, mechanisms, made) -> str:
    collection_id = gen_id()
    made[TargetCollection].append(collection_id)
    session.add(
        TargetCollection(
            target_collection_id=collection_id,
            target_collection_name=f"Seeds {collection_id}",
            workspace_id=workspace_id,
        )
    )
    compounds = {}
    for formula, ion_formula, mechanism, isotopes in ions:
        if formula not in compounds:
            compounds[formula] = gen_id()
            made[TargetCompound].append(compounds[formula])
            session.add(
                TargetCompound(
                    target_compound_id=compounds[formula],
                    target_compound_formula=formula,
                )
            )
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=compounds[formula],
                    target_collection_id=collection_id,
                )
            )
        ion_id = gen_id()
        session.add(
            TargetIon(
                target_ion_id=ion_id,
                target_compound_id=compounds[formula],
                ionization_mechanism_id=mechanisms[mechanism],
                target_ion_formula=ion_formula,
            )
        )
        for isotope_formula, mz, abundance, resolution in isotopes:
            session.add(
                TargetIsotope(
                    target_isotope_id=gen_id(),
                    target_ion_id=ion_id,
                    target_isotope_formula=isotope_formula,
                    mz=mz,
                    relative_abundance=abundance,
                    resolution=resolution,
                )
            )
    return collection_id


async def _make_batch(session, made, *, seed: bool, collection: bool, specs=_SPECS):
    now = datetime.now(timezone.utc)
    workspace_id, dataset_id, batch_id, mode_id = gen_id(), gen_id(), gen_id(), gen_id()
    made[Workspace].append(workspace_id)
    made[IonizationMode].append(mode_id)
    mechanisms = await _mechanisms(session, made)
    session.add(
        Workspace(
            workspace_id=workspace_id,
            workspace_name=f"Seeding WS {workspace_id}",
            workspace_status="active",
            workspace_utc_created=now,
            workspace_utc_modified=now,
        )
    )
    session.add(
        Dataset(
            dataset_id=dataset_id,
            workspace_id=workspace_id,
            dataset_name="Seeding DS",
            dataset_utc_created=now,
        )
    )
    session.add(
        SampleBatch(
            sample_batch_id=batch_id,
            dataset_id=dataset_id,
            sample_batch_name="Seeding Batch",
            sample_batch_utc_created=now,
            seed_batch_peaks=seed,
        )
    )
    session.add(
        IonizationMode(
            ionization_mode_id=mode_id,
            ionization_mode_name=f"Seeding setup {mode_id}",
            ionization_mode_polarity="+",
            ionization_mechanism_ids=[mechanisms[key] for key in _SETUP],
        )
    )
    await session.flush()
    if collection:
        attached = await _collection(session, workspace_id, _IONS, mechanisms, made)
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=attached, sample_batch_id=batch_id
            )
        )
        await _collection(session, workspace_id, [_OTHER_ION], mechanisms, made)
    samples = {}
    for hour, (name, rows) in enumerate(specs.items()):
        file_id, item_id, run_id = gen_id(), gen_id(), gen_id()
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=f"orbi-seeding-test-{name}-{file_id}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 7, 4, 12 + hour, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=item_id,
                sample_batch_id=batch_id,
                sample_file_id=file_id,
                sample_item_name=f"Seeding Sample {name}",
                sample_item_type="sample",
                polarity="+",
                ionization_mode_id=mode_id,
                sample_item_utc_created=now,
            )
        )
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=item_id,
                engine_version="0.1.0-test",
                status="completed",
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        for pid, mz, neutral, ion, tier, fit, intensity, err in rows:
            session.add(
                PeakAssignment(
                    peak_assignment_id=gen_id(32),
                    peak_assignment_run_id=run_id,
                    sample_item_id=item_id,
                    sample_peak_id=pid,
                    sample_peak_mz=mz,
                    sample_peak_intensity=intensity,
                    role="M0" if neutral else "unassigned",
                    assigned_formula=neutral,
                    ion_formula=ion,
                    source="database" if neutral else None,
                    fit_score=fit,
                    mz_error_ppm=err,
                    tier=tier,
                )
            )
        samples[name] = item_id
    await session.commit()
    return batch_id, samples


#: What the fixture deletes after a test, in this order: the workspaces take
#: their datasets, batches and samples with them, and the samples are what
#: hold the setups; the mechanisms take the target ions, which the suite's
#: other tests would otherwise meet as a notation already taken.
_CLEANUP = (
    (Workspace, Workspace.workspace_id),
    (TargetCollection, TargetCollection.target_collection_id),
    (TargetCompound, TargetCompound.target_compound_id),
    (IonizationMode, IonizationMode.ionization_mode_id),
    (IonizationMechanism, IonizationMechanism.ionization_mechanism_id),
)


@pytest_asyncio.fixture
async def make_batch(async_session_factory, patch_db, pa_sample_view):
    made = defaultdict(list)

    async def make(**kwargs):
        async with async_session_factory() as session:
            return await _make_batch(session, made, **kwargs)

    yield make
    async with async_session_factory() as session:
        for model, key in _CLEANUP:
            if made[model]:
                await session.execute(delete(model).where(key.in_(made[model])))
        await session.commit()


async def _anchors(session_factory, batch_id) -> list:
    async with session_factory() as session:
        return (
            (
                await session.execute(
                    select(BatchPeak)
                    .where(BatchPeak.sample_batch_id == batch_id)
                    .order_by(BatchPeak.mz)
                )
            )
            .scalars()
            .all()
        )


async def _members(session_factory, batch_peak_id) -> set:
    async with session_factory() as session:
        return set(
            (
                await session.execute(
                    select(BatchPeakOccurrence.sample_peak_id).where(
                        BatchPeakOccurrence.batch_peak_id == batch_peak_id
                    )
                )
            ).scalars()
        )


async def test_a_batch_with_a_collection_seeds_exactly_its_ions(
    async_session_factory, make_batch
):
    """The first fold mints an anchor at each [M+H]+ ion's monoisotopic m/z,
    marked seeded, and nothing for another mechanism, the other polarity, a
    TOF's line, an isotope line or a collection the batch lacks. The first
    sample's glucose peak joins the seed, which keeps the ion's m/z rather
    than the sample's; the naphthol seed stands with no member."""
    batch_id, samples = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])

    anchors = await _anchors(async_session_factory, batch_id)
    assert sorted(a.mz for a in anchors if a.seeded) == [NAPHTHOL_MZ, GLUCOSE_MZ]
    assert sorted(round(a.mz, 3) for a in anchors if not a.seeded) == [
        200.05,
        250.1,
    ]

    glucose = next(a for a in anchors if a.mz == GLUCOSE_MZ)
    assert await _members(async_session_factory, glucose.batch_peak_id) == {"A1"}
    assert glucose.consensus_formula == "C6H12O6"
    assert glucose.n_present == 1
    assert glucose.mz_tol_ppm == pytest.approx(2.0)

    naphthol = next(a for a in anchors if a.mz == NAPHTHOL_MZ)
    assert await _members(async_session_factory, naphthol.batch_peak_id) == set()
    assert naphthol.n_present == 0
    assert naphthol.consensus_tier == "unassigned"

    # The ledger names the seeded rows.
    ledger = await get_batch_peak_ledger(sample_batch_id=batch_id, min_n_present=1)
    assert sorted((round(row["mz"], 3), row["seeded"]) for row in ledger["data"]) == [
        (145.065, True),
        (181.071, True),
        (200.05, False),
        (250.1, False),
    ]


async def test_the_next_fold_snaps_to_the_seed_and_seeds_nothing_more(
    async_session_factory, make_batch
):
    batch_id, samples = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    before = {
        a.batch_peak_id: a.mz for a in await _anchors(async_session_factory, batch_id)
    }
    await fold_sample_into_batch_peaks(samples["B"])

    anchors = await _anchors(async_session_factory, batch_id)
    after = {a.batch_peak_id: a.mz for a in anchors}
    assert {k: after[k] for k in before} == before
    # B's glucose joins the seed; B2 is the only anchor B mints.
    assert set(after) - set(before) == {
        next(a.batch_peak_id for a in anchors if round(a.mz, 3) == 300.2)
    }
    assert sum(a.seeded for a in anchors) == 2
    glucose = next(a for a in anchors if a.mz == GLUCOSE_MZ)
    assert await _members(async_session_factory, glucose.batch_peak_id) == {
        "A1",
        "B1",
    }


#: The ledger fields that name the batch's own rows: ids every batch mints.
_BATCH_OWN = ("batch_peak_id", "sample_batch_id", "ionization_mode_id")


def _ledger_shape(ledger: dict) -> list:
    """A ledger's rows without the ids that differ between two batches."""
    return [
        {k: v for k, v in row.items() if k not in _BATCH_OWN} for row in ledger["data"]
    ]


async def test_option_off_or_no_collection_folds_as_before(make_batch):
    """With the option off, and with it on but no collection, the ledger is
    the one a batch with neither folds: no seed, the glucose anchor where the
    first sample put it."""
    ledgers = []
    for seed, collection in ((False, False), (False, True), (True, False)):
        batch_id, samples = await make_batch(seed=seed, collection=collection)
        await fold_sample_into_batch_peaks(samples["A"])
        await fold_sample_into_batch_peaks(samples["B"])
        ledgers.append(
            _ledger_shape(
                await get_batch_peak_ledger(sample_batch_id=batch_id, min_n_present=1)
            )
        )
    assert ledgers[1] == ledgers[0]
    assert ledgers[2] == ledgers[0]
    assert not any(row["seeded"] for row in ledgers[0])
    glucose = next(row for row in ledgers[0] if row["consensus_formula"] == "C6H12O6")
    assert glucose["mz"] == pytest.approx(_ppm(GLUCOSE_MZ, 1.5), abs=1e-9)


async def test_a_ledger_that_exists_is_seeded_only_when_a_rebuild_asks(
    async_session_factory, make_batch
):
    """Turning the option on for a folded batch seeds nothing, nor does a
    plain rebuild; a rebuild that asks to re-seed seeds the ion no anchor
    holds and keeps every anchor where it was, the glucose one included,
    which already holds that ion's m/z."""
    batch_id, samples = await make_batch(seed=False, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    async with async_session_factory() as session:
        await session.execute(
            update(SampleBatch)
            .where(SampleBatch.sample_batch_id == batch_id)
            .values(seed_batch_peaks=True)
        )
        await session.commit()
    await fold_sample_into_batch_peaks(samples["B"])
    await backfill_sample_batch_peaks(batch_id)
    before = {
        a.batch_peak_id: a.mz for a in await _anchors(async_session_factory, batch_id)
    }
    assert not any(a.seeded for a in await _anchors(async_session_factory, batch_id))

    assert await backfill_sample_batch_peaks(batch_id, reseed=True) == (2, 0)

    anchors = await _anchors(async_session_factory, batch_id)
    after = {a.batch_peak_id: a.mz for a in anchors}
    assert {k: after[k] for k in before} == before
    assert [a.mz for a in anchors if a.seeded] == [NAPHTHOL_MZ]
    assert set(after) - set(before) == {
        next(a.batch_peak_id for a in anchors if a.seeded)
    }

    # Asking again changes nothing: every seed is held now.
    await backfill_sample_batch_peaks(batch_id, reseed=True)
    assert {
        a.batch_peak_id: a.mz for a in await _anchors(async_session_factory, batch_id)
    } == after


async def test_a_rebuild_seeds_a_partition_it_folds_first(
    async_session_factory, make_batch
):
    """A batch with the option on and no ledger yet is seeded by the rebuild
    that first folds it, without asking."""
    batch_id, _ = await make_batch(seed=True, collection=True)
    assert await backfill_sample_batch_peaks(batch_id) == (2, 0)
    anchors = await _anchors(async_session_factory, batch_id)
    assert sorted(a.mz for a in anchors if a.seeded) == [NAPHTHOL_MZ, GLUCOSE_MZ]


async def test_a_run_snapshot_keeps_the_mark(async_session_factory, make_batch):
    """A batch run snapshots the ledger it replaces, and a read of that run
    names the seeded rows as the live ledger did."""
    batch_id, samples = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    run_id = await start_run(batch_id, ACTION_SEARCH)
    await complete_run(batch_id, run_id)
    async with async_session_factory() as session:
        snapshot_run = (
            await session.execute(
                select(BatchPeakRun.batch_peak_run_id).where(
                    BatchPeakRun.sample_batch_id == batch_id,
                    BatchPeakRun.is_current == 0,
                )
            )
        ).scalar_one()
    ledger = await get_batch_peak_ledger(
        sample_batch_id=batch_id, min_n_present=1, batch_peak_run_id=snapshot_run
    )
    assert sorted((round(row["mz"], 3), row["seeded"]) for row in ledger["data"]) == [
        (145.065, True),
        (181.071, True),
        (200.05, False),
        (250.1, False),
    ]


async def test_a_tof_partition_is_seeded_at_the_lines_a_tof_run_matches(
    async_session_factory, make_batch, monkeypatch
):
    """A TOF run matches the merged low-resolution lines, so a TOF partition
    is seeded at those: the glucose ion's, the naphthol ion having none."""
    monkeypatch.setattr(
        batch_peaks_controller, "get_instrument_type", lambda filename: "tof"
    )
    batch_id, samples = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    anchors = await _anchors(async_session_factory, batch_id)
    seeds = [a for a in anchors if a.seeded]
    assert [(a.mz, a.intensity_variable) for a in seeds] == [
        (181.0711, "sum_peak_areas")
    ]
    assert await _members(async_session_factory, seeds[0].batch_peak_id) == {"A1"}


async def test_a_rebuild_asks_a_sample_without_a_run_to_reseed(
    async_session_factory, make_batch, monkeypatch
):
    """A sample with no completed run is folded through Stage A, and a
    rebuild's request to re-seed reaches that fold too."""
    batch_id, samples = await make_batch(seed=False, collection=True)
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignmentRun)
            .where(PeakAssignmentRun.sample_item_id == samples["B"])
            .values(status="failed")
        )
        await session.commit()
    calls = []

    async def fold_without_run(sample_item_id, **kwargs):
        calls.append((sample_item_id, kwargs["reseed"]))
        return None

    monkeypatch.setattr(service, "fold_sample_peaks_without_run", fold_without_run)
    await backfill_sample_batch_peaks(batch_id, reseed=True)
    await backfill_sample_batch_peaks(batch_id)
    assert calls == [(samples["B"], True), (samples["B"], False)]


async def test_the_rebuild_run_seeds_when_asked(async_session_factory, make_batch):
    """The rebuild as a batch run hands a request to re-seed to its folds."""
    batch_id, samples = await make_batch(seed=False, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    await rebuild_batch_ledger(batch_id)
    assert not any(a.seeded for a in await _anchors(async_session_factory, batch_id))
    await rebuild_batch_ledger(batch_id, reseed=True)
    anchors = await _anchors(async_session_factory, batch_id)
    assert [a.mz for a in anchors if a.seeded] == [NAPHTHOL_MZ]


@pytest.mark.parametrize("reseed", [False, True])
async def test_the_rebuild_task_hands_on_the_request(make_batch, monkeypatch, reseed):
    """The background task the route starts hands ``reseed`` to the rebuild."""
    batch_id, _ = await make_batch(seed=False, collection=True)
    calls = []

    async def rebuild(sample_batch_id, **kwargs):
        calls.append(kwargs["reseed"])
        return {"status": "success", "message": "ok", "data": {}}

    monkeypatch.setattr(batch_peaks_controller, "rebuild_batch_ledger", rebuild)
    await batch_peaks_controller.compute_batch_peaks(
        sample_batch_id=batch_id, independent_transaction=True, reseed=reseed
    )
    assert calls == [reseed]
