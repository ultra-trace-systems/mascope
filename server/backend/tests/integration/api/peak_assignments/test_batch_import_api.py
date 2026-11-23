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
from pydantic import ValidationError
from sqlalchemy import select, update
from stage_stubs import (  # the tests root, on sys.path
    frame,
    mechanism,
    stub_sample_reads,
    stub_seeded_scorer,
)
from test_utils import captured_logs

from mascope_backend.api.new.peak_assignments import (
    batch_import,
    batch_peaks,
    batch_staleness,
    pipeline,
)
from mascope_backend.api.new.peak_assignments.batch_fold import rebuild_batch_ledger
from mascope_backend.api.new.peak_assignments.batch_import import (
    REASON_CURATED,
    REASON_ISOTOPOLOGUE,
    REASON_NO_ANCHOR,
    import_outcome,
    perform_batch_import,
    run_batch_import,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import ROLE_M0, role_code
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
from mascope_backend.api.new.peak_assignments.batch_stages import (
    begin_stage,
    finish_stage,
    stage_standing,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import BatchStage
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
    return await _fold_batch(async_session_factory, pa_test_data)


@pytest_asyncio.fixture
async def mixed_batch(async_session_factory, pa_test_data, monkeypatch):
    """The batch of ``folded_batch`` with S2 a TOF file: each instrument type
    folds into anchors of its own, so every m/z has an anchor in each, with
    one member apiece."""
    # The fold classifies a file by its name.
    monkeypatch.setattr(
        batch_peaks,
        "get_instrument_type",
        lambda filename: "tof" if "-S2-" in filename else "orbi",
    )
    return await _fold_batch(async_session_factory, pa_test_data)


async def _fold_batch(async_session_factory, pa_test_data):
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
        return SimpleNamespace(isotope_abundance_threshold=0.01, mz_tolerance=5.0)

    async def seeded(sample, seeds, params, *, config=None):
        calls.append((sample.sample_item_id, set(seeds)))
        ion_by_seed = {("C6H12O6", "mech-h"): "ion-glc"}
        fit_by_ion = {"ion-glc": 0.81}
        errors = {
            ("ion-glc", "p1"): {"mz_error_ppm": 0.9},
            ("ion-glc", "p2"): {"mz_error_ppm": 1.1},
        }
        return ion_by_seed, fit_by_ion, errors, pd.DataFrame({"x": [1]})

    _stub_reads(monkeypatch, match_params)
    stub_seeded_scorer(monkeypatch, seeded, _peaks_of)
    return calls


def _peaks_of(sample_item_id):
    """Each sample's peaks: both hold the same three m/z."""
    return frame(_PEAKS["S1"])


def _stub_reads(monkeypatch, match_params):
    """What the stage list reads of each sample (``stage_stubs``)."""
    stub_sample_reads(monkeypatch, _peaks_of, [mechanism("[M+H]+", "mech-h")])
    monkeypatch.setattr(pipeline, "default_match_params", match_params)


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
    assert counts["rows_landed"] == 1
    assert counts["anchors_matched_by_instrument_type"] == {"orbi": 1}
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


async def test_a_mixed_batch_lands_a_row_on_the_anchor_of_each_instrument_type(
    async_session_factory, mixed_batch, stubbed_scorer
):
    """One species has an anchor per instrument type. The row lands on both,
    each anchor measured on its own member, the engine's verdict goes on both,
    and the summary says what each instrument type took."""
    batch_id = mixed_batch["batch_id"]
    rows = [
        BatchImportRow(
            mz=181.0709,
            formula="C6H12O6",
            ion_formula="C6H13O6+",
            ionization_mechanism_id="mech-h",
            engine_tier="candidate",
            engine_claim="level 3",
        ),
        # Within the tolerance of nothing: skipped once, not once per type.
        BatchImportRow(mz=300.5, formula="C10H20O10", ionization_mechanism_id="mech-h"),
    ]

    counts = await run_batch_import(batch_id, "peaky", 5.0, rows, POLARITY)

    assert counts["anchors_matched"] == 2
    assert counts["rows_landed"] == 1
    assert counts["anchors_matched_by_instrument_type"] == {"orbi": 1, "tof": 1}
    assert (
        sum(counts["anchors_matched_by_instrument_type"].values())
        == (counts["anchors_matched"])
    )
    assert counts["rows_skipped_by_reason"] == {REASON_NO_ANCHOR: 1}
    assert counts["members_measured"] == 2
    assert counts["engine_verdicts"] == 2
    assert "1 of 2 rows landed" in import_outcome(counts, batch_id, "peaky")["message"]

    async with async_session_factory() as session:
        glucose = {
            anchor.intensity_variable: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == batch_id,
                        BatchPeak.mz < 181.5,
                    )
                )
            )
            .scalars()
            .all()
        }
    assert set(glucose) == {"sum_peak_heights", "sum_peak_areas"}
    for unit, name in (("sum_peak_heights", "S1"), ("sum_peak_areas", "S2")):
        anchor = glucose[unit]
        assert anchor.consensus_formula == "C6H12O6"
        assert (anchor.engine_tier, anchor.engine_claim) == ("candidate", "level 3")
        entry = next(c for c in anchor.candidates if c["formula"] == "C6H12O6")
        assert entry["source"] == "peaky"
        # Each anchor's identity is its own member's measurement.
        members = await _members(async_session_factory, mixed_batch["samples"][name])
        assert members["p1"].batch_peak_id == anchor.batch_peak_id
        assert members["p1"].candidate == anchor.candidates.index(entry)
        assert members["p1"].fit_score == pytest.approx(0.81, abs=1e-6)


async def test_an_anchor_whose_unit_names_no_instrument_is_counted_unnamed(
    async_session_factory, folded_batch, stubbed_scorer
):
    """A batch folded before the ledger kept the instrument types apart: its
    anchors name no unit, are one partition, and import as before."""
    batch_id = folded_batch["batch_id"]
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.sample_batch_id == batch_id)
            .values(intensity_variable=None)
        )
        await session.commit()

    counts = await run_batch_import(
        batch_id, "peaky", 5.0, _rows((181.0709, "C6H12O6")), POLARITY
    )
    assert counts["anchors_matched"] == 1
    assert counts["anchors_matched_by_instrument_type"] == {"unnamed": 1}


async def _glucose_anchor_ids(session_factory, batch_id):
    async with session_factory() as session:
        return dict(
            (
                await session.execute(
                    select(BatchPeak.intensity_variable, BatchPeak.batch_peak_id).where(
                        BatchPeak.sample_batch_id == batch_id, BatchPeak.mz < 181.5
                    )
                )
            ).all()
        )


async def test_a_row_refused_by_one_instrument_types_anchor_lands_on_the_other(
    async_session_factory, mixed_batch, stubbed_scorer
):
    """A pinned Orbitrap anchor refuses the row, and the TOF anchor takes it:
    the row landed, so nothing is skipped. Refused by both, it is skipped once,
    for the human pin."""
    batch_id = mixed_batch["batch_id"]
    ids = await _glucose_anchor_ids(async_session_factory, batch_id)
    async with async_session_factory() as session:
        pinned = await session.get(BatchPeak, ids["sum_peak_heights"])
        pinned.provenance = {
            "manual": {"action": "promote_identity", "formula": "C6H12O6"}
        }
        await session.commit()

    counts = await run_batch_import(
        batch_id, "peaky", 5.0, _rows((181.0709, "C6H12O6")), POLARITY
    )
    assert counts["anchors_matched"] == 1
    assert counts["rows_landed"] == 1
    assert counts["anchors_matched_by_instrument_type"] == {"tof": 1}
    assert counts["rows_skipped_by_reason"] == {}

    async with async_session_factory() as session:
        child = await session.get(BatchPeak, ids["sum_peak_areas"])
        child.isotopologue_of = ids["sum_peak_heights"]
        await session.commit()

    counts = await run_batch_import(
        batch_id, "peaky", 5.0, _rows((181.0709, "C6H12O6")), POLARITY
    )
    assert counts["anchors_matched"] == 0
    assert counts["rows_landed"] == 0
    assert counts["rows_skipped_by_reason"] == {REASON_CURATED: 1}


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
    scorer = pipeline.score_seeds  # the fixture's canned pairing

    async def failing(sample, seeds, params, **kwargs):
        if sample.sample_item_id == samples["S2"]:
            raise FileNotFoundError("S2's peak file has moved")
        return await scorer(sample, seeds, params, **kwargs)

    recomputed = []
    recompute = batch_import.recompute_batch_consensus

    async def recording(sample_batch_id, batch_peak_ids):
        recomputed.append(set(batch_peak_ids))
        return await recompute(sample_batch_id, batch_peak_ids)

    monkeypatch.setattr(pipeline, "score_seeds", failing)
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
    scorer = pipeline.score_seeds

    async def cancelled(sample, seeds, params, **kwargs):
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

    monkeypatch.setattr(pipeline, "score_seeds", cancelled)
    monkeypatch.setattr(batch_import, "recompute_batch_consensus", recording)
    await _stages_ran(batch_id, BatchStage.SERIES)

    with pytest.raises(asyncio.CancelledError):
        await run_batch_import(
            batch_id, "peaky", 5.0, _rows((181.0709, "C6H12O6")), POLARITY
        )

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    assert recomputed == [{anchors[181].batch_peak_id}]
    # The sample measured before the cancellation committed its members: the
    # stages read them, so they are stale whichever way the recompute went.
    assert await _stale_reasons(batch_id) == {"series": "import"}


async def test_a_cancelled_import_whose_stale_mark_fails_stays_a_cancellation(
    folded_batch, stubbed_scorer, monkeypatch
):
    """The database gone as the import is cancelled: the deferred pass fails,
    and so does the stages' mark after it. The mark's failure is logged
    rather than raised, so the cancellation still reaches the caller as what
    it is, not as a database error."""
    batch_id, samples = folded_batch["batch_id"], folded_batch["samples"]
    scorer = pipeline.score_seeds

    async def cancelled(sample, seeds, params, **kwargs):
        if sample.sample_item_id == samples["S2"]:
            raise asyncio.CancelledError()
        return await scorer(sample, seeds, params)

    async def fails(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(pipeline, "score_seeds", cancelled)
    monkeypatch.setattr(batch_import, "recompute_batch_consensus", fails)
    monkeypatch.setattr(batch_staleness, "mark_every_stage_stale", fails)
    await _stages_ran(batch_id, BatchStage.SERIES)

    with captured_logs("WARNING") as logs, pytest.raises(asyncio.CancelledError):
        await run_batch_import(
            batch_id, "peaky", 5.0, _rows((181.0709, "C6H12O6")), POLARITY
        )

    assert any(
        record["message"].startswith("Could not mark the batch stages")
        and "the database went away" in record["message"]
        for record in logs
    )
    # Nothing could be written: the stage reads as it did.
    assert await _stale_reasons(batch_id) == {}


async def _stages_ran(batch_id, *stages):
    """Record stages as run and current, as a pass leaves them."""
    for stage in stages:
        _, started = await begin_stage(batch_id, stage)
        await finish_stage(batch_id, stage, started, run_id=None)


async def _stale_reasons(batch_id):
    standing = await stage_standing(batch_id)
    return {
        entry["stage"]: entry["stale_reason"]
        for entry in standing["stages"]
        if entry["state"] == "stale"
    }


async def test_an_import_leaves_every_stage_that_ran_stale(
    folded_batch, stubbed_scorer
):
    """An import measures members across the batch, past what they read:
    every stage reads them, so every stage that ran is stale, and a pass
    starts again at the first of them."""
    batch_id = folded_batch["batch_id"]
    await _stages_ran(batch_id, BatchStage.SERIES, BatchStage.BATCH_CHECKS)
    assert await _stale_reasons(batch_id) == {}

    await run_batch_import(
        batch_id, "peaky", 5.0, _rows((181.0709, "C6H12O6")), POLARITY
    )

    assert await _stale_reasons(batch_id) == {
        "series": "import",
        "batch_checks": "import",
    }


# --- The engine's own verdict --------------------------------------------------


@pytest.fixture
def two_identity_scorer(monkeypatch):
    """The canned pairing of ``stubbed_scorer`` plus a second identity whose
    envelope reaches the 250 of every sample, so two anchors can land."""

    async def match_params(sample_item_id):
        return SimpleNamespace(isotope_abundance_threshold=0.01, mz_tolerance=5.0)

    async def seeded(sample, seeds, params, *, config=None):
        ion_by_seed = {
            ("C6H12O6", "mech-h"): "ion-glc",
            ("C12H18O6", "mech-h"): "ion-b",
        }
        fit_by_ion = {"ion-glc": 0.81, "ion-b": 0.81}
        errors = {
            ("ion-glc", "p1"): {"mz_error_ppm": 0.9},
            ("ion-b", "p3"): {"mz_error_ppm": 0.4},
        }
        return ion_by_seed, fit_by_ion, errors, pd.DataFrame({"x": [1]})

    _stub_reads(monkeypatch, match_params)
    stub_seeded_scorer(monkeypatch, seeded, _peaks_of)


def _verdict_rows(*specs):
    """``(mz, formula, engine_tier, engine_claim)`` per row."""
    return [
        BatchImportRow(
            mz=mz,
            formula=formula,
            ionization_mechanism_id="mech-h",
            engine_tier=tier,
            engine_claim=claim,
        )
        for mz, formula, tier, claim in specs
    ]


async def _ledger(batch_id, **filters):
    result = await get_batch_peak_ledger(batch_id, min_n_present=1, **filters)
    return {round(row["mz"]): row for row in result["data"]}


_OTHER_TIER = {"assigned": "candidate"}


async def test_the_engines_verdict_lands_beside_the_derived_tier_and_filters(
    folded_batch, two_identity_scorer
):
    batch_id = folded_batch["batch_id"]
    # An import without verdicts first: the ledger reads as it always did, and
    # says which tier this server derives for each identity.
    plain = await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        _verdict_rows(
            (181.0707, "C6H12O6", None, None), (250.1, "C12H18O6", None, None)
        ),
        POLARITY,
    )
    assert (plain["engine_verdicts"], plain["engine_verdicts_withheld"]) == (0, 0)
    before = await _ledger(batch_id)
    assert {row["engine_tier"] for row in before.values()} == {None}
    assert {row["engine_claim"] for row in before.values()} == {None}
    derived = {mz: row["consensus_tier"] for mz, row in before.items()}
    assert derived[181] != "unassigned" and derived[250] != "unassigned"
    dissent = _OTHER_TIER.get(derived[250], "assigned")

    # The glucose verdict agrees with this server, the 250's does not.
    counts = await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        _verdict_rows(
            (181.0707, "C6H12O6", derived[181], "level 2"),
            (250.1, "C12H18O6", dissent, None),
        ),
        POLARITY,
    )
    assert (counts["engine_verdicts"], counts["engine_verdicts_withheld"]) == (2, 0)
    ledger = await _ledger(batch_id)
    assert (ledger[181]["engine_tier"], ledger[181]["engine_claim"]) == (
        derived[181],
        "level 2",
    )
    assert (ledger[250]["engine_tier"], ledger[250]["engine_claim"]) == (dissent, None)
    # The derived tier is still the ledger's tier, and nothing landed on the 182.
    assert {mz: row["consensus_tier"] for mz, row in ledger.items()} == derived
    assert ledger[182]["engine_tier"] is None

    assert set(await _ledger(batch_id, tier_disagrees=True)) == {250}
    assert set(await _ledger(batch_id, tier_disagrees=False)) == {181}
    assert set(await _ledger(batch_id, engine_tier=dissent)) == {250}

    # The next import is the ledger's view now: a verdict it does not send
    # comes off, here because it sends none at all.
    await run_batch_import(
        batch_id,
        "other-engine",
        5.0,
        _verdict_rows((181.0707, "C6H12O6", None, None)),
        POLARITY,
    )
    after = await _ledger(batch_id)
    assert {row["engine_tier"] for row in after.values()} == {None}
    assert {row["engine_claim"] for row in after.values()} == {None}


async def test_a_verdict_beside_an_identity_the_ledger_does_not_show_is_withheld(
    folded_batch, two_identity_scorer
):
    batch_id = folded_batch["batch_id"]
    # The row lands on the 250, but no envelope of its formula reaches the
    # members, so the consensus names nothing and the verdict has nothing to
    # stand beside.
    counts = await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        _verdict_rows((250.1, "C20H30O2", "assigned", "level 1")),
        POLARITY,
    )
    assert counts["anchors_matched"] == 1
    assert counts["members_measured"] == 0
    assert (counts["engine_verdicts"], counts["engine_verdicts_withheld"]) == (0, 1)
    ledger = await _ledger(batch_id)
    assert ledger[250]["consensus_formula"] is None
    assert ledger[250]["engine_tier"] is None


async def test_a_claim_alone_is_kept_and_an_import_that_lands_nothing_clears(
    folded_batch, two_identity_scorer
):
    batch_id = folded_batch["batch_id"]
    counts = await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        _verdict_rows((181.0707, "C6H12O6", None, "unique in its window")),
        POLARITY,
    )
    assert counts["engine_verdicts"] == 1
    ledger = await _ledger(batch_id)
    assert (ledger[181]["engine_tier"], ledger[181]["engine_claim"]) == (
        None,
        "unique in its window",
    )

    nothing = await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        _verdict_rows((400.0, "C20H30O2", "assigned", None)),
        POLARITY,
    )
    assert nothing["anchors_matched"] == 0
    assert (await _ledger(batch_id))[181]["engine_claim"] is None


async def test_an_imports_verdicts_stay_readable_in_its_run_snapshot(
    folded_batch, two_identity_scorer
):
    batch_id = folded_batch["batch_id"]
    run_id = await start_run(
        batch_id, ACTION_IMPORT, engine="peaky", engine_version="0.10.0"
    )
    await perform_batch_import(
        batch_id,
        run_id,
        "peaky",
        5.0,
        _verdict_rows((181.0707, "C6H12O6", "below_assignability", "level 4")),
        POLARITY,
    )
    # The next run snapshots the import's ledger as it left it, then clears it.
    next_id = await start_run(batch_id, ACTION_IMPORT, engine="other-engine")
    await perform_batch_import(
        batch_id,
        next_id,
        "other-engine",
        5.0,
        _verdict_rows((181.0707, "C6H12O6", None, None)),
        POLARITY,
    )
    assert (await _ledger(batch_id))[181]["engine_tier"] is None

    then = await _ledger(batch_id, batch_peak_run_id=run_id)
    assert (then[181]["engine_tier"], then[181]["engine_claim"]) == (
        "below_assignability",
        "level 4",
    )
    assert set(
        await _ledger(batch_id, batch_peak_run_id=run_id, tier_disagrees=True)
    ) == {181}
    assert not await _ledger(batch_id, batch_peak_run_id=run_id, tier_disagrees=False)


async def test_the_engines_level_lands_clears_and_stays_in_its_snapshot(
    folded_batch, two_identity_scorer
):
    """Step 5.8: the engine's identification level rides with its tier and
    claim - on the anchor its row landed on, off when the next import lands,
    and kept in the run's snapshot as that run left it."""
    batch_id = folded_batch["batch_id"]
    run_id = await start_run(
        batch_id, ACTION_IMPORT, engine="peaky", engine_version="0.10.0"
    )
    await perform_batch_import(
        batch_id,
        run_id,
        "peaky",
        5.0,
        [
            BatchImportRow(
                mz=181.0707,
                formula="C6H12O6",
                ionization_mechanism_id="mech-h",
                engine_level="4b",
                engine_evidence="the engine's why",
                engine_lift="the engine's lift",
            )
        ],
        POLARITY,
    )
    # A level alone is a verdict, as a claim alone is; its two lines go with it.
    landed = await _ledger(batch_id)
    assert (landed[181]["engine_level"], landed[181]["engine_tier"]) == ("4b", None)
    assert (landed[181]["engine_evidence"], landed[181]["engine_lift"]) == (
        "the engine's why",
        "the engine's lift",
    )
    assert landed[250]["engine_level"] is None

    next_id = await start_run(batch_id, ACTION_IMPORT, engine="other-engine")
    await perform_batch_import(
        batch_id,
        next_id,
        "other-engine",
        5.0,
        _verdict_rows((181.0707, "C6H12O6", None, None)),
        POLARITY,
    )

    assert (await _ledger(batch_id))[181]["engine_level"] is None
    then = (await _ledger(batch_id, batch_peak_run_id=run_id))[181]
    assert then["engine_level"] == "4b"
    assert then["engine_lift"] == "the engine's lift"
    # A snapshot keeps no link from its members to their runs' rows, so it
    # serves no member levels.
    assert (then["member_level"], then["member_levels"]) == (None, None)


async def test_the_engines_lines_alone_are_a_verdict_and_clear(
    folded_batch, two_identity_scorer
):
    """A row with only the engine's evidence or lift lands as a verdict, as a
    claim alone does, and an import that lands nothing takes it off again."""
    batch_id = folded_batch["batch_id"]
    for lines in ({"engine_evidence": "the engine's why"}, {"engine_lift": "MS/MS"}):
        counts = await run_batch_import(
            batch_id,
            "peaky",
            5.0,
            [
                BatchImportRow(
                    mz=181.0707,
                    formula="C6H12O6",
                    ionization_mechanism_id="mech-h",
                    **lines,
                )
            ],
            POLARITY,
        )
        assert counts["engine_verdicts"] == 1
        [(key, value)] = lines.items()
        landed = (await _ledger(batch_id))[181]
        assert (landed[key], landed["engine_level"]) == (value, None)

        nothing = await run_batch_import(
            batch_id,
            "peaky",
            5.0,
            _verdict_rows((400.0, "C20H30O2", "assigned", None)),
            POLARITY,
        )
        assert nothing["anchors_matched"] == 0
        assert (await _ledger(batch_id))[181][key] is None


async def test_an_engine_level_outside_the_scale_is_refused():
    with pytest.raises(ValidationError):
        BatchImportRow(mz=181.0707, formula="C6H12O6", engine_level="4c")
    assert BatchImportRow(mz=1.0, formula="C", engine_level="5a").engine_level == "5a"


async def test_a_rebuild_takes_every_verdict_off(folded_batch, two_identity_scorer):
    batch_id = folded_batch["batch_id"]
    await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        _verdict_rows((181.0707, "C6H12O6", "candidate", "level 3")),
        POLARITY,
    )
    assert (await _ledger(batch_id))[181]["engine_tier"] == "candidate"

    await rebuild_batch_ledger(batch_id)

    assert {row["engine_tier"] for row in (await _ledger(batch_id)).values()} == {None}


async def test_a_rebuild_takes_the_engines_level_off_too(
    folded_batch, two_identity_scorer
):
    batch_id = folded_batch["batch_id"]
    await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        [
            BatchImportRow(
                mz=181.0707,
                formula="C6H12O6",
                ionization_mechanism_id="mech-h",
                engine_level="4a",
            )
        ],
        POLARITY,
    )
    assert (await _ledger(batch_id))[181]["engine_level"] == "4a"

    await rebuild_batch_ledger(batch_id)

    assert {row["engine_level"] for row in (await _ledger(batch_id)).values()} == {None}


async def test_the_ledger_route_filters_on_the_engines_tier(
    guest_client, folded_batch, two_identity_scorer
):
    batch_id = folded_batch["batch_id"]
    await run_batch_import(
        batch_id,
        "peaky",
        5.0,
        _verdict_rows((181.0707, "C6H12O6", "below_assignability", "level 4")),
        POLARITY,
    )
    url = f"/api/batch-peaks/batch/{batch_id}"

    async def mzs(**params):
        response = await guest_client.get(url, params={"min_n_present": 1, **params})
        assert response.status_code == 200, response.text
        return {round(row["mz"]) for row in response.json()["data"]}

    assert await mzs() == {181, 182, 250}
    assert await mzs(tier_disagrees="true") == {181}
    assert await mzs(tier_disagrees="false") == set()
    assert await mzs(engine_tier="below_assignability") == {181}
    assert await mzs(engine_tier="assigned") == set()
