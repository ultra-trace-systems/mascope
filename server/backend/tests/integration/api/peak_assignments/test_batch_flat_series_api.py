"""Integration tests for the flat-series reading (step 4.2e).

Seeds a batch of 24 samples folded into the ledger: six compounds whose
traces follow the day, so the batch varies; a reagent ion that dims through
the batch, the normaliser; a nitrogen-rich cluster that holds a constant
ratio to it, at assigned; the same at candidate; a compound with a diel trace
of its own, at assigned; and a nitrogen dioxide reading whose ion the
bromide source's own list names, flat. One sample is folded from a run of its
own, which resolved that profile. The traces' arithmetic is the library's
(``test_coherence.py``); what is proven here is the batch-level contract
through *Read co-variation*: every committed reading is flagged flat or not,
the listed ion is named only from the batch's own runs' profiles, nothing
moves - no tier, role, member or claim -, a later fold keeps the flag, a new
reading replaces it, a batch that holds still flags nothing, and the ledger,
an earlier run's snapshot, the inspector and the export serve it. See
``batch_coherence.py``.
"""

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.new.peak_assignments.batch_coherence import (
    run_batch_co_variation,
)
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    batch_species_for,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    FLAT_SERIES_KEY,
    TIER_REASONS_KEY,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_CO_VARIATION,
    complete_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.fold_view import (
    fold_assignment_id,
    fold_run_id,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

#: The co-variation suite's adduct, made where that suite has not.
MECH, NOTATION = "mech-coh-h2po4", "[M+H2PO4]-"

N_SAMPLES = 24
RUN_SAMPLE = "S00"

#: The reagent ion dims through the batch: its signal falls by a third.
REAGENT = [1e6 * (1.0 - 0.015 * i) for i in range(N_SAMPLES)]


def _day(i, phase=0.0, amplitude=0.5):
    return amplitude * math.sin(2 * math.pi * i / 12.0 + phase)


def _peaks(steady=False):
    """Peak id -> (m/z, formula, ion, linear trace, tier)."""
    peaks = {}
    for k in range(6):
        trace = [
            10.0 ** (4.0 + (0.0 if steady else _day(i, phase=k)))
            for i in range(N_SAMPLES)
        ]
        peaks[f"bg-{k}"] = (150.0 + 10 * k, f"C{5 + k}H8O4", None, trace, "assigned")
    # A constant ratio to the reagent ion: background.
    peaks["flat"] = (
        231.0,
        "C2H3N3",
        None,
        [r * 2e-3 for r in REAGENT],
        "assigned",
    )
    peaks["flat-c"] = (
        241.0,
        "C2H2N2O",
        None,
        [r * 1e-3 for r in REAGENT],
        "candidate",
    )
    # Its own diel cycle on top of the reagent's: an analyte.
    peaks["diel"] = (
        251.0,
        "C10H16O3",
        None,
        [
            r * 1e-3 * 10.0 ** (0.0 if steady else _day(i, amplitude=0.8))
            for i, r in enumerate(REAGENT)
        ],
        "assigned",
    )
    # An ion of the source's air, which the library names.
    peaks["no2"] = (45.9935, "NO2", "NO2-", [r * 5e-3 for r in REAGENT], "assigned")
    return peaks


#: Each sample's total ion current where the batch holds no reagent ion: it
#: drifts as the reagent's signal does.
TIC = [10.0 * r for r in REAGENT]


def _rows(
    sample_item_id, index, *, run_id=None, peaks=None, overrides=None, reagent=True
):
    rows = []
    for peak_id, (mz, formula, ion, trace, tier) in (peaks or _peaks()).items():
        value = (overrides or {}).get(peak_id, trace)[index]
        rows.append(
            dict(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=run_id or fold_run_id(sample_item_id),
                sample_item_id=sample_item_id,
                sample_peak_id=peak_id,
                sample_peak_mz=mz,
                sample_peak_intensity=value,
                role="M0",
                assigned_formula=formula,
                ion_formula=ion or f"{formula}-ion",
                ionization_mechanism_id=MECH,
                source="untargeted",
                tier=tier,
                fit_score=0.9,
                mz_error_ppm=0.1,
                owner_peak_assignment_id=None,
                provenance=None,
            )
        )
    if not reagent:
        return rows
    rows.append(
        dict(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=run_id or fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id="reagent",
            sample_peak_mz=124.9875,
            sample_peak_intensity=REAGENT[index],
            role="reagent",
            assigned_formula=None,
            ion_formula="H2PO4-",
            ionization_mechanism_id=None,
            source="reagent",
            tier="unassigned",
            fit_score=None,
            mz_error_ppm=0.1,
            owner_peak_assignment_id=None,
            provenance=None,
        )
    )
    return rows


async def _seed(async_session_factory, pa_test_data, *, steady=False, reagent=True):
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    samples = {}
    peaks = _peaks(steady)
    async with async_session_factory() as session:
        dataset_id = (
            await session.execute(
                select(SampleBatch.dataset_id).where(
                    SampleBatch.sample_batch_id == pa_test_data["sample_batch_id"]
                )
            )
        ).scalar_one()
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"Flat series {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, MECH) is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=MECH,
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=NOTATION,
                )
            )
        for i in range(N_SAMPLES):
            name = f"S{i:02d}"
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-flat-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 11, 16, i % 24, 0, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[40.0, 500.0],
                    polarity="-",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=f"Flat {name}",
                    sample_item_type="sample",
                    polarity="-",
                    tic=TIC[i],
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        run_id = gen_id()
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=samples[RUN_SAMPLE],
                engine_version="0.1.0-test",
                status="completed",
                config={"resolved_profile": {"profile": "BR"}},
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        run_rows = {}
        for row in _rows(
            samples[RUN_SAMPLE], 0, run_id=run_id, peaks=peaks, reagent=reagent
        ):
            session.add(PeakAssignment(**row))
            run_rows[row["sample_peak_id"]] = row["peak_assignment_id"]
        await session.commit()
    for i in range(N_SAMPLES):
        name = f"S{i:02d}"
        if name == RUN_SAMPLE:
            folded = await fold_sample_into_batch_peaks(samples[name])
        else:
            folded = await fold_sample_into_batch_peaks(
                samples[name],
                rows=[
                    SimpleNamespace(**r)
                    for r in _rows(samples[name], i, peaks=peaks, reagent=reagent)
                ],
                persisted=False,
            )
        assert folded == batch_id
    return {"batch_id": batch_id, "samples": samples, "run_rows": run_rows}


@pytest_asyncio.fixture
async def flat_batch(async_session_factory, pa_test_data):
    return await _seed(async_session_factory, pa_test_data)


async def _anchors(session_factory, batch_id, *, reagent=True):
    """Peak id -> its anchor, by the m/z each peak was seeded at."""
    async with session_factory() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
    by_mz = {round(anchor.mz, 2): anchor for anchor in anchors}
    mzs = {peak_id: mz for peak_id, (mz, *_rest) in _peaks().items()}
    if reagent:
        mzs["reagent"] = 124.9875
    return {peak_id: by_mz[round(mz, 2)] for peak_id, mz in mzs.items()}


async def _member_tiers(session_factory, anchor):
    async with session_factory() as session:
        return {
            sample: tier
            for sample, tier in (
                await session.execute(
                    select(
                        BatchPeakOccurrence.sample_item_id, BatchPeakOccurrence.tier
                    ).where(BatchPeakOccurrence.batch_peak_id == anchor.batch_peak_id)
                )
            ).all()
        }


def _record(anchor):
    return (anchor.provenance or {}).get(FLAT_SERIES_KEY)


def _reasons(anchor):
    return [
        reason["rule"]
        for reason in (anchor.provenance or {}).get(TIER_REASONS_KEY) or []
    ]


ASSIGNED = tier_code("assigned")


async def test_a_flat_reading_is_flagged_and_moves_no_tier(
    async_session_factory, flat_batch
):
    batch_id = flat_batch["batch_id"]
    before = await _anchors(async_session_factory, batch_id)
    summary = await run_batch_co_variation(batch_id, batch_peak_run_id="run-flat")
    anchors = await _anchors(async_session_factory, batch_id)

    flat = anchors["flat"]
    record = _record(flat)
    assert record["verdict"] == "flat"
    assert record["formula"] == "C2H3N3"
    assert record["cv"] == pytest.approx(0.0, abs=1e-3)
    assert record["n_present"] == record["n_spectra"] == N_SAMPLES
    assert record["normaliser"] == "reagent" and record["normalised"] is True
    assert record["source_ion"] is None
    assert record["batch_peak_run_id"] == "run-flat"
    assert _record(anchors["flat-c"])["verdict"] == "flat"
    diel = _record(anchors["diel"])
    assert diel["verdict"] == "varying" and diel["cv"] > 0.25
    # The reagent ion is the normaliser, not a reading.
    assert _record(anchors["reagent"]) is None

    # Nothing moved: no tier, role, formula, reason or member.
    for peak_id, anchor in anchors.items():
        assert anchor.consensus_tier == before[peak_id].consensus_tier
        assert anchor.consensus_role == before[peak_id].consensus_role
        assert anchor.consensus_formula == before[peak_id].consensus_formula
        assert _reasons(anchor) == []
    for peak_id in ("flat", "no2"):
        tiers = await _member_tiers(async_session_factory, anchors[peak_id])
        assert set(tiers.values()) == {ASSIGNED}

    flat_summary = summary["flat_series"]
    [partition] = flat_summary["partitions"]
    assert partition["normaliser"] == "reagent"
    assert partition["steady"] is False
    assert partition["profiles"] == ["BR"]
    assert flat_summary["readings"] == 10
    assert flat_summary["flat"] == 3
    assert flat_summary["flat_assigned"] == 2
    assert flat_summary["flat_source_ions"] == 1


async def test_a_listed_flat_ion_is_named_and_not_claimed(
    async_session_factory, flat_batch
):
    batch_id, samples = flat_batch["batch_id"], flat_batch["samples"]
    await run_batch_co_variation(batch_id)
    no2 = (await _anchors(async_session_factory, batch_id))["no2"]
    assert _record(no2)["source_ion"]["label"] == "[NO2]-"
    assert no2.consensus_role is None
    assert no2.consensus_formula == "NO2" and no2.consensus_tier == "assigned"
    # Claiming is the known-species step's: a sample's next run takes nothing
    # on this flag.
    assert await batch_species_for(await fetch_sample(samples["S05"])) == []


async def test_no_source_ion_where_the_batch_s_runs_resolved_none(
    async_session_factory, flat_batch
):
    batch_id = flat_batch["batch_id"]
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignmentRun)
            .where(
                PeakAssignmentRun.sample_item_id == flat_batch["samples"][RUN_SAMPLE]
            )
            .values(config={})
        )
        await session.commit()
    summary = await run_batch_co_variation(batch_id)
    no2 = (await _anchors(async_session_factory, batch_id))["no2"]
    assert _record(no2)["verdict"] == "flat"
    assert _record(no2)["source_ion"] is None
    assert summary["flat_series"]["partitions"][0]["profiles"] == []


async def test_the_sample_s_latest_completed_own_run_names_its_profile(
    async_session_factory, flat_batch
):
    # Later runs that do not speak for the sample: a decoy arm, a failed run
    # and an import that resolved no profile. Each names a profile whose list
    # holds no NO2- but for the decoy and the failed run, which would.
    sample = flat_batch["samples"][RUN_SAMPLE]
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    async with async_session_factory() as session:
        for i, (status, decoy, config) in enumerate(
            [
                (
                    "completed",
                    "mz_shift_ppm=+9",
                    {"resolved_profile": {"profile": "UR"}},
                ),
                ("failed", None, {"resolved_profile": {"profile": "UR"}}),
                ("completed", None, {}),
            ]
        ):
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=gen_id(),
                    sample_item_id=sample,
                    engine_version="0.1.0-test",
                    status=status,
                    decoy=decoy,
                    config=config,
                    peak_assignment_run_utc_created=later + timedelta(minutes=i),
                )
            )
        await session.commit()
    summary = await run_batch_co_variation(flat_batch["batch_id"])
    assert summary["flat_series"]["partitions"][0]["profiles"] == ["BR"]
    no2 = (await _anchors(async_session_factory, flat_batch["batch_id"]))["no2"]
    assert _record(no2)["source_ion"]["label"] == "[NO2]-"

    # A later completed run of the sample's own that resolved another profile
    # speaks for it from then on.
    async with async_session_factory() as session:
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=gen_id(),
                sample_item_id=sample,
                engine_version="0.1.0-test",
                status="completed",
                config={"resolved_profile": {"profile": "UR"}},
                peak_assignment_run_utc_created=later + timedelta(minutes=10),
            )
        )
        await session.commit()
    summary = await run_batch_co_variation(flat_batch["batch_id"])
    assert summary["flat_series"]["partitions"][0]["profiles"] == ["UR"]
    no2 = (await _anchors(async_session_factory, flat_batch["batch_id"]))["no2"]
    assert _record(no2)["source_ion"] is None


async def test_a_later_fold_keeps_the_flag(async_session_factory, flat_batch):
    batch_id, samples = flat_batch["batch_id"], flat_batch["samples"]
    await run_batch_co_variation(batch_id)
    flagged = _record((await _anchors(async_session_factory, batch_id))["flat"])
    name = "S07"
    await fold_sample_into_batch_peaks(
        samples[name],
        rows=[SimpleNamespace(**r) for r in _rows(samples[name], 7)],
        persisted=False,
    )
    kept = (await _anchors(async_session_factory, batch_id))["flat"]
    assert _record(kept) == flagged
    assert kept.consensus_tier == "assigned"


async def test_a_new_reading_replaces_the_flag(async_session_factory, flat_batch):
    batch_id, samples = flat_batch["batch_id"], flat_batch["samples"]
    await run_batch_co_variation(batch_id, batch_peak_run_id="run-1")
    before = await _anchors(async_session_factory, batch_id)
    # The cluster now follows a day of its own.
    plume = [r * 2e-3 * 10.0 ** _day(i, amplitude=0.8) for i, r in enumerate(REAGENT)]
    async with async_session_factory() as session:
        for i in range(N_SAMPLES):
            await session.execute(
                update(BatchPeakOccurrence)
                .where(
                    BatchPeakOccurrence.batch_peak_id == before["flat"].batch_peak_id,
                    BatchPeakOccurrence.sample_item_id == samples[f"S{i:02d}"],
                )
                .values(intensity=plume[i])
            )
        await session.commit()
    await run_batch_co_variation(batch_id, batch_peak_run_id="run-2")
    anchors = await _anchors(async_session_factory, batch_id)
    assert _record(anchors["flat"])["verdict"] == "varying"
    assert _record(anchors["flat"])["batch_peak_run_id"] == "run-2"
    assert _record(anchors["no2"])["batch_peak_run_id"] == "run-2"


async def test_a_batch_that_holds_still_flags_nothing(
    async_session_factory, pa_test_data
):
    seeded = await _seed(async_session_factory, pa_test_data, steady=True)
    summary = await run_batch_co_variation(seeded["batch_id"])
    anchors = await _anchors(async_session_factory, seeded["batch_id"])
    assert summary["flat_series"]["partitions"][0]["steady"] is True
    assert summary["flat_series"]["flat"] == 0
    assert _record(anchors["flat"])["verdict"] == "untestable"
    assert _record(anchors["flat"])["reason"] == "steady_batch"


async def test_a_batch_with_no_reagent_ion_is_read_against_the_tic(
    async_session_factory, pa_test_data
):
    seeded = await _seed(async_session_factory, pa_test_data, reagent=False)
    summary = await run_batch_co_variation(seeded["batch_id"])
    anchors = await _anchors(async_session_factory, seeded["batch_id"], reagent=False)
    [partition] = summary["flat_series"]["partitions"]
    assert partition["normaliser"] == "tic"
    assert partition["normalised"] is True
    record = _record(anchors["flat"])
    # The cluster drifts with the TIC: divided by it, it holds still.
    assert record["normaliser"] == "tic"
    assert record["cv"] == pytest.approx(0.0, abs=1e-3)
    assert record["verdict"] == "flat"


async def test_the_ledger_a_snapshot_the_inspector_and_the_export_serve_it(
    guest_client, async_session_factory, flat_batch
):
    batch_id, samples = flat_batch["batch_id"], flat_batch["samples"]
    run_id = await start_run(batch_id, ACTION_CO_VARIATION)
    await run_batch_co_variation(batch_id, batch_peak_run_id=run_id)
    await complete_run(batch_id, run_id)
    anchors = await _anchors(async_session_factory, batch_id)

    async def ledger(**params):
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch_id}",
            params={"min_n_present": 1, **params},
        )
        assert response.status_code == 200, response.text
        return {row["batch_peak_id"]: row for row in response.json()["data"]}

    live = await ledger()
    flat = live[anchors["flat"].batch_peak_id]
    assert flat["flat_series"]["verdict"] == "flat"
    assert flat["consensus_tier"] == "assigned"
    assert flat["tier_reasons"] is None
    assert "flat_source" not in flat
    no2 = live[anchors["no2"].batch_peak_id]
    assert no2["flat_series"]["source_ion"]["label"] == "[NO2]-"
    assert live[anchors["diel"].batch_peak_id]["flat_series"]["verdict"] == "varying"

    later = await start_run(batch_id, ACTION_CO_VARIATION)
    await complete_run(batch_id, later)
    earlier = await ledger(batch_peak_run_id=run_id)
    assert earlier[anchors["flat"].batch_peak_id]["flat_series"] == flat["flat_series"]
    assert earlier[anchors["no2"].batch_peak_id]["flat_series"] == no2["flat_series"]

    async def detail(sample_item_id, peak_assignment_id):
        response = await guest_client.get(
            f"/api/peak-assignments/sample/{sample_item_id}/assignment/"
            f"{peak_assignment_id}"
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["data"]
        return row

    run_rows = flat_batch["run_rows"]
    own = await detail(samples[RUN_SAMPLE], run_rows["flat"])
    assert own["batch_flat_series"] == flat["flat_series"]
    assert own["tier"] == "assigned"
    derived = await detail(
        samples["S03"], fold_assignment_id(anchors["flat"].batch_peak_id)
    )
    assert derived["batch_flat_series"] == flat["flat_series"]
    assert (await detail(samples[RUN_SAMPLE], run_rows["bg-0"]))["batch_flat_series"][
        "verdict"
    ] == "varying"

    response = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}/members",
        params={"sample_item_id": samples["S03"]},
    )
    assert response.status_code == 200, response.text
    rows = {row["batch_peak_id"]: row for row in response.json()["data"]}
    assert rows[anchors["flat"].batch_peak_id]["flat_series"] == "flat"
    assert rows[anchors["flat"].batch_peak_id]["flat_series_cv"] == pytest.approx(
        0.0, abs=1e-3
    )
    assert rows[anchors["no2"].batch_peak_id]["flat_series_source_ion"] == "[NO2]-"
    assert rows[anchors["diel"].batch_peak_id]["flat_series"] == "varying"
