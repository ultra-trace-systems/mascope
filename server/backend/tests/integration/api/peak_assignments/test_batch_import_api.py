"""An external engine's batch-level result landing on the batch ledger as a
batch run.

The batch is two samples folded without runs, every peak unassigned, as the
untargeted-search suite seeds it; the seeded scorer is stubbed so a measurement
is a canned pairing rather than a spectrum. What is real: the matching, the
writes onto the members and the registry, the consensus, and the run
bookkeeping around them.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments import (
    batch_import,
    batch_untargeted,
)
from mascope_backend.api.new.peak_assignments.batch_import import (
    REASON_CURATED,
    REASON_ISOTOPOLOGUE,
    REASON_NO_ANCHOR,
    import_outcome,
    perform_batch_import,
    run_batch_import,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_M0,
    role_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    get_batch_peak_ledger,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_IMPORT,
    STATUS_COMPLETED,
    start_run,
)
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.api.new.peak_assignments.schemas import BatchImportRow
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    BatchPeakRunAnchor,
    IonizationMechanism,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

# Two unassigned peaks per sample: the M0 at 181 and its M+1 at 182, plus a
# 250 nothing claims.
_PEAKS = {
    "S1": [("p1", 181.0707, 9000.0), ("p2", 182.0741, 700.0), ("p3", 250.1, 300.0)],
    "S2": [("p1", 181.0707, 4000.0), ("p2", 182.0741, 300.0), ("p3", 250.1, 200.0)],
}
POLARITY = {"mech-h": "+"}


def _unassigned_rows(sample_item_id, peaks):
    return [
        SimpleNamespace(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id=pid,
            sample_peak_mz=mz,
            sample_peak_intensity=intensity,
            role="unassigned",
            assigned_formula=None,
            ion_formula=None,
            ionization_mechanism_id=None,
            source=None,
            tier="unassigned",
            fit_score=None,
            mz_error_ppm=None,
            owner_peak_assignment_id=None,
            provenance=None,
        )
        for pid, mz, intensity in peaks
    ]


@pytest_asyncio.fixture
async def folded_batch(async_session_factory, pa_test_data):
    """A batch of its own with two samples folded without runs, every peak
    unassigned."""
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
                sample_batch_name=f"Import {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, "mech-h") is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id="mech-h",
                    ionization_mechanism_polarity="+",
                    ionization_mechanism="H+ (batch import test)",
                )
            )
        for name in _PEAKS:
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-import-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 7, 4, 12, 0, 0),
                    datetime_utc=now,
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
                    sample_item_name=f"Import {name}",
                    sample_item_type="sample",
                    polarity="+",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        await session.commit()
    for name, sample_item_id in samples.items():
        rows = _unassigned_rows(sample_item_id, _PEAKS[name])
        assert (
            await fold_sample_into_batch_peaks(
                sample_item_id, rows=rows, persisted=False
            )
            == batch_id
        )
    return {"batch_id": batch_id, "samples": samples}


@pytest.fixture
def stubbed_scorer(monkeypatch):
    """The seeded scorer replaced by a canned pairing: glucose's envelope
    reaches the 181 and the 182 of every sample with a fit of 0.81."""
    calls = []

    async def match_params(sample_item_id):
        return SimpleNamespace(isotope_abundance_threshold=0.01)

    async def seeded(sample, seeds, params):
        calls.append((sample.sample_item_id, set(seeds)))
        ion_by_seed = {("C6H12O6", "mech-h"): "ion-glc"}
        fit_by_ion = {"ion-glc": 0.81}
        errors = {
            ("ion-glc", "p1"): {"mz_error_ppm": 0.9},
            ("ion-glc", "p2"): {"mz_error_ppm": 1.1},
        }
        return ion_by_seed, fit_by_ion, errors, pd.DataFrame({"x": [1]})

    monkeypatch.setattr(batch_untargeted, "default_match_params", match_params)
    monkeypatch.setattr(batch_untargeted, "score_seeds", seeded)
    return calls


def _rows(*specs):
    return [
        BatchImportRow(
            mz=mz,
            formula=formula,
            ion_formula="C6H13O6+",
            ionization_mechanism_id="mech-h",
        )
        for mz, formula in specs
    ]


async def _anchors_by_mz(session_factory, batch_id):
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
    return {round(anchor.mz): anchor for anchor in anchors}


async def _members(session_factory, sample_item_id):
    async with session_factory() as session:
        members = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.sample_item_id == sample_item_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return {member.sample_peak_id: member for member in members}


async def test_the_engines_identity_lands_on_every_member_of_the_matched_anchor(
    async_session_factory, folded_batch, stubbed_scorer
):
    batch_id = folded_batch["batch_id"]
    # 181.0709 is 1.1 ppm off the anchor: lands. 300.5 is nowhere: skipped.
    rows = _rows((181.0709, "C6H12O6"), (300.5, "C10H20O10"))

    counts = await run_batch_import(batch_id, "peaky", 5.0, rows, POLARITY)

    assert counts["anchors_matched"] == 1
    assert counts["rows_skipped_by_reason"] == {REASON_NO_ANCHOR: 1}
    assert counts["members_measured"] == 2
    assert counts["samples_rescored"] == 2
    # Both samples were measured against the engine's seed, not its score.
    assert sorted(seeds for _, seeds in stubbed_scorer) == [
        {("C6H12O6", "mech-h")},
        {("C6H12O6", "mech-h")},
    ]

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    glucose = anchors[181]
    assert glucose.consensus_formula == "C6H12O6"
    assert glucose.ionization_mechanism_id == "mech-h"
    entry = next(c for c in glucose.candidates if c["formula"] == "C6H12O6")
    assert entry["source"] == "peaky"
    assert entry["ion_formula"] == "C6H13O6+"
    # The 182 was paired by the envelope too, but no row claimed its anchor,
    # so it is left as it was; so is the 250.
    assert anchors[182].consensus_formula is None
    assert anchors[250].consensus_formula is None

    for sample_item_id in folded_batch["samples"].values():
        members = await _members(async_session_factory, sample_item_id)
        assert members["p1"].candidate == glucose.candidates.index(entry)
        # The column is a float32: the fit comes back as the nearest one.
        assert members["p1"].fit_score == pytest.approx(0.81, abs=1e-6)
        assert members["p1"].role == role_code(ROLE_M0)
        assert members["p1"].owner_batch_peak_id is None
        assert members["p2"].candidate is None


async def test_a_curated_anchor_and_an_isotopologue_anchor_are_left_alone(
    async_session_factory, folded_batch, stubbed_scorer
):
    batch_id = folded_batch["batch_id"]
    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    async with async_session_factory() as session:
        pinned = await session.get(BatchPeak, anchors[181].batch_peak_id)
        pinned.provenance = {
            "manual": {"action": "promote_identity", "formula": "C6H12O6"}
        }
        child = await session.get(BatchPeak, anchors[182].batch_peak_id)
        child.isotopologue_of = anchors[181].batch_peak_id
        await session.commit()

    counts = await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        _rows((181.0707, "C7H14O7"), (182.0741, "C7H14O7")),
        POLARITY,
    )

    assert counts["anchors_matched"] == 0
    assert counts["rows_skipped_by_reason"] == {
        REASON_CURATED: 1,
        REASON_ISOTOPOLOGUE: 1,
    }
    assert counts["members_measured"] == 0
    assert stubbed_scorer == []
    for sample_item_id in folded_batch["samples"].values():
        members = await _members(async_session_factory, sample_item_id)
        assert members["p1"].candidate is None
        assert members["p2"].candidate is None


async def test_the_import_is_a_run_and_the_ledger_before_it_stays_readable(
    async_session_factory, folded_batch, stubbed_scorer
):
    batch_id = folded_batch["batch_id"]
    run_id = await start_run(
        batch_id, ACTION_IMPORT, engine="peaky", engine_version="0.7.0"
    )

    outcome = await perform_batch_import(
        batch_id, run_id, "peaky", 5.0, _rows((181.0707, "C6H12O6")), POLARITY
    )

    assert outcome["status"] == "success"
    assert "1 of 1 row landed" in outcome["message"]
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
        by_action = {run.action: run for run in runs}
        imported = by_action[ACTION_IMPORT]
        assert imported.status == STATUS_COMPLETED
        assert imported.is_current == 1
        assert imported.engine == "peaky"
        assert imported.summary["anchors_matched"] == 1
        fold = by_action["fold"]
        assert fold.is_current == 0
        snapshot = (
            (
                await session.execute(
                    select(BatchPeakRunAnchor).where(
                        BatchPeakRunAnchor.batch_peak_run_id == fold.batch_peak_run_id
                    )
                )
            )
            .scalars()
            .all()
        )
    # The fold run's snapshot holds the ledger as it was: nothing assigned.
    assert len(snapshot) == 3
    assert {row.consensus_formula for row in snapshot} == {None}
    before = await get_batch_peak_ledger(
        batch_id, min_n_present=1, batch_peak_run_id=fold.batch_peak_run_id
    )
    assert {row["consensus_formula"] for row in before["data"]} == {None}
    live = await get_batch_peak_ledger(batch_id, min_n_present=1)
    assert "C6H12O6" in {row["consensus_formula"] for row in live["data"]}


async def test_the_route_opens_a_run_and_refuses_what_it_cannot_honour(
    editor_client, guest_client, folded_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = folded_batch["batch_id"]
    url = f"/api/batch-peaks/batch/{batch_id}/runs/import"
    body = {
        "engine": "peaky",
        "engine_version": "0.7.0",
        "mz_tolerance_ppm": 4.0,
        "config": {"reagent": "acetate"},
        "rows": [
            {
                "mz": 181.0707,
                "formula": "C6H12O6",
                "ion_formula": "C6H13O6+",
                "ionization_mechanism_id": "mech-h",
                "ion_score": 0.9,
            }
        ],
    }

    launched = {}

    async def fake_task(**kwargs):
        launched.update(kwargs)
        return {"status": "success", "message": "", "data": {}}

    from mascope_backend.api.new.peak_assignments import batch_peaks_routes

    monkeypatch.setattr(batch_peaks_routes, "import_batch_run", fake_task)

    assert (await guest_client.post(url, json=body)).status_code == 403

    reserved = await editor_client.post(url, json={**body, "engine": "mascope"})
    assert reserved.status_code == 422
    assert "reserved" in reserved.text

    unknown = await editor_client.post(
        url,
        json={**body, "rows": [{**body["rows"][0], "ionization_mechanism_id": "nope"}]},
    )
    assert unknown.status_code == 422
    assert "'nope'" in unknown.text

    accepted = await editor_client.post(url, json=body)
    assert accepted.status_code == 202, accepted.text
    assert accepted.headers["process-id"]
    record = accepted.json()["data"][0]
    assert record["action"] == ACTION_IMPORT
    assert record["status"] == "running"
    assert launched["batch_peak_run_id"] == record["batch_peak_run_id"]
    assert launched["engine"] == "peaky"
    assert launched["tolerance_ppm"] == 4.0
    assert launched["mechanism_polarity"] == {"mech-h": "+"}
    assert [row.formula for row in launched["rows"]] == ["C6H12O6"]
    assert launched["independent_transaction"] is True

    runs = (await guest_client.get(f"/api/batch-peaks/batch/{batch_id}/runs")).json()
    newest = runs["data"][0]
    assert newest["batch_peak_run_id"] == record["batch_peak_run_id"]
    assert newest["engine"] == "peaky"
    assert newest["engine_version"] == "0.7.0"
    assert newest["config"]["client"] == {"reagent": "acetate"}
    assert newest["config"]["mz_tolerance_ppm"] == 4.0

    # The stub never closed the run, so a second import finds it in flight.
    again = await editor_client.post(url, json=body)
    assert again.status_code == 409


async def test_a_sample_that_cannot_be_measured_is_skipped_and_the_rest_are_measured(
    async_session_factory, folded_batch, stubbed_scorer, monkeypatch
):
    """One sample's measurement raising is counted and skipped: the rows have
    already matched their anchors, the other samples are measured against them,
    and the deferred consensus pass runs over the matched anchors."""
    batch_id, samples = folded_batch["batch_id"], folded_batch["samples"]
    scorer = batch_untargeted.score_seeds  # the fixture's canned pairing

    async def failing(sample, seeds, params):
        if sample.sample_item_id == samples["S2"]:
            raise FileNotFoundError("S2's peak file has moved")
        return await scorer(sample, seeds, params)

    recomputed = []
    recompute = batch_import.recompute_batch_consensus

    async def recording(sample_batch_id, batch_peak_ids):
        recomputed.append(set(batch_peak_ids))
        return await recompute(sample_batch_id, batch_peak_ids)

    monkeypatch.setattr(batch_untargeted, "score_seeds", failing)
    monkeypatch.setattr(batch_import, "recompute_batch_consensus", recording)

    counts = await run_batch_import(
        batch_id, "peaky", 5.0, _rows((181.0709, "C6H12O6")), POLARITY
    )

    assert counts["anchors_matched"] == 1
    assert counts["samples_rescored"] == 1
    assert counts["samples_failed"] == 1
    assert counts["members_measured"] == 1
    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    glucose = anchors[181]
    entry = next(c for c in glucose.candidates if c["formula"] == "C6H12O6")
    s1 = await _members(async_session_factory, samples["S1"])
    assert s1["p1"].candidate == glucose.candidates.index(entry)
    s2 = await _members(async_session_factory, samples["S2"])
    assert s2["p1"].candidate is None
    assert recomputed == [{glucose.batch_peak_id}]
    assert glucose.consensus_formula == "C6H12O6"
    outcome = import_outcome(counts, batch_id, "peaky")
    assert outcome["status"] == "partial"
    assert "1 sample could not be measured and was skipped." in outcome["message"]


@pytest.mark.parametrize("recompute_fails", [False, True])
async def test_a_cancellation_mid_import_still_recomputes_and_stays_a_cancellation(
    async_session_factory, folded_batch, stubbed_scorer, monkeypatch, recompute_fails
):
    """Cancellation walks past the per-sample arm, being a BaseException. The
    samples measured before it have committed their members while the anchors
    still describe the members they had, so the deferred pass runs on the way
    out - and a failure there is logged rather than allowed to replace the
    cancellation, which has to reach the caller as what it is."""
    batch_id, samples = folded_batch["batch_id"], folded_batch["samples"]
    scorer = batch_untargeted.score_seeds

    async def cancelled(sample, seeds, params):
        if sample.sample_item_id == samples["S2"]:
            raise asyncio.CancelledError()
        return await scorer(sample, seeds, params)

    recomputed = []
    recompute = batch_import.recompute_batch_consensus

    async def recording(sample_batch_id, batch_peak_ids):
        recomputed.append(set(batch_peak_ids))
        if recompute_fails:
            raise RuntimeError("the database went away")
        return await recompute(sample_batch_id, batch_peak_ids)

    monkeypatch.setattr(batch_untargeted, "score_seeds", cancelled)
    monkeypatch.setattr(batch_import, "recompute_batch_consensus", recording)

    with pytest.raises(asyncio.CancelledError):
        await run_batch_import(
            batch_id, "peaky", 5.0, _rows((181.0709, "C6H12O6")), POLARITY
        )

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    assert recomputed == [{anchors[181].batch_peak_id}]
