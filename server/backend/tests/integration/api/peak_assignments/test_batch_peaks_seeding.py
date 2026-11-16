"""Integration tests for seeding a batch ledger's anchors at target ions' m/z.

A batch whose ``seed_batch_peaks`` is on mints a new anchor at a target ion's
monoisotopic m/z, marked ``seeded``, where the peak that mints it is the line
the sample's run assigned to that ion, and the peak lies inside the window an
anchor there carries. A target no sample's peak was assigned to mints nothing.
A batch with the option off, or with no collection, folds as it always has.
See ``batch_peaks.fold_in_sample`` and
``batch_peaks_controller.target_ion_seed_mzs``.
"""

from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.api.new.peak_assignments import batch_peaks_controller
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    backfill_sample_batch_peaks,
    fold_sample_into_batch_peaks,
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
BENZOIC_MZ = 123.0441

#: The mechanism the ions name.
_MECHANISMS = {"mh": ("[M+H]+", "+")}

#: (key, compound, ion formula, mechanism key, [(isotope formula, mz,
#: abundance, resolution)]) of the batch's collection. An ion seeds at its
#: monoisotopic line at the resolution the run reads: HIGH on an Orbitrap, the
#: merged LOW line on a TOF, which the naphthol ion has none of.
_IONS = [
    (
        "glucose",
        "C6H12O6",
        "C6H13O6+",
        "mh",
        [
            ("C6H13O6+", GLUCOSE_MZ, 1.0, "HIGH"),
            ("[13C]C5H13O6+", 182.0741, 0.065, "HIGH"),
            ("C6H13O6+/[13C]C5H13O6+", 181.0711, 1.0, "LOW"),
        ],
    ),
    ("naphthol", "C10H8O", "C10H9O+", "mh", [("C10H9O+", NAPHTHOL_MZ, 1.0, "HIGH")]),
]

#: An ion of a collection the batch does not have.
_OTHER_IONS = [
    ("benzoic", "C7H6O2", "C7H7O2+", "mh", [("C7H7O2+", BENZOIC_MZ, 1.0, "HIGH")])
]


def _ppm(mz: float, ppm: float) -> float:
    return mz * (1.0 + ppm / 1e6)


# (peak_id, mz, neutral, ion, role, target ion key, tier, fit, intensity,
# mz_err_ppm). Every formula row reads 1 ppm high, so each sample's offset is
# 1 ppm. A1, the glucose ion's line, sits 2.5 ppm high, 1.5 ppm off the ion
# once corrected - inside the 2 ppm window an anchor carries without a
# resolution function. A4 is the ion's 13C line, A5 the line of an ion of a
# collection the batch lacks, A2 an untargeted reading. Nothing in A is the
# naphthol ion, which B's B3 first is. B1 sits 1 ppm off the glucose ion once
# corrected, inside the window of both its HIGH and its merged LOW line.
_SPECS = {
    "A": [
        (
            "A1",
            _ppm(GLUCOSE_MZ, 2.5),
            "C6H12O6",
            "C6H13O6+",
            "M0",
            "glucose",
            "assigned",
            0.95,
            5e3,
            1.0,
        ),
        (
            "A2",
            200.0500,
            "C10H8O2",
            "C10H9O2+",
            "M0",
            None,
            "candidate",
            0.6,
            800.0,
            1.0,
        ),
        (
            "A3",
            250.1000,
            None,
            None,
            "unassigned",
            None,
            "unassigned",
            None,
            300.0,
            None,
        ),
        (
            "A4",
            _ppm(182.0741, 1.5),
            "C6H12O6",
            "C6H13O6+",
            "iso_child",
            "glucose",
            "assigned",
            0.9,
            300.0,
            1.0,
        ),
        (
            "A5",
            _ppm(BENZOIC_MZ, 1.5),
            "C7H6O2",
            "C7H7O2+",
            "M0",
            "benzoic",
            "assigned",
            0.9,
            1e3,
            1.0,
        ),
    ],
    "B": [
        (
            "B1",
            _ppm(GLUCOSE_MZ, 2.0),
            "C6H12O6",
            "C6H13O6+",
            "M0",
            "glucose",
            "assigned",
            0.9,
            4e3,
            1.0,
        ),
        (
            "B2",
            300.2000,
            "C12H10",
            "C12H11+",
            "M0",
            None,
            "assigned",
            0.85,
            2e3,
            1.0,
        ),
        (
            "B3",
            _ppm(NAPHTHOL_MZ, 2.0),
            "C10H8O",
            "C10H9O+",
            "M0",
            "naphthol",
            "assigned",
            0.9,
            1e3,
            1.0,
        ),
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


async def _collection(session, workspace_id, ions, mechanisms, made, ion_ids):
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
    for key, formula, ion_formula, mechanism, isotopes in ions:
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
        ion_ids[key] = gen_id()
        session.add(
            TargetIon(
                target_ion_id=ion_ids[key],
                target_compound_id=compounds[formula],
                ionization_mechanism_id=mechanisms[mechanism],
                target_ion_formula=ion_formula,
            )
        )
        for isotope_formula, mz, abundance, resolution in isotopes:
            session.add(
                TargetIsotope(
                    target_isotope_id=gen_id(),
                    target_ion_id=ion_ids[key],
                    target_isotope_formula=isotope_formula,
                    mz=mz,
                    relative_abundance=abundance,
                    resolution=resolution,
                )
            )
    await session.flush()
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
            ionization_mechanism_ids=[mechanisms["mh"]],
        )
    )
    await session.flush()
    # The ions exist either way, so the rows can name them; only with
    # ``collection`` is the batch given the one that holds glucose and naphthol.
    ion_ids: dict[str, str] = {}
    attached = await _collection(
        session, workspace_id, _IONS, mechanisms, made, ion_ids
    )
    if collection:
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=attached, sample_batch_id=batch_id
            )
        )
    await _collection(session, workspace_id, _OTHER_IONS, mechanisms, made, ion_ids)
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
        for row in rows:
            session.add(
                PeakAssignment(
                    peak_assignment_id=gen_id(32),
                    peak_assignment_run_id=run_id,
                    sample_item_id=item_id,
                    **_row_fields(row, ion_ids),
                )
            )
        samples[name] = item_id
    await session.commit()
    return batch_id, samples, ion_ids


def _row_fields(spec, ion_ids: dict) -> dict:
    pid, mz, neutral, ion, role, ion_key, tier, fit, intensity, err = spec
    return {
        "sample_peak_id": pid,
        "sample_peak_mz": mz,
        "sample_peak_intensity": intensity,
        "role": role,
        "assigned_formula": neutral,
        "ion_formula": ion,
        "source": "database" if neutral else None,
        "fit_score": fit,
        "mz_error_ppm": err,
        "tier": tier,
        "target_ion_id": ion_ids.get(ion_key),
    }


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


def _at(anchors, mz):
    return next(a for a in anchors if a.mz == pytest.approx(mz, abs=1e-6))


async def test_a_target_ion_line_mints_its_anchor_at_the_ions_mz(
    async_session_factory, make_batch
):
    """The glucose line A's run assigned mints its anchor at the ion's m/z,
    marked seeded, not where A put it. Nothing else of A is seeded: not the
    ion's 13C line, an untargeted reading, an unassigned peak, or the line of
    an ion of a collection the batch lacks. The naphthol ion, which no peak
    of A was assigned to, mints nothing."""
    batch_id, samples, _ = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])

    anchors = await _anchors(async_session_factory, batch_id)
    assert [a.mz for a in anchors if a.seeded] == [GLUCOSE_MZ]
    assert sorted(round(a.mz, 4) for a in anchors if not a.seeded) == [
        round(_ppm(BENZOIC_MZ, 0.5), 4),
        round(_ppm(182.0741, 0.5), 4),
        round(_ppm(200.05, -1.0), 4),
        round(_ppm(250.1, -1.0), 4),
    ]
    assert not any(a.mz == pytest.approx(NAPHTHOL_MZ, abs=1e-3) for a in anchors)

    glucose = _at(anchors, GLUCOSE_MZ)
    assert await _members(async_session_factory, glucose.batch_peak_id) == {"A1"}
    assert glucose.consensus_formula == "C6H12O6"
    assert glucose.mz_tol_ppm == pytest.approx(2.0)

    # The ledger names the seeded row.
    ledger = await get_batch_peak_ledger(sample_batch_id=batch_id, min_n_present=1)
    assert [round(row["mz"], 4) for row in ledger["data"] if row["seeded"]] == [
        round(GLUCOSE_MZ, 4)
    ]
    assert sum(not row["seeded"] for row in ledger["data"]) == 4


async def test_a_later_sample_joins_the_seed_and_seeds_its_own_first_target(
    async_session_factory, make_batch
):
    """B's glucose line joins A's seed, which keeps its m/z. B's naphthol
    line, the first any sample has, mints the naphthol ion's anchor at the
    ion's m/z."""
    batch_id, samples, _ = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    before = {
        a.batch_peak_id: a.mz for a in await _anchors(async_session_factory, batch_id)
    }
    await fold_sample_into_batch_peaks(samples["B"])

    anchors = await _anchors(async_session_factory, batch_id)
    after = {a.batch_peak_id: a.mz for a in anchors}
    assert {k: after[k] for k in before} == before
    assert [a.mz for a in anchors if a.seeded] == [NAPHTHOL_MZ, GLUCOSE_MZ]
    glucose = _at(anchors, GLUCOSE_MZ)
    assert await _members(async_session_factory, glucose.batch_peak_id) == {
        "A1",
        "B1",
    }
    naphthol = _at(anchors, NAPHTHOL_MZ)
    assert naphthol.batch_peak_id not in before
    assert await _members(async_session_factory, naphthol.batch_peak_id) == {"B3"}


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
        batch_id, samples, _ = await make_batch(seed=seed, collection=collection)
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


async def _move_peak(session_factory, sample_item_id, peak_id, mz) -> None:
    async with session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.sample_item_id == sample_item_id,
                PeakAssignment.sample_peak_id == peak_id,
            )
            .values(sample_peak_mz=mz)
        )
        await session.commit()


async def test_a_seed_its_last_member_leaves_is_deleted(
    async_session_factory, make_batch
):
    """A seeded anchor is an anchor like any other once minted: a re-fold
    that takes its only member away deletes it. The peak, now 59 ppm off the
    ion once corrected, is outside the window a seed there would carry, so
    it mints an unseeded anchor where it is; back on the ion, it mints a seed
    again, and the unseeded anchor goes."""
    batch_id, samples, _ = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    glucose = _at(await _anchors(async_session_factory, batch_id), GLUCOSE_MZ)

    await _move_peak(async_session_factory, samples["A"], "A1", _ppm(GLUCOSE_MZ, 60))
    await fold_sample_into_batch_peaks(samples["A"])

    anchors = await _anchors(async_session_factory, batch_id)
    assert glucose.batch_peak_id not in {a.batch_peak_id for a in anchors}
    assert not any(a.seeded for a in anchors)
    moved = _at(anchors, _ppm(GLUCOSE_MZ, 59))
    assert await _members(async_session_factory, moved.batch_peak_id) == {"A1"}

    await _move_peak(async_session_factory, samples["A"], "A1", _ppm(GLUCOSE_MZ, 2.5))
    await fold_sample_into_batch_peaks(samples["A"])

    back = await _anchors(async_session_factory, batch_id)
    assert moved.batch_peak_id not in {a.batch_peak_id for a in back}
    seed = _at(back, GLUCOSE_MZ)
    assert seed.seeded
    assert await _members(async_session_factory, seed.batch_peak_id) == {"A1"}


async def test_turning_the_option_on_moves_no_anchor(async_session_factory, make_batch):
    """A folded batch whose option is turned on keeps its anchors where they
    are, through a fold and a rebuild: the glucose anchor stays where A put
    it and B's glucose line joins it. Only an anchor minted from then on is
    seeded: B's naphthol line's."""
    batch_id, samples, _ = await make_batch(seed=False, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    before = {
        a.batch_peak_id: a.mz for a in await _anchors(async_session_factory, batch_id)
    }
    async with async_session_factory() as session:
        await session.execute(
            update(SampleBatch)
            .where(SampleBatch.sample_batch_id == batch_id)
            .values(seed_batch_peaks=True)
        )
        await session.commit()
    await fold_sample_into_batch_peaks(samples["B"])
    assert await backfill_sample_batch_peaks(batch_id) == (2, 0)

    anchors = await _anchors(async_session_factory, batch_id)
    after = {a.batch_peak_id: a.mz for a in anchors}
    assert {k: after[k] for k in before} == before
    assert [a.mz for a in anchors if a.seeded] == [NAPHTHOL_MZ]
    glucose = _at(anchors, _ppm(GLUCOSE_MZ, 1.5))
    assert await _members(async_session_factory, glucose.batch_peak_id) == {
        "A1",
        "B1",
    }


async def test_a_rebuild_seeds_a_batch_with_the_option_on(
    async_session_factory, make_batch
):
    """A batch with the option on and no ledger yet is seeded by the rebuild
    that first folds it."""
    batch_id, _, _ = await make_batch(seed=True, collection=True)
    assert await backfill_sample_batch_peaks(batch_id) == (2, 0)
    anchors = await _anchors(async_session_factory, batch_id)
    assert [a.mz for a in anchors if a.seeded] == [NAPHTHOL_MZ, GLUCOSE_MZ]


async def test_rows_folded_without_a_run_seed_as_a_runs_do(
    async_session_factory, make_batch
):
    """The run-less fold hands the fold Stage A's rows in memory; their
    target lines seed as a run's rows do."""
    batch_id, samples, ion_ids = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(
        samples["B"], rows=_stage_a_rows(_SPECS["B"], ion_ids), persisted=False
    )
    anchors = await _anchors(async_session_factory, batch_id)
    assert [a.mz for a in anchors if a.seeded] == [NAPHTHOL_MZ, GLUCOSE_MZ]


async def test_a_run_snapshot_keeps_the_mark(async_session_factory, make_batch):
    """A batch run snapshots the ledger it replaces, and a read of that run
    names the seeded row as the live ledger did."""
    batch_id, samples, _ = await make_batch(seed=True, collection=True)
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
    assert [round(row["mz"], 4) for row in ledger["data"] if row["seeded"]] == [
        round(GLUCOSE_MZ, 4)
    ]
    assert sum(not row["seeded"] for row in ledger["data"]) == 4


async def test_a_tof_line_seeds_at_the_merged_line_a_tof_run_matches(
    async_session_factory, make_batch, monkeypatch
):
    """A TOF run matches the merged low-resolution lines, so a TOF anchor is
    seeded at the glucose ion's merged line. The naphthol ion has no such
    line, so B's naphthol line mints at its own m/z."""
    monkeypatch.setattr(
        batch_peaks_controller, "get_instrument_type", lambda filename: "tof"
    )
    batch_id, samples, _ = await make_batch(seed=True, collection=True)
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])
    anchors = await _anchors(async_session_factory, batch_id)
    seeds = [a for a in anchors if a.seeded]
    assert [(a.mz, a.intensity_variable) for a in seeds] == [
        (181.0711, "sum_peak_areas")
    ]
    assert await _members(async_session_factory, seeds[0].batch_peak_id) == {
        "A1",
        "B1",
    }
    naphthol = next(a for a in anchors if abs(a.mz - NAPHTHOL_MZ) < 1e-3)
    assert naphthol.mz == pytest.approx(_ppm(NAPHTHOL_MZ, 1.0), abs=1e-9)


def _stage_a_rows(specs, ion_ids: dict) -> list:
    """Rows as the run-less fold hands them over: in memory, never written."""
    return [
        SimpleNamespace(
            **_row_fields(spec, ion_ids),
            peak_assignment_id=gen_id(32),
            ionization_mechanism_id=None,
            target_compound_id=None,
            owner_peak_assignment_id=None,
            provenance=None,
        )
        for spec in specs
    ]


async def test_a_target_ions_line_read_in_another_role_does_not_seed(
    async_session_factory, make_batch
):
    """A row that names the glucose ion but reads its peak as an isotopologue
    of it rather than its monoisotopic line seeds nothing, even with the peak
    on the ion's m/z."""
    batch_id, samples, ion_ids = await make_batch(seed=True, collection=True)
    specs = [
        (spec[0], spec[1], spec[2], spec[3], "iso_child", *spec[5:])
        if spec[0] == "A1"
        else spec
        for spec in _SPECS["A"]
    ]
    await fold_sample_into_batch_peaks(
        samples["A"], rows=_stage_a_rows(specs, ion_ids), persisted=False
    )
    anchors = await _anchors(async_session_factory, batch_id)
    assert not any(a.seeded for a in anchors)
    assert _at(anchors, _ppm(GLUCOSE_MZ, 1.5))


async def test_an_ion_of_another_batchs_collection_does_not_seed(
    async_session_factory, make_batch
):
    """The rows of a sample of a batch with no collection name ions of a
    collection another batch has: the seeds are the folding batch's own
    collections' ions, so none of them seeds."""
    _, _, ion_ids = await make_batch(seed=False, collection=True)
    batch_id, samples, _ = await make_batch(seed=True, collection=False)
    await fold_sample_into_batch_peaks(
        samples["A"], rows=_stage_a_rows(_SPECS["A"], ion_ids), persisted=False
    )
    await fold_sample_into_batch_peaks(
        samples["B"], rows=_stage_a_rows(_SPECS["B"], ion_ids), persisted=False
    )
    anchors = await _anchors(async_session_factory, batch_id)
    assert not any(a.seeded for a in anchors)
    assert _at(anchors, _ppm(GLUCOSE_MZ, 1.5))
