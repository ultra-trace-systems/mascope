"""Integration tests for the batch's co-variation reading (step 4.2).

Seeds a batch of twelve samples folded into the ledger: twelve compounds seen
through one channel, which carry the batch's common mode and nothing else, a
reagent ion, and five compounds seen through two channels - one whose two
traces follow each other, one whose second channel runs against its first,
one with a flat second channel, one whose second channel is in six samples
only, and one whose two traces share nothing but the common mode. One sample
is folded from a run of its own, so its rows are a run's rather than derived
from the ledger. The traces' arithmetic is the library's
(``test_coherence.py``); what is proven here is the batch-level contract: every
pair is read and recorded on both anchors, the run is a batch run with its
summary, the consensus keeps a record while it names the reading and drops
it once it moves, a new reading replaces the last, the ledger and an earlier
run's snapshot serve it, the inspector's detail read shows it on a sample's
row of the same reading, and no tier moves.
See ``batch_coherence.py``.
"""

import math
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select, update

from mascope_backend.api.new.peak_assignments import batch_peaks_routes
from mascope_backend.api.new.peak_assignments.batch_coherence import (
    run_batch_co_variation,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import CO_VARIATION_KEY
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
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
    BatchPeakRun,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.coherence import (
    REASON_FEW_SPECTRA,
    REASON_FLAT,
    VERDICT_CO_VARYING,
    VERDICT_NOT_CO_VARYING,
    VERDICT_UNTESTABLE,
)


pytestmark = pytest.mark.asyncio

#: Two real adducts no other suite names: the mechanism table's notations are
#: unique, and the integration database is shared by the whole run.
MECH_A, NOTATION_A = "mech-coh-h2po4", "[M+H2PO4]-"
MECH_B, NOTATION_B = "mech-coh-clo4", "[M+ClO4]-"

N_SAMPLES = 12
#: The sample folded from a run of its own.
RUN_SAMPLE = "S00"

#: The batch's common mode: every trace follows it.
COMMON = [0.6 * math.sin(2 * math.pi * i / 11.0) for i in range(N_SAMPLES)]
#: What one compound does on its own.
OWN = [0.4 * math.sin(2 * math.pi * i / 4.0 + 0.5) for i in range(N_SAMPLES)]

#: Compounds seen through one channel: the common mode's floor. Enough of
#: them that every spectrum's median sits among them.
BACKGROUND = [
    "C5H8O3",
    "C6H10O3",
    "C7H12O3",
    "C8H14O3",
    "C9H16O3",
    "C5H6O5",
    "C6H8O5",
    "C7H10O5",
    "C8H12O5",
    "C9H14O5",
    "C10H16O5",
    "C11H18O5",
]


def _noise(seed, scale):
    return list(np.random.default_rng(seed).normal(0.0, scale, N_SAMPLES))


#: Peak id -> (m/z, formula, mechanism, log10 trace with None for absent, tier).
def _peaks():
    peaks = {}
    for i, formula in enumerate(BACKGROUND):
        noise = _noise(100 + i, 0.01)
        peaks[f"bg-{i}"] = (
            150.0 + 10 * i,
            formula,
            MECH_A,
            [4.0 + c + n for c, n in zip(COMMON, noise)],
            "assigned",
        )
    n1, n2 = _noise(1, 0.02), _noise(2, 0.02)
    # Follows its own: both channels share OWN.
    peaks["p-a"] = (
        301.0,
        "C3H4O4",
        MECH_A,
        [5 + c + o + n for c, o, n in zip(COMMON, OWN, n1)],
        "assigned",
    )
    peaks["p-b"] = (
        311.0,
        "C3H4O4",
        MECH_B,
        [4 + c + o + n for c, o, n in zip(COMMON, OWN, n2)],
        "assigned",
    )
    # The second channel runs against the first, as a radical anion beside its
    # deprotonated ion does when the source state moves.
    peaks["q-a"] = (
        321.0,
        "C4H6O4",
        MECH_A,
        [5 + c + o for c, o in zip(COMMON, OWN)],
        "assigned",
    )
    peaks["q-b"] = (
        331.0,
        "C4H6O4",
        MECH_B,
        [4 + c - o for c, o in zip(COMMON, OWN)],
        "candidate",
    )
    # A flat second channel: it follows the common mode and nothing else.
    peaks["f-a"] = (
        341.0,
        "C5H8O4",
        MECH_A,
        [5 + c + o for c, o in zip(COMMON, OWN)],
        "assigned",
    )
    peaks["f-b"] = (351.0, "C5H8O4", MECH_B, [4 + c for c in COMMON], "assigned")
    # A second channel in six samples only.
    peaks["d-a"] = (
        361.0,
        "C6H10O4",
        MECH_A,
        [5 + c + o for c, o in zip(COMMON, OWN)],
        "assigned",
    )
    peaks["d-b"] = (
        371.0,
        "C6H10O4",
        MECH_B,
        [4 + c + o if i < 6 else None for i, (c, o) in enumerate(zip(COMMON, OWN))],
        "assigned",
    )
    # Two traces that share the common mode and nothing else: a correlation
    # over raw traces reads them as co-varying.
    u1, u2 = _noise(3, 0.15), _noise(4, 0.15)
    peaks["u-a"] = (
        381.0,
        "C7H12O4",
        MECH_A,
        [5 + c + n for c, n in zip(COMMON, u1)],
        "assigned",
    )
    peaks["u-b"] = (
        391.0,
        "C7H12O4",
        MECH_B,
        [4 + c + n for c, n in zip(COMMON, u2)],
        "below_assignability",
    )
    return peaks


#: The reagent ion's trace.
REAGENT = [6.0 + 0.05 * math.cos(i) for i in range(N_SAMPLES)]


def _rows(sample_item_id, index, *, run_id=None, overrides=None):
    rows = []
    for peak_id, (mz, formula, mechanism, values, tier) in _peaks().items():
        value = values[index]
        if value is None:
            continue
        formula, mechanism = (overrides or {}).get(peak_id, (formula, mechanism))
        rows.append(
            dict(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=run_id or fold_run_id(sample_item_id),
                sample_item_id=sample_item_id,
                sample_peak_id=peak_id,
                sample_peak_mz=mz,
                sample_peak_intensity=10.0**value,
                role="M0",
                assigned_formula=formula,
                ion_formula=f"{formula}-ion",
                ionization_mechanism_id=mechanism,
                source="untargeted",
                tier=tier,
                fit_score=0.9,
                mz_error_ppm=0.1,
                owner_peak_assignment_id=None,
                provenance=None,
            )
        )
    m0 = next(r for r in rows if r["sample_peak_id"] == "p-a")
    # An isotope line of the first compound: its parent's trace again.
    rows.append(
        {
            **m0,
            "peak_assignment_id": gen_id(32),
            "sample_peak_id": "p-a-13c",
            "sample_peak_mz": 302.0034,
            "sample_peak_intensity": m0["sample_peak_intensity"] * 0.04,
            "role": "iso_child",
            "ion_formula": "[13C]C2H4O4-ion",
            "owner_peak_assignment_id": m0["peak_assignment_id"],
        }
    )
    rows.append(
        dict(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=run_id or fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id="reagent",
            sample_peak_mz=124.9875,
            sample_peak_intensity=10.0 ** REAGENT[index],
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


async def _seed(async_session_factory, pa_test_data):
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    samples = {}
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
                sample_batch_name=f"Coherence {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        for mechanism, notation in ((MECH_A, NOTATION_A), (MECH_B, NOTATION_B)):
            if await session.get(IonizationMechanism, mechanism) is None:
                session.add(
                    IonizationMechanism(
                        ionization_mechanism_id=mechanism,
                        ionization_mechanism_polarity="-",
                        ionization_mechanism=notation,
                    )
                )
        for i in range(N_SAMPLES):
            name = f"S{i:02d}"
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-coherence-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 11, 8, 0, i, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=f"Coherence {name}",
                    sample_item_type="sample",
                    polarity="-",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        # The one sample whose rows are a run's own.
        run_id = gen_id()
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=samples[RUN_SAMPLE],
                engine_version="0.1.0-test",
                status="completed",
                config={},
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        run_rows = {}
        for row in _rows(samples[RUN_SAMPLE], 0, run_id=run_id):
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
                rows=[SimpleNamespace(**r) for r in _rows(samples[name], i)],
                persisted=False,
            )
        assert folded == batch_id
    return {"batch_id": batch_id, "samples": samples, "run_rows": run_rows}


@pytest_asyncio.fixture
async def coherence_batch(async_session_factory, pa_test_data):
    return await _seed(async_session_factory, pa_test_data)


async def _anchors(session_factory, batch_id):
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
    mzs.update({"p-a-13c": 302.0034, "reagent": 124.9875})
    return {peak_id: by_mz[round(mz, 2)] for peak_id, mz in mzs.items()}


def _record(anchor):
    return (anchor.provenance or {}).get(CO_VARIATION_KEY)


async def test_every_pair_is_read_and_recorded_on_both_anchors(
    async_session_factory, coherence_batch
):
    batch_id = coherence_batch["batch_id"]
    before = await _anchors(async_session_factory, batch_id)
    summary = await run_batch_co_variation(batch_id, batch_peak_run_id="run-cov")
    anchors = await _anchors(async_session_factory, batch_id)

    p_a, p_b = _record(anchors["p-a"]), _record(anchors["p-b"])
    assert p_a["verdict"] == VERDICT_CO_VARYING
    assert p_a["r"] > 0.9
    assert p_a["n_spectra"] == N_SAMPLES
    assert p_a["formula"] == "C3H4O4"
    assert p_a["ionization_mechanism_id"] == MECH_A
    assert p_a["channel"] == NOTATION_A
    assert p_a["partner_batch_peak_id"] == anchors["p-b"].batch_peak_id
    assert p_a["partner_channel"] == NOTATION_B
    assert p_a["normalised"] is True
    assert p_a["batch_peak_run_id"] == "run-cov"
    assert p_b["partner_batch_peak_id"] == anchors["p-a"].batch_peak_id
    assert p_b["r"] == p_a["r"]

    # Anti-correlation is read as it is, at any tier.
    q_b = _record(anchors["q-b"])
    assert q_b["verdict"] == VERDICT_NOT_CO_VARYING
    assert q_b["r"] < -0.9
    assert _record(anchors["f-a"])["verdict"] == VERDICT_UNTESTABLE
    assert _record(anchors["f-a"])["reason"] == REASON_FLAT
    assert _record(anchors["d-b"])["reason"] == REASON_FEW_SPECTRA
    assert _record(anchors["d-b"])["n_spectra"] == 6
    # Without the common mode taken out these two would read as co-varying.
    raw = np.corrcoef(_peaks()["u-a"][3], _peaks()["u-b"][3])[0, 1]
    assert raw > 0.8
    u = _record(anchors["u-b"])
    assert u["verdict"] == VERDICT_NOT_CO_VARYING
    assert abs(u["r"]) < 0.6

    # A compound seen through one channel, an isotope line and the reagent's
    # ion carry nothing.
    for peak_id in ("bg-0", "bg-11", "p-a-13c", "reagent"):
        assert _record(anchors[peak_id]) is None

    assert summary["neutrals_read"] == 5
    assert summary["pairs"] == 5
    assert summary["co_varying"] == 1
    assert summary["not_co_varying"] == 2
    assert summary["untestable_flat"] == 1
    assert summary["untestable_few_spectra"] == 1
    assert summary["anchors_recorded"] == 10
    [partition] = summary["partitions"]
    assert partition["instrument_type"] == "orbi"
    assert partition["spectra"] == N_SAMPLES
    assert partition["spectra_read"] == N_SAMPLES
    assert partition["reagent_ions"] == 1
    assert partition["normalised"] is True
    # Every monoisotopic, unclaimed trace: twelve compounds and ten channels.
    assert partition["traces"] == 22

    # Nothing but the records moved.
    for peak_id, anchor in anchors.items():
        assert anchor.consensus_tier == before[peak_id].consensus_tier
        assert anchor.consensus_formula == before[peak_id].consensus_formula


async def test_the_consensus_keeps_a_record_while_it_names_the_reading(
    async_session_factory, coherence_batch
):
    batch_id, samples = coherence_batch["batch_id"], coherence_batch["samples"]
    await run_batch_co_variation(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    await recompute_batch_consensus(
        batch_id, {anchor.batch_peak_id for anchor in anchors.values()}
    )
    kept = await _anchors(async_session_factory, batch_id)
    assert _record(kept["p-a"]) == _record(anchors["p-a"])

    # Most of the batch now reads the first compound's peak through the
    # other channel: the reading the record was read for is gone.
    for i in range(1, 8):
        name = f"S{i:02d}"
        await fold_sample_into_batch_peaks(
            samples[name],
            rows=[
                SimpleNamespace(**r)
                for r in _rows(samples[name], i, overrides={"p-a": ("C3H4O4", MECH_B)})
            ],
            persisted=False,
        )
    moved = await _anchors(async_session_factory, batch_id)
    assert moved["p-a"].ionization_mechanism_id == MECH_B
    assert _record(moved["p-a"]) is None
    # Its partner's record stands until the next reading.
    assert _record(moved["p-b"]) == _record(anchors["p-b"])


async def test_a_new_reading_replaces_the_last(async_session_factory, coherence_batch):
    batch_id = coherence_batch["batch_id"]
    anchors = await _anchors(async_session_factory, batch_id)
    stale = {
        "formula": anchors["bg-0"].consensus_formula,
        "ionization_mechanism_id": MECH_A,
        "verdict": VERDICT_CO_VARYING,
    }
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == anchors["bg-0"].batch_peak_id)
            .values(
                provenance={
                    **(anchors["bg-0"].provenance or {}),
                    CO_VARIATION_KEY: stale,
                }
            )
        )
        await session.commit()
    await run_batch_co_variation(batch_id, batch_peak_run_id="run-2")
    anchors = await _anchors(async_session_factory, batch_id)
    assert _record(anchors["bg-0"]) is None
    assert _record(anchors["p-a"])["batch_peak_run_id"] == "run-2"


async def test_the_route_runs_it_as_a_batch_run(
    editor_client, guest_client, async_session_factory, coherence_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = coherence_batch["batch_id"]
    refused = await guest_client.post(
        f"/api/batch-peaks/batch/{batch_id}/read-co-variation"
    )
    assert refused.status_code == 403

    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/read-co-variation"
    )
    assert accepted.status_code == 202, accepted.text
    assert "please wait" in accepted.json()["message"]
    async with async_session_factory() as session:
        runs = (
            (
                await session.execute(
                    select(BatchPeakRun).where(BatchPeakRun.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
    [run] = [run for run in runs if run.action == ACTION_CO_VARIATION]
    assert run.status == "completed"
    assert run.is_current == 1
    assert run.config == {"co_varying_r": 0.6, "min_spectra": 10, "flat_spread": 0.02}
    assert run.summary["pairs"] == 5
    assert run.summary["co_varying"] == 1
    anchors = await _anchors(async_session_factory, batch_id)
    assert _record(anchors["p-a"])["batch_peak_run_id"] == run.batch_peak_run_id


async def test_the_route_refuses_while_another_run_rewrites_the_ledger(
    editor_client, coherence_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "read_batch_co_variation", fake_task)
    await start_run(coherence_batch["batch_id"], ACTION_CO_VARIATION)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{coherence_batch['batch_id']}/read-co-variation"
    )
    assert response.status_code == 409, response.text
    assert launched == []


async def test_the_ledger_serves_the_reading_and_an_earlier_run_keeps_it(
    guest_client, async_session_factory, coherence_batch
):
    batch_id = coherence_batch["batch_id"]
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
    p_a = live[anchors["p-a"].batch_peak_id]["co_variation"]
    assert p_a["verdict"] == VERDICT_CO_VARYING
    assert p_a["batch_peak_run_id"] == run_id
    assert live[anchors["bg-0"].batch_peak_id]["co_variation"] is None

    # A later run snapshots this one; read back, the snapshot still says it.
    later = await start_run(batch_id, ACTION_CO_VARIATION)
    await complete_run(batch_id, later)
    earlier = await ledger(batch_peak_run_id=run_id)
    assert earlier[anchors["p-a"].batch_peak_id]["co_variation"] == p_a
    assert earlier[anchors["bg-0"].batch_peak_id]["co_variation"] is None


async def test_the_inspector_reads_it_on_a_sample_s_row_of_the_same_reading(
    guest_client, async_session_factory, coherence_batch
):
    batch_id, samples = coherence_batch["batch_id"], coherence_batch["samples"]
    await run_batch_co_variation(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    record = _record(anchors["p-a"])

    async def detail(sample_item_id, peak_assignment_id):
        response = await guest_client.get(
            f"/api/peak-assignments/sample/{sample_item_id}/assignment/"
            f"{peak_assignment_id}"
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["data"]
        return row

    # A run's own row reads the record of the batch peak its peak is in.
    run_rows = coherence_batch["run_rows"]
    own = await detail(samples[RUN_SAMPLE], run_rows["p-a"])
    assert own["batch_co_variation"] == record
    # An isotope line, and a compound the batch sees through one channel, show
    # none.
    assert (await detail(samples[RUN_SAMPLE], run_rows["p-a-13c"]))[
        "batch_co_variation"
    ] is None
    assert (await detail(samples[RUN_SAMPLE], run_rows["bg-0"]))[
        "batch_co_variation"
    ] is None
    # A row the batch ledger serves reads it off its anchor.
    derived = await detail(
        samples["S03"], fold_assignment_id(anchors["p-a"].batch_peak_id)
    )
    assert derived["batch_co_variation"] == record

    # Where the run's row reads its peak another way, the record is not its.
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(PeakAssignment.peak_assignment_id == run_rows["p-a"])
            .values(ionization_mechanism_id=MECH_B)
        )
        await session.commit()
    assert (await detail(samples[RUN_SAMPLE], run_rows["p-a"]))[
        "batch_co_variation"
    ] is None
