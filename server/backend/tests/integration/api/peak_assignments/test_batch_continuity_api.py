"""Integration tests for the batch's ratio-stability pass (step 4.11).

Seeds an acquisition batch of fourteen samples an hour apart, each folded
from a run of its own: a compound read through two channels, whose M0 has an
M+1 line and an M+2 line linked through the M+1 (a chain), and a line whose
samples split their votes on what owns it. The M+1's ratio to the M0 holds
while the source swings over three orders of magnitude; the second channel's
ratio to the first climbs threefold. One sample reads the M+1 as another
compound, one is labelled a blank and one comes from a file with no
instrument function. The ratio arithmetic is the unit suite's
(``tests/unit/api/peak_assignments/test_batch_continuity.py``); what is
proven here is the batch-level contract: the pass grades every pair off the
folded ledger and writes the two columns and nothing else, a second pass
writes nothing, the ledger and an earlier run's snapshot serve the grade, a
fold since the pass marks it stale, the fold trigger (off by default) and the
rebuild's trigger with its packets, the lock a second pass skips on, a batch
that is not time-ordered, the per-batch limit, and the route.
See ``continuity.py``.
"""

import math
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from mascope_backend.api.new.peak_assignments import (
    batch_peaks,
    batch_peaks_controller,
    batch_peaks_routes,
    continuity,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import CO_VARIATION_KEY
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    backfill_sample_batch_peaks,
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_CO_VARIATION,
    complete_run,
    current_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.continuity import (
    REASON_GAP,
    SKIPPED_CURRENT,
    SKIPPED_IN_PROGRESS,
    WARNING_PARTIAL,
    run_batch_continuity,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakRun,
    InstrumentFunction,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

#: Two adducts no other suite names: the mechanism table's notations are
#: unique, and the integration database is shared by the whole run.
MECH_A, NOTATION_A = "mech-cont-cs", "[M+Cs]+"
MECH_B, NOTATION_B = "mech-cont-rb", "[M+Rb]+"

T0 = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
N_GRADED = 12
#: The labelled blank and the file with no instrument function.
BLANK, NO_FUNCTION = 12, 13
N_SAMPLES = 14
#: The sample that reads the M+1 line as another compound.
DISSENT = 4

SOURCE = [10 ** (4 + 1.5 * math.sin(0.9 * i)) for i in range(N_SAMPLES)]
WOBBLE = [0.03 * math.sin(2.3 * i + 0.4) for i in range(N_SAMPLES)]
M1_SHARE, M2_SHARE = 0.066, 0.004

#: Peak id -> m/z, for finding each anchor again.
MZ = {"m0": 314.97, "m1": 315.98, "m2": 316.98, "rb": 267.0, "split": 400.1}


def _rows(run_id, sample_item_id, i):
    """One sample's run rows, in the order the owner links need."""
    ids = {key: gen_id(32) for key in MZ}
    wild = 30.0 if i in (BLANK, NO_FUNCTION) else 1.0

    def row(key, formula, ion, mechanism, intensity, role="M0", owner=None):
        return PeakAssignment(
            peak_assignment_id=ids[key],
            peak_assignment_run_id=run_id,
            sample_item_id=sample_item_id,
            sample_peak_id=key,
            sample_peak_mz=MZ[key],
            sample_peak_intensity=intensity,
            role=role,
            assigned_formula=formula,
            ion_formula=ion,
            ionization_mechanism_id=mechanism,
            source="database",
            tier="assigned",
            fit_score=0.9,
            mz_error_ppm=0.1,
            owner_peak_assignment_id=ids[owner] if owner else None,
        )

    source = SOURCE[i]
    rows = [
        row("m0", "C6H12O6", "C6H12O6Cs+", MECH_A, source),
        (
            row("m1", "C9H9NO", "C9H9NOCs+", MECH_A, source * 2.0)
            if i == DISSENT
            else row(
                "m1",
                "C6H12O6",
                "[13C]C5H12O6Cs+",
                MECH_A,
                source * M1_SHARE * math.exp(WOBBLE[i]) * wild,
                role="iso_child",
                owner="m0",
            )
        ),
        # Owned by the M+1 row: its anchor links to the M+1's, a chain the
        # ledger draws under the M0.
        row(
            "m2",
            "C6H12O6",
            "[13C2]C4H12O6Cs+",
            MECH_A,
            source * M2_SHARE * math.exp(-WOBBLE[i]),
            role="iso_child",
            owner=None if i == DISSENT else "m1",
        ),
        row("rb", "C6H12O6", "C6H12O6Rb+", MECH_B, source * 0.5 * math.exp(0.1 * i)),
        # An M+1 of the M0 in half the samples, a compound of its own in the
        # rest: no majority, no link, no pair.
        (
            row(
                "split",
                "C6H12O6",
                "[13C]C5H12O6Cs+",
                MECH_A,
                source * 0.01,
                role="iso_child",
                owner="m0",
            )
            if i % 2
            else row("split", "C12H18O5", "C12H18O5Cs+", MECH_A, source * 0.01)
        ),
    ]
    if i == DISSENT:
        # Its owner is a compound of its own in this sample.
        rows[2].owner_peak_assignment_id = ids["m0"]
    return rows


async def _seed(async_session_factory, pa_test_data):
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    samples = []
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
                sample_batch_name=f"Continuity {batch_id}",
                sample_batch_type="ACQUISITION",
                sample_batch_utc_created=now,
            )
        )
        for mechanism, notation in ((MECH_A, NOTATION_A), (MECH_B, NOTATION_B)):
            if await session.get(IonizationMechanism, mechanism) is None:
                session.add(
                    IonizationMechanism(
                        ionization_mechanism_id=mechanism,
                        ionization_mechanism_polarity="+",
                        ionization_mechanism=notation,
                    )
                )
        function_id = gen_id(32)
        session.add(
            InstrumentFunction(
                instrument_function_id=function_id,
                instrument="orbi-test",
                method_file="continuity.meth",
                datetime_utc=T0,
            )
        )
        await session.flush()
        for i in range(N_SAMPLES):
            sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
            at = T0 + timedelta(hours=i)
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    instrument_function_id=None if i == NO_FUNCTION else function_id,
                    filename=f"orbi-continuity-{i:02d}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=at.replace(tzinfo=None),
                    datetime_utc=at,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="+",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=f"Continuity {i:02d}",
                    sample_item_type="BLANK" if i == BLANK else "SAMPLE",
                    polarity="+",
                    sample_item_utc_created=now,
                )
            )
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=run_id,
                    sample_item_id=sample_item_id,
                    engine_version="0.1.0-test",
                    status="completed",
                    config={},
                    peak_assignment_run_utc_created=now,
                    peak_assignment_run_utc_completed=now,
                )
            )
            await session.flush()
            for row in _rows(run_id, sample_item_id, i):
                session.add(row)
                await session.flush()
            samples.append(sample_item_id)
        await session.commit()
    for sample_item_id in samples:
        assert await fold_sample_into_batch_peaks(sample_item_id) == batch_id
    return {"batch_id": batch_id, "samples": samples}


@pytest_asyncio.fixture
async def continuity_batch(async_session_factory, pa_test_data):
    return await _seed(async_session_factory, pa_test_data)


async def _anchors(session_factory, batch_id):
    """Peak id -> its anchor."""
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
    by_mz = {round(anchor.mz, 1): anchor for anchor in anchors}
    return {key: by_mz[round(mz, 1)] for key, mz in MZ.items()}


def _pair(anchor, partner):
    [entry] = [
        p
        for p in anchor.continuity["pairs"]
        if p["partner_batch_peak_id"] == partner.batch_peak_id
    ]
    return entry


async def test_the_pass_grades_every_pair_off_the_folded_ledger(
    async_session_factory, continuity_batch
):
    batch_id = continuity_batch["batch_id"]
    before = await _anchors(async_session_factory, batch_id)
    # The fold drew the chain and the split as the pass expects them.
    assert before["m1"].isotopologue_of == before["m0"].batch_peak_id
    assert before["m2"].isotopologue_of == before["m1"].batch_peak_id
    assert before["split"].isotopologue_of is None
    async with async_session_factory() as session:
        stamp = (await current_run(session, batch_id)).ledger_utc_modified

    summary = await run_batch_continuity(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    m0, m1, m2, rb = (anchors[k] for k in ("m0", "m1", "m2", "rb"))

    assert summary["time_ordered"] is True
    assert summary["pairs"] == 3
    assert summary["pairs_by_kind"] == {"isotopologue": 2, "adduct": 1}
    assert summary["written"] == 4

    # The M+1 holds its share through the source's swing; so does the M+2,
    # paired with the root of the chain it is drawn under.
    assert m1.continuity_grade == "stable"
    to_m1 = _pair(m0, m1)
    assert to_m1["kind"] == "isotopologue"
    assert to_m1["ratio_median"] == pytest.approx(M1_SHARE, rel=0.05)
    assert to_m1["n_both"] == N_SAMPLES
    assert to_m1["n_excluded_blank"] == 2
    assert to_m1["n_excluded_dissenting"] == 1
    assert to_m1["n"] == N_GRADED - 1
    assert m2.continuity_grade == "stable"
    assert _pair(m0, m2)["ratio_median"] == pytest.approx(M2_SHARE, rel=0.05)
    assert _pair(m0, m2)["n_excluded_dissenting"] == 0
    with pytest.raises(ValueError):
        _pair(m1, m2)
    # The second channel climbs threefold and nothing says the two traces
    # co-vary: red, on both of its anchors.
    to_rb = _pair(m0, rb)
    assert to_rb["kind"] == "adduct"
    assert to_rb["grade"] == "red"
    assert to_rb["drift_log"] == pytest.approx(1.1, abs=0.05)
    assert to_rb["co_variation"] is None
    assert rb.continuity_grade == "red"
    assert m0.continuity_grade == "red"
    assert m0.continuity["n_present_at_compute"] == m0.n_present
    assert m0.continuity["formula"] == "C6H12O6"
    # The split line is in no pair.
    assert anchors["split"].continuity_grade is None
    assert anchors["split"].continuity is None

    # Nothing but the two columns moved, and the fold's stamp is the fold's.
    for key, anchor in anchors.items():
        assert anchor.consensus_tier == before[key].consensus_tier
        assert anchor.consensus_formula == before[key].consensus_formula
        assert anchor.batch_peak_utc_modified == before[key].batch_peak_utc_modified
    async with async_session_factory() as session:
        assert (await current_run(session, batch_id)).ledger_utc_modified > stamp


async def test_a_co_varying_reading_turns_the_channel_s_drift_into_drift(
    async_session_factory, continuity_batch
):
    batch_id = continuity_batch["batch_id"]
    anchors = await _anchors(async_session_factory, batch_id)
    rb = anchors["rb"]
    record = {
        "formula": rb.consensus_formula,
        "ionization_mechanism_id": rb.ionization_mechanism_id,
        "verdict": "co_varying",
        "partners": [
            {
                "batch_peak_id": anchors["m0"].batch_peak_id,
                "verdict": "co_varying",
                "r": 0.88,
                "n_spectra": 12,
            }
        ],
    }
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == rb.batch_peak_id)
            .values(provenance={**(rb.provenance or {}), CO_VARIATION_KEY: record})
        )
        await session.commit()
    await run_batch_continuity(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    to_rb = _pair(anchors["m0"], anchors["rb"])
    assert to_rb["grade"] == "drift"
    assert to_rb["co_variation"] == {
        "verdict": "co_varying",
        "r": 0.88,
        "n_spectra": 12,
    }
    # The source's chemistry moved and the assignment stands: no red left.
    assert anchors["m0"].continuity_grade == "drift"
    assert anchors["rb"].continuity_grade == "drift"


async def test_a_second_pass_over_the_same_ledger_writes_nothing(
    async_session_factory, continuity_batch
):
    batch_id = continuity_batch["batch_id"]
    await run_batch_continuity(batch_id)
    first = await _anchors(async_session_factory, batch_id)
    async with async_session_factory() as session:
        stamp = (await current_run(session, batch_id)).ledger_utc_modified
    again = await run_batch_continuity(batch_id)
    assert again["written"] == 0
    second = await _anchors(async_session_factory, batch_id)
    for key in MZ:
        assert second[key].continuity == first[key].continuity
    # Nor does it stamp the ledger: the stamp is the viz store's cache key, and
    # a pass that changed nothing must not send every client to reload.
    async with async_session_factory() as session:
        assert (await current_run(session, batch_id)).ledger_utc_modified == stamp


async def test_a_pair_that_no_longer_forms_is_cleared(
    async_session_factory, continuity_batch
):
    batch_id = continuity_batch["batch_id"]
    await run_batch_continuity(batch_id)
    rb = (await _anchors(async_session_factory, batch_id))["rb"]
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == rb.batch_peak_id)
            .values(consensus_tier="below_assignability")
        )
        await session.commit()
    summary = await run_batch_continuity(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    assert summary["pairs"] == 2
    assert anchors["rb"].continuity_grade is None
    assert anchors["rb"].continuity is None
    assert anchors["m0"].continuity_grade == "stable"


async def test_the_ledger_serves_the_grade_and_marks_it_stale_after_a_fold(
    guest_client, async_session_factory, continuity_batch
):
    batch_id, samples = continuity_batch["batch_id"], continuity_batch["samples"]
    await run_batch_continuity(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)

    async def ledger():
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch_id}", params={"min_n_present": 1}
        )
        assert response.status_code == 200, response.text
        return {row["batch_peak_id"]: row for row in response.json()["data"]}

    rows = await ledger()
    m0 = rows[anchors["m0"].batch_peak_id]
    assert m0["continuity_grade"] == "red"
    assert m0["continuity"] == anchors["m0"].continuity
    assert m0["continuity_stale"] is False
    assert rows[anchors["split"].batch_peak_id]["continuity_grade"] is None

    # A sample re-folded without its M0: the M0's prevalence moves, and with
    # the fold trigger off nothing regrades it.
    assert batch_peaks.CONTINUITY_ON_FOLD is False
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.sample_item_id == samples[0],
                PeakAssignment.sample_peak_id == "m0",
            )
            .values(sample_peak_mz=500.0)
        )
        await session.commit()
    await fold_sample_into_batch_peaks(samples[0])
    rows = await ledger()
    m0 = rows[anchors["m0"].batch_peak_id]
    assert m0["continuity_stale"] is True
    assert (
        m0["continuity"]["computed_utc"] == (anchors["m0"].continuity["computed_utc"])
    )
    assert rows[anchors["rb"].batch_peak_id]["continuity_stale"] is False


async def test_with_the_fold_trigger_on_a_fold_regrades_the_batch(
    async_session_factory, continuity_batch, monkeypatch
):
    batch_id, samples = continuity_batch["batch_id"], continuity_batch["samples"]
    await run_batch_continuity(batch_id)
    before = (await _anchors(async_session_factory, batch_id))["m0"].continuity
    monkeypatch.setattr(batch_peaks, "CONTINUITY_ON_FOLD", True)
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.sample_item_id == samples[0],
                PeakAssignment.sample_peak_id == "m0",
            )
            .values(sample_peak_mz=500.0)
        )
        await session.commit()
    await fold_sample_into_batch_peaks(samples[0])
    m0 = (await _anchors(async_session_factory, batch_id))["m0"]
    assert m0.continuity["n_present_at_compute"] == m0.n_present
    assert m0.continuity["computed_utc"] > before["computed_utc"]
    assert not batch_peaks.continuity_stale(m0)


async def test_a_fold_s_failing_pass_leaves_the_fold_done(
    async_session_factory, continuity_batch, monkeypatch
):
    batch_id, samples = continuity_batch["batch_id"], continuity_batch["samples"]
    monkeypatch.setattr(batch_peaks, "CONTINUITY_ON_FOLD", True)

    async def broken(*args, **kwargs):
        raise RuntimeError("no grading today")

    monkeypatch.setattr(continuity, "run_batch_continuity", broken)
    assert await fold_sample_into_batch_peaks(samples[0]) == batch_id


async def test_a_second_pass_skips_while_one_holds_the_batch(
    async_session_factory, continuity_batch
):
    batch_id = continuity_batch["batch_id"]
    async with async_session_factory() as holder:
        assert await continuity._try_continuity_lock(holder, batch_id)
        assert await run_batch_continuity(batch_id) == {"skipped": SKIPPED_IN_PROGRESS}
        await holder.rollback()
    assert "skipped" not in await run_batch_continuity(batch_id)


async def test_a_triggered_pass_a_newer_one_already_saw_is_a_no_op(
    async_session_factory, continuity_batch
):
    batch_id = continuity_batch["batch_id"]
    fold_committed = datetime.now(timezone.utc)
    await run_batch_continuity(batch_id)
    assert await run_batch_continuity(batch_id, triggered_at=fold_committed) == {
        "skipped": SKIPPED_CURRENT
    }
    later = await run_batch_continuity(
        batch_id, triggered_at=datetime.now(timezone.utc) + timedelta(seconds=1)
    )
    assert "skipped" not in later


async def test_a_batch_that_stops_being_time_ordered_has_its_grades_withdrawn(
    async_session_factory, continuity_batch
):
    batch_id, samples = continuity_batch["batch_id"], continuity_batch["samples"]
    await run_batch_continuity(batch_id)
    async with async_session_factory() as session:
        await session.execute(
            update(SampleBatch)
            .where(SampleBatch.sample_batch_id == batch_id)
            .values(sample_batch_type="ANALYSIS")
        )
        file_id = (
            await session.execute(
                select(SampleItem.sample_file_id).where(
                    SampleItem.sample_item_id == samples[-3]
                )
            )
        ).scalar_one()
        await session.execute(
            update(SampleFile)
            .where(SampleFile.sample_file_id == file_id)
            .values(datetime_utc=T0 + timedelta(days=60))
        )
        await session.commit()
    summary = await run_batch_continuity(batch_id)
    assert summary["time_ordered"] is False
    assert summary["reason"] == REASON_GAP
    anchors = await _anchors(async_session_factory, batch_id)
    for key in ("m0", "m1", "m2", "rb"):
        assert anchors[key].continuity_grade is None
        assert anchors[key].continuity["eligible"] is False
        assert anchors[key].continuity["reason"] == REASON_GAP
        assert anchors[key].continuity["pairs"] == []


async def test_over_its_limit_the_pass_marks_the_anchors_it_left(
    async_session_factory, continuity_batch, monkeypatch
):
    batch_id = continuity_batch["batch_id"]
    monkeypatch.setattr(continuity, "MAX_PAIRS_PER_BATCH", 1)
    summary = await run_batch_continuity(batch_id)
    assert summary["pairs_graded"] == 1
    assert summary["pairs_skipped"] == 2
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["m0"].continuity["warnings"] == [WARNING_PARTIAL]
    assert anchors["rb"].continuity_grade is None
    assert anchors["rb"].continuity["warnings"] == [WARNING_PARTIAL]


async def test_an_earlier_run_s_snapshot_keeps_the_grade_it_had(
    guest_client, async_session_factory, continuity_batch
):
    batch_id = continuity_batch["batch_id"]
    run_id = await start_run(batch_id, ACTION_CO_VARIATION)
    await complete_run(batch_id, run_id)
    await run_batch_continuity(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    later = await start_run(batch_id, ACTION_CO_VARIATION)
    await complete_run(batch_id, later)
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}",
        params={"min_n_present": 1, "batch_peak_run_id": run_id},
    )
    assert response.status_code == 200, response.text
    rows = {row["batch_peak_id"]: row for row in response.json()["data"]}
    m0 = rows[anchors["m0"].batch_peak_id]
    assert m0["continuity_grade"] == "red"
    assert m0["continuity"] == anchors["m0"].continuity
    assert m0["continuity_stale"] is False


async def test_a_rebuild_grades_the_batch_at_its_end_and_says_so(
    async_session_factory, continuity_batch, monkeypatch
):
    batch_id = continuity_batch["batch_id"]
    packets = []

    async def capture(notification, increment=None):
        packets.append((notification, increment))

    monkeypatch.setattr(
        batch_peaks_controller, "send_progress_user_notification", capture
    )
    folded, failed = await backfill_sample_batch_peaks(batch_id, process_id="proc-1")
    assert (folded, failed) == (N_SAMPLES, 0)
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["m0"].continuity_grade == "red"
    assert anchors["m1"].continuity_grade == "stable"
    grading = [n for n, _ in packets if "ratio stability" in n.message]
    assert [n.message for n in grading] == [
        "Rebuilding the batch ledger, grading the ratio stability of its batch peaks.",
        "Rebuilding the batch ledger. Graded the ratio stability of 3 pairs of "
        "batch peaks: 2 stable, 0 drift, 1 red, 0 inconclusive and 0 on too few "
        "samples. No tier moved.",
    ]
    for packet in grading:
        assert packet.type == "compute_batch_peaks"
        assert packet.status == "pending"
        assert packet.process_id == "proc-1"
        assert packet.data["_item_index"] == N_SAMPLES - 1
        assert packet.data["_total_samples"] == N_SAMPLES
    # After every sample's own two ticks.
    assert packets.index((grading[0], 1.0)) == 2 * N_SAMPLES


async def test_a_rebuild_whose_grading_fails_is_still_a_rebuild(
    async_session_factory, continuity_batch, monkeypatch
):
    async def broken(*args, **kwargs):
        raise RuntimeError("no grading today")

    monkeypatch.setattr(continuity, "run_batch_continuity", broken)
    folded, failed = await backfill_sample_batch_peaks(continuity_batch["batch_id"])
    assert (folded, failed) == (N_SAMPLES, 0)


async def test_the_route_grades_the_batch_for_an_editor(
    editor_client, guest_client, async_session_factory, continuity_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = continuity_batch["batch_id"]
    url = f"/api/batch-peaks/batch/{batch_id}/recompute-continuity"
    assert (await guest_client.post(url)).status_code == 403
    accepted = await editor_client.post(url)
    assert accepted.status_code == 202, accepted.text
    assert "please wait" in accepted.json()["message"]
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["m0"].continuity_grade == "red"
    # Not a batch run: the ledger's history is the folds' alone.
    async with async_session_factory() as session:
        actions = (
            (
                await session.execute(
                    select(BatchPeakRun.action).where(
                        BatchPeakRun.sample_batch_id == batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert actions == ["fold"]


async def test_the_route_refuses_while_a_run_rewrites_the_ledger(
    editor_client, continuity_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "recompute_batch_continuity", fake_task)
    await start_run(continuity_batch["batch_id"], ACTION_CO_VARIATION)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{continuity_batch['batch_id']}/recompute-continuity"
    )
    assert response.status_code == 409, response.text
    assert launched == []


async def test_the_route_is_behind_the_feature_flag(
    editor_client, continuity_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{continuity_batch['batch_id']}/recompute-continuity"
    )
    assert response.status_code == 403, response.text


async def test_the_database_refuses_a_grade_it_does_not_know(
    async_session_factory, continuity_batch
):
    from sqlalchemy.exc import IntegrityError

    anchors = await _anchors(async_session_factory, continuity_batch["batch_id"])
    async with async_session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(BatchPeak)
                .where(BatchPeak.batch_peak_id == anchors["m0"].batch_peak_id)
                .values(continuity_grade="green")
            )
        await session.rollback()
    async with async_session_factory() as session:
        assert (
            await session.execute(
                select(func.count()).where(BatchPeak.continuity_grade == "green")
            )
        ).scalar_one() == 0


async def test_a_fold_hands_the_pass_the_time_it_committed(
    async_session_factory, continuity_batch, monkeypatch
):
    batch_id, samples = continuity_batch["batch_id"], continuity_batch["samples"]
    monkeypatch.setattr(batch_peaks, "CONTINUITY_ON_FOLD", True)
    calls = []

    async def recorder(sample_batch_id, **kwargs):
        calls.append((sample_batch_id, kwargs))
        return {"skipped": SKIPPED_CURRENT}

    monkeypatch.setattr(continuity, "run_batch_continuity", recorder)
    before = datetime.now(timezone.utc)
    await fold_sample_into_batch_peaks(samples[0])
    after = datetime.now(timezone.utc)
    [(graded, kwargs)] = calls
    assert graded == batch_id
    assert before <= kwargs["triggered_at"] <= after


async def test_a_rebuild_grades_once_whatever_the_fold_trigger_says(
    async_session_factory, continuity_batch, monkeypatch
):
    monkeypatch.setattr(batch_peaks, "CONTINUITY_ON_FOLD", True)
    calls = []

    async def recorder(sample_batch_id, **kwargs):
        calls.append(kwargs)
        return {"pairs": 0, "pairs_graded": 0}

    monkeypatch.setattr(continuity, "run_batch_continuity", recorder)
    await backfill_sample_batch_peaks(continuity_batch["batch_id"])
    # The rebuild's folds defer their consensus, and grade nothing until the
    # last one is in: one pass, asked for outright.
    assert calls == [{}]
