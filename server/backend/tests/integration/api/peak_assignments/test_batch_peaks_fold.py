"""Integration tests for the batch-peak fold-in controller.

Seeds a two-sample batch (a shared m/z, two unique m/z, and an unassigned peak)
and folds each sample, asserting the load-bearing behaviours end-to-end against a
real database: append-only anchor stability, cross-sample consensus, unassigned
peaks as first-class batch peaks, idempotent re-folding, and -- driving the race
deliberately -- that concurrent folds of one batch cannot duplicate an anchor.
"""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments import batch_peaks_controller
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    backfill_sample_batch_peaks,
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    get_batch_peak_ledger,
    get_batch_peak_series,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    Dataset,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

# (peak_id, mz, neutral_formula, ion_formula, role, tier, fit, intensity, mz_err_ppm)
_SPECS = {
    "A": [
        ("A1", 181.0707, "C6H12O6", "C6H13O6+", "M0", "assigned", 0.95, 5000.0, 1.0),
        ("A2", 200.0500, "C10H8O", "C10H9O+", "M0", "candidate", 0.60, 800.0, 1.0),
        ("A3", 250.1000, None, None, "unassigned", "unassigned", None, 300.0, None),
    ],
    "B": [
        # B1 shares A1's m/z -> must snap to the SAME anchor (append-only).
        ("B1", 181.0707, "C6H12O6", "C6H13O6+", "M0", "assigned", 0.90, 4500.0, 1.0),
        ("B2", 300.2000, "C12H10", "C12H11+", "M0", "assigned", 0.85, 2000.0, 1.0),
    ],
}


async def _seed(session, now):
    ws, ds, batch = gen_id(), gen_id(), gen_id()
    session.add(
        Workspace(
            workspace_id=ws,
            workspace_name=f"Batch Peak WS {ws}",
            workspace_status="active",
            workspace_utc_created=now,
            workspace_utc_modified=now,
        )
    )
    session.add(
        Dataset(
            dataset_id=ds,
            workspace_id=ws,
            dataset_name="BP DS",
            dataset_utc_created=now,
        )
    )
    session.add(
        SampleBatch(
            sample_batch_id=batch,
            dataset_id=ds,
            sample_batch_name="BP Batch",
            sample_batch_utc_created=now,
        )
    )
    samples = {}
    for name, rows in _SPECS.items():
        sf, si, run = gen_id(), gen_id(), gen_id()
        session.add(
            SampleFile(
                sample_file_id=sf,
                filename=f"orbi-bp-test-{name}-{sf}.zarr",
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
                sample_item_id=si,
                sample_batch_id=batch,
                sample_file_id=sf,
                sample_item_name=f"BP Sample {name}",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run,
                sample_item_id=si,
                engine_version="0.1.0-test",
                status="completed",
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        for pid, mz, nf, ionf, role, tier, fit, inten, err in rows:
            session.add(
                PeakAssignment(
                    peak_assignment_id=gen_id(32),
                    peak_assignment_run_id=run,
                    sample_item_id=si,
                    sample_peak_id=pid,
                    sample_peak_mz=mz,
                    sample_peak_intensity=inten,
                    role=role,
                    assigned_formula=nf,
                    ion_formula=ionf,
                    source=("database" if nf else None),
                    fit_score=fit,
                    mz_error_ppm=err,
                    tier=tier,
                )
            )
        samples[name] = si
    await session.commit()
    return batch, samples


@pytest_asyncio.fixture
async def seeded(async_session_factory, patch_db, pa_sample_view):
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        return await _seed(session, now)


async def _batch_peaks(session_factory, batch):
    async with session_factory() as s:
        return (
            (
                await s.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch)
                )
            )
            .scalars()
            .all()
        )


async def test_fold_is_append_only_with_cross_sample_consensus(
    async_session_factory, seeded
):
    batch, samples = seeded

    # --- Fold sample A: three anchors (two assigned, one unassigned). ---
    assert await fold_sample_into_batch_peaks(samples["A"]) == batch
    peaks_a = await _batch_peaks(async_session_factory, batch)
    assert len(peaks_a) == 3
    anchor181 = next(p for p in peaks_a if p.consensus_formula == "C6H12O6")
    id181, mz181 = anchor181.batch_peak_id, anchor181.mz
    assert anchor181.n_present == 1

    # --- Fold sample B: B1 snaps to the existing 181 anchor, B2 mints one. ---
    assert await fold_sample_into_batch_peaks(samples["B"]) == batch
    peaks_b = {
        p.batch_peak_id: p for p in await _batch_peaks(async_session_factory, batch)
    }
    assert len(peaks_b) == 4  # 181 (shared) + 200 + 250 + 300

    # Append-only: the shared anchor keeps its id AND its frozen m/z.
    assert id181 in peaks_b
    assert peaks_b[id181].mz == mz181

    shared = peaks_b[id181]
    assert shared.n_present == 2
    assert shared.consensus_formula == "C6H12O6"
    assert shared.consensus_tier == "assigned"
    assert shared.support_fraction == pytest.approx(1.0)

    async with async_session_factory() as s:
        occ = (
            (
                await s.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.batch_peak_id == id181
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(occ) == 2
    assert {o.sample_item_id for o in occ} == {samples["A"], samples["B"]}


async def test_unassigned_peak_is_a_first_class_batch_peak(
    async_session_factory, seeded
):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    peaks = await _batch_peaks(async_session_factory, batch)
    unassigned = [p for p in peaks if p.consensus_formula is None]
    assert len(unassigned) == 1
    assert unassigned[0].consensus_tier == "unassigned"
    assert unassigned[0].n_present == 1  # still a drawable trace


async def test_refold_is_idempotent(async_session_factory, seeded):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])
    await fold_sample_into_batch_peaks(samples["A"])  # re-fold A

    peaks = await _batch_peaks(async_session_factory, batch)
    assert len(peaks) == 4
    shared = next(p for p in peaks if p.consensus_formula == "C6H12O6")
    assert shared.n_present == 2

    async with async_session_factory() as s:
        occ = (
            (
                await s.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.batch_peak_id == shared.batch_peak_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(occ) == 2  # not duplicated
    assert len({o.sample_item_id for o in occ}) == 2


async def test_series_full_load_applies_occupancy_filter(async_session_factory, seeded):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])

    # Default occupancy (present in >= 2 samples): only the shared 181 peak.
    res = await get_batch_peak_series(sample_batch_id=batch)
    assert res["results"] == 1
    rec = res["data"][0]
    assert rec["consensus_formula"] == "C6H12O6"
    assert rec["consensus_tier"] == "assigned"
    assert rec["n_present"] == 2
    series = rec["peak_series"]
    assert set(series["sample_item_ids"]) == {samples["A"], samples["B"]}
    assert set(series["intensities"]) == {5000.0, 4500.0}
    assert series["tiers"] == ["assigned", "assigned"]

    # min_n_present=1 keeps every batch peak (181 + 200 + 250 + 300).
    res_all = await get_batch_peak_series(sample_batch_id=batch, min_n_present=1)
    assert res_all["results"] == 4


async def test_series_carries_the_sample_peak_behind_each_point(
    async_session_factory, seeded
):
    """The series pairs each sample with the peak folded in from it.

    This is what lets a click on a chart point land on the peak it was drawn
    from: without it the point knows its batch peak and its sample but not which
    of that sample's peaks it is. Row order is not guaranteed, so the arrays are
    asserted as pairs rather than by position.
    """
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])

    res = await get_batch_peak_series(sample_batch_id=batch)
    series = res["data"][0]["peak_series"]

    # The shared 181 peak was folded from A1 in sample A and B1 in sample B.
    assert dict(zip(series["sample_item_ids"], series["sample_peak_ids"])) == {
        samples["A"]: "A1",
        samples["B"]: "B1",
    }
    # Parallel to every other array, so a point index means the same thing in all.
    assert len(series["sample_peak_ids"]) == len(series["intensities"])

    # An unassigned batch peak carries its sample peak too -- it is a first-class
    # trace, and clicking it is how you get to a peak nothing was assigned to.
    res_all = await get_batch_peak_series(sample_batch_id=batch, min_n_present=1)
    unassigned = next(r for r in res_all["data"] if r["consensus_tier"] == "unassigned")
    assert unassigned["peak_series"]["sample_peak_ids"] == ["A3"]


async def test_series_sample_slice_ignores_occupancy(async_session_factory, seeded):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])

    # A single-sample slice returns sample A's three peaks, each series limited to A.
    res = await get_batch_peak_series(sample_item_ids=[samples["A"]])
    assert res["results"] == 3
    for rec in res["data"]:
        assert rec["peak_series"]["sample_item_ids"] == [samples["A"]]


async def test_backfill_folds_every_sample_of_the_batch(async_session_factory, seeded):
    batch, samples = seeded
    # Backfill from the samples' existing completed runs (no re-assignment).
    # The failure count is what tells "nothing was assigned" apart from "every
    # fold raised", which are the same zero to the caller otherwise.
    folded, failed = await backfill_sample_batch_peaks(batch)
    assert (folded, failed) == (2, 0)

    peaks = await _batch_peaks(async_session_factory, batch)
    assert len(peaks) == 4  # same as folding A and B one by one
    shared = next(p for p in peaks if p.consensus_formula == "C6H12O6")
    assert shared.n_present == 2


async def test_ledger_returns_metadata_without_series(async_session_factory, seeded):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])

    # Default occupancy (>= 2 samples): only the shared 181 peak, metadata only.
    res = await get_batch_peak_ledger(sample_batch_id=batch)
    assert res["results"] == 1
    rec = res["data"][0]
    assert rec["consensus_formula"] == "C6H12O6"
    assert rec["n_present"] == 2
    assert "peak_series" not in rec  # ledger is metadata-only

    # min_n_present=1 lists every batch peak, still without per-sample series.
    res_all = await get_batch_peak_ledger(sample_batch_id=batch, min_n_present=1)
    assert res_all["results"] == 4
    assert all("peak_series" not in r for r in res_all["data"])


# --- the fold-in lock -------------------------------------------------------
#
# Two folds of one batch that both read the frozen anchor set before either
# commits each mint their own anchor for the species they share, and since
# anchors never move the split is permanent. The two tests below drive that race
# on purpose: one fold is held open after it has read the anchors and minted its
# own, and the other is released into that window. With the lock the second fold
# cannot get in and the species keeps one anchor; with the lock patched out the
# same harness produces the duplicate -- which is what proves the passing test
# is not passing vacuously.
#
# The gate only ever holds the fold that arrives FIRST, only until the second
# reaches its anchor read, and only for a bounded time. So it cannot deadlock
# against the lock it is testing: under the lock the second fold never reaches
# that read, and the first waits out the hold alone and commits.

#: How long the first fold holds the window open under the lock. Nothing can
#: signal it early there -- the second fold is blocked before the anchor read
#: that would -- so this is always paid in full and is the whole cost of that
#: test. The assertions do not depend on its length: whether the second fold
#: waits on the lock or arrives after the release, it reads a committed anchor
#: set either way.
_LOCKED_HOLD_SECONDS = 1.0

#: The same hold with the lock removed, where it means the opposite thing: a
#: ceiling the second fold is expected to beat, not a wait. It costs nothing to
#: be generous (reaching the anchor read ends it early, and always does), and
#: being generous is what keeps a slow first connection on a cold pool from
#: overrunning it -- which would silently turn the race into a serialized run
#: and fail the test for a reason that is not the code's.
_UNLOCKED_HOLD_SECONDS = 10.0

#: Ceiling on the whole two-fold run, so a fold that raises before it reaches
#: the window fails the test instead of hanging the suite on an event that will
#: never be set.
_FOLD_RACE_TIMEOUT_SECONDS = 30.0


class _FoldGate:
    """Holds the first fold open inside its read-mint-insert window.

    ``in_window`` fires once a fold has read the anchor set and minted its own
    anchors but has not yet committed; ``second_read_anchors`` fires when a
    second fold reaches its own anchor read, which is what the first one is
    waiting to see. Under the fold-in lock the second never gets there while the
    window is open -- that is the whole point -- so the wait is bounded rather
    than a rendezvous.

    ``raced`` is the verdict: whether the second fold reached its anchor read
    *while the first still held the window open*. It has to be sampled at the
    moment the hold ends, because the second fold reads the anchors either way
    in the end -- under the lock it just does so afterwards, off a committed
    anchor set. Reading the event after both folds return would say "yes" to
    both the serialized and the racing run.
    """

    def __init__(self) -> None:
        self.in_window = asyncio.Event()
        self.second_read_anchors = asyncio.Event()
        self.anchor_reads = 0
        self.folds_in_window = 0
        self.raced: bool | None = None


def _install_fold_gate(monkeypatch, hold_seconds: float) -> _FoldGate:
    """Patch the two seams the race needs and return the gate driving them.

    ``_recompute_consensus`` is the last thing a fold awaits before its commit,
    so it sits inside the window and after the anchor mint; ``AnchorSet`` is
    constructed exactly once per fold, immediately after the anchor read, so
    counting its constructions counts folds that got as far as reading.
    """
    gate = _FoldGate()
    real_recompute = batch_peaks_controller._recompute_consensus
    real_anchor_set = batch_peaks_controller.AnchorSet

    async def gated_recompute(session, batch_peak_ids, now):
        gate.folds_in_window += 1
        if gate.folds_in_window == 1:
            gate.in_window.set()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(gate.second_read_anchors.wait(), hold_seconds)
            gate.raced = gate.second_read_anchors.is_set()
        return await real_recompute(session, batch_peak_ids, now)

    def counting_anchor_set(anchors=()):
        gate.anchor_reads += 1
        if gate.anchor_reads >= 2:
            gate.second_read_anchors.set()
        return real_anchor_set(anchors)

    monkeypatch.setattr(batch_peaks_controller, "_recompute_consensus", gated_recompute)
    monkeypatch.setattr(batch_peaks_controller, "AnchorSet", counting_anchor_set)
    return gate


async def _fold_two_samples_concurrently(gate, samples) -> list:
    """Fold A and B at once, B entering only once A is inside its window."""

    async def first():
        return await fold_sample_into_batch_peaks(samples["A"])

    async def second():
        await gate.in_window.wait()
        return await fold_sample_into_batch_peaks(samples["B"])

    return await asyncio.wait_for(
        asyncio.gather(first(), second()), _FOLD_RACE_TIMEOUT_SECONDS
    )


def _anchors_near(peaks, mz: float, tol_ppm: float = 5.0) -> list:
    """The batch peaks within ``tol_ppm`` of ``mz`` -- one species' anchors."""
    return [p for p in peaks if abs(p.mz - mz) / mz * 1e6 <= tol_ppm]


async def test_concurrent_folds_of_one_batch_keep_one_anchor_per_species(
    async_session_factory, seeded, monkeypatch
):
    """The lock serializes the window, so the shared species keeps one anchor."""
    batch, samples = seeded
    gate = _install_fold_gate(monkeypatch, _LOCKED_HOLD_SECONDS)

    assert await _fold_two_samples_concurrently(gate, samples) == [batch, batch]

    # The lock held: the second fold never reached the anchor read while the
    # first had the window open, so it read a committed anchor set afterwards.
    assert gate.raced is False

    peaks = await _batch_peaks(async_session_factory, batch)
    assert len(peaks) == 4  # 181 (shared) + 200 + 250 + 300

    shared = _anchors_near(peaks, 181.0707)
    assert len(shared) == 1
    assert shared[0].n_present == 2  # both samples on one trace
    assert shared[0].consensus_formula == "C6H12O6"
    assert shared[0].support_fraction == pytest.approx(1.0)


async def test_concurrent_folds_without_the_lock_split_one_species(
    async_session_factory, seeded, monkeypatch
):
    """Negative control: the same race, lock removed, mints the duplicate.

    This is what the fold did before ``_acquire_batch_fold_lock`` existed, and
    it is what makes the test above meaningful -- without it, a fold that simply
    never interleaved would pass just as well.
    """
    batch, samples = seeded
    gate = _install_fold_gate(monkeypatch, _UNLOCKED_HOLD_SECONDS)

    async def _no_lock(session, sample_batch_id):
        return None

    monkeypatch.setattr(batch_peaks_controller, "_acquire_batch_fold_lock", _no_lock)

    assert await _fold_two_samples_concurrently(gate, samples) == [batch, batch]

    # The second fold really did read the anchor set inside the first's window;
    # that is the race, not a timing accident.
    assert gate.raced is True

    peaks = await _batch_peaks(async_session_factory, batch)
    assert len(peaks) == 5  # one more than the serialized fold produces

    # One species, two anchors, neither of which knows about the other sample --
    # and because anchors are frozen, no later fold ever merges them.
    shared = _anchors_near(peaks, 181.0707)
    assert len(shared) == 2
    assert [p.n_present for p in shared] == [1, 1]
