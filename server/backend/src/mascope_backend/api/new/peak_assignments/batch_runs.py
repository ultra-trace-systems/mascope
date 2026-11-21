"""Batch runs: the batch ledger's history, one record per batch-level operation.

The batch ledger is one living object - every fold, search, curation and
verdict edits it in place. A **batch run** is a record of a batch-level
operation that rewrote it (a rebuild, an untargeted search with its
parameters, an import), with what the ledger looked like when the next one
started. That is the batch counterpart of a sample's assignment runs, and it is
what a run selector switches between.

Three rules:

- **The live ledger is the current run.** Exactly one run per batch is
  ``current``: the one whose state the anchors and members hold. A ledger built
  by folds alone gets an implicit ``fold`` run the first time a sample folds in,
  so there is always a run to name; later folds, curations and verdicts edit the
  current run's state without creating a run, as per-sample curation edits a row
  of a run.
- **Snapshots are taken when the next run starts, per anchor, as arrays.** When
  a new run starts, the current run's state is captured into
  ``batch_peak_run_anchor``: one row per anchor holding its consensus and its
  members as parallel arrays (sample, peak, offset, intensity, candidate index,
  tier, role, fit, P(correct), owner). Columnar because a snapshot is written
  once and read whole - the run selector reads the ledger as that run left it,
  the chart reads the series - and the arrays cost a small fraction of a row per
  member. The registry rides along, so the snapshot resolves its own candidate
  indices however the live registry grows.
- **Retention is bounded.** Completing a run makes it current and prunes the
  batch's older runs beyond :data:`BATCH_RUN_KEEP`, snapshots and all.

Edits that make no run would leave a client reading the run listing nothing to
see them by, so every write to the live ledger moves the current run's
``ledger_utc_modified`` in its own transaction (:func:`touch_ledger`): the
stamp a client compares, as it compares a batch's modified time.

A run that fails is kept, marked, and never becomes current; the live ledger is
whatever the failed operation left, which the record says.

**A pass of the batch stages is one run** (``batch_stages.assign_batch``): one
start, one snapshot, one end, with every stage's outcome in its ``summary``
under ``stages``. Each stage's step writes into the pass's run
(:func:`in_batch_run`) instead of opening its own, so :data:`BATCH_RUN_KEEP`
counts passes, not the clicks of one. A pass a person cancels
(:func:`request_cancel`) stops between stages, keeps what it wrote and is
recorded ``cancelled``, never current, as a failed one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime as dt
from datetime import timezone
from typing import Any, Iterable, Optional

from fastapi import status
from sqlalchemy import delete, insert, select, update

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    CodedHTTPException,
    NotFoundException,
)
from mascope_backend.api.new.peak_assignments.batch_blend import blend_record_of
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    continuity_stale,
    instrument_type_of,
    known_species_summary,
    manual_pin_of,
    merged_isotopologue_of_summary,
    owner_link,
    persistence_summary,
    stamped_isotopologue_of_summary,
    standing_batch_checks_record,
    standing_co_variation_record,
    standing_diagnostic_isotopologues_record,
    standing_flat_series_record,
    standing_merged_isotopologues_record,
    standing_series_record,
    tier_name,
    tier_reasons_of,
    water_ladder_summary,
)
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    PEAK_ASSIGNMENT_ENGINE_VERSION,
)
from mascope_backend.api.new.peak_assignments.listing import consensus_listing
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    BatchPeakRunAnchor,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime


#: What a batch run did.
ACTION_FOLD = "fold"
ACTION_REBUILD = "rebuild"
ACTION_SEARCH = "search_untargeted"
ACTION_IMPORT = "import"
ACTION_SERIES = "detect_series"
ACTION_CO_VARIATION = "read_co_variation"
ACTION_ISOTOPOLOGUES = "stamp_isotopologues"
ACTION_STRIP_ISOTOPOLOGUES = "strip_isotopologues"
ACTION_BATCH_CHECKS = "read_batch_checks"
ACTION_KNOWN_SPECIES = "decide_known_species"
ACTION_WATER_LADDERS = "measure_water_ladders"
#: A pass of the batch stages, started as one (``batch_stages.assign_batch``).
#: A pass a step's own route starts keeps that step's action.
ACTION_ASSIGN = "assign"
ACTIONS = (
    ACTION_FOLD,
    ACTION_REBUILD,
    ACTION_SEARCH,
    ACTION_IMPORT,
    ACTION_SERIES,
    ACTION_CO_VARIATION,
    ACTION_ISOTOPOLOGUES,
    ACTION_STRIP_ISOTOPOLOGUES,
    ACTION_BATCH_CHECKS,
    ACTION_KNOWN_SPECIES,
    ACTION_WATER_LADDERS,
    ACTION_ASSIGN,
)

#: The runs no pass is: the folds that built a ledger, a rebuild started on its
#: own, an engine's result imported. A cancel is never theirs.
NOT_A_PASS = frozenset({ACTION_FOLD, ACTION_REBUILD, ACTION_IMPORT})

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
#: A pass that stopped before its end without failing: a person cancelled it,
#: or its task was cancelled (``error`` then says so). It kept what it wrote
#: and never became current.
STATUS_CANCELLED = "cancelled"

#: The key a running pass keeps its progress under in its run's summary
#: (:func:`record_progress`), until its end writes the summary whole.
PROGRESS_KEY = "progress"

#: Runs kept per batch beyond the current one, snapshots and all. The rest are
#: pruned when a run completes, oldest first.
BATCH_RUN_KEEP = 5

#: Machine-readable code on the refusal a client has to react to.
RUN_IN_FLIGHT_CODE = "batch_run_in_flight"

#: The parallel arrays a snapshot holds per anchor, in order.
MEMBER_FIELDS = (
    "sample_item_ids",
    "sample_peak_ids",
    "mz_delta_ppms",
    "intensities",
    "candidates",
    "tiers",
    "roles",
    "fit_scores",
    "p_corrects",
    "owner_batch_peak_ids",
)

#: Rows inserted per statement when a snapshot is written.
_INSERT_CHUNK = 500


class BatchRunInFlightException(CodedHTTPException):
    """A batch-level operation is already rewriting this ledger (409)."""

    error_code = RUN_IN_FLIGHT_CODE

    def __init__(self, sample_batch_id: str, running: Any):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A batch run ({running.action}, started "
                f"{running.batch_peak_run_utc_created:%Y-%m-%d %H:%M} UTC) is still "
                "rewriting this batch's ledger; wait for it to finish"
            ),
        )


# --- pure parts -------------------------------------------------------------------


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def member_arrays(members: Iterable[Any]) -> dict[str, list]:
    """A snapshot's parallel arrays for one anchor's members, in sample order,
    so two snapshots of the same state compare equal."""
    rows = sorted(members, key=lambda m: _get(m, "sample_item_id") or "")
    return {
        "sample_item_ids": [_get(m, "sample_item_id") for m in rows],
        "sample_peak_ids": [_get(m, "sample_peak_id") for m in rows],
        "mz_delta_ppms": [_get(m, "mz_delta_ppm") for m in rows],
        "intensities": [_get(m, "intensity") for m in rows],
        "candidates": [_get(m, "candidate") for m in rows],
        "tiers": [_get(m, "tier") for m in rows],
        "roles": [_get(m, "role") for m in rows],
        "fit_scores": [_get(m, "fit_score") for m in rows],
        "p_corrects": [_get(m, "p_correct") for m in rows],
        "owner_batch_peak_ids": [_get(m, "owner_batch_peak_id") for m in rows],
    }


def anchor_snapshot_row(run_id: str, anchor: Any, members: Iterable[Any]) -> dict:
    """One ``batch_peak_run_anchor`` row: the anchor's consensus and its members."""
    return {
        "batch_peak_run_id": run_id,
        "batch_peak_id": anchor.batch_peak_id,
        "mz": anchor.mz,
        "ionization_mode_id": anchor.ionization_mode_id,
        "consensus_formula": anchor.consensus_formula,
        "consensus_ion_formula": anchor.consensus_ion_formula,
        "ionization_mechanism_id": anchor.ionization_mechanism_id,
        "consensus_tier": anchor.consensus_tier,
        "consensus_role": anchor.consensus_role,
        "best_fit_score": anchor.best_fit_score,
        "support_fraction": anchor.support_fraction,
        "n_present": anchor.n_present,
        "is_ambiguous": int(bool(anchor.is_ambiguous)),
        "intensity_variable": anchor.intensity_variable,
        "max_intensity": anchor.max_intensity,
        "isotopologue_of": anchor.isotopologue_of,
        "curated": int(manual_pin_of(anchor) is not None),
        "seeded": bool(anchor.seeded),
        "engine_tier": anchor.engine_tier,
        "engine_claim": anchor.engine_claim,
        "engine_level": anchor.engine_level,
        "engine_evidence": anchor.engine_evidence,
        "engine_lift": anchor.engine_lift,
        "series": standing_series_record(anchor),
        "co_variation": standing_co_variation_record(anchor),
        "continuity_grade": anchor.continuity_grade,
        "continuity": anchor.continuity,
        "diagnostic_isotopologues": standing_diagnostic_isotopologues_record(anchor),
        "stamped_isotopologue_of": stamped_isotopologue_of_summary(anchor),
        "merged_isotopologue_of": merged_isotopologue_of_summary(anchor),
        "merged_isotopologues": standing_merged_isotopologues_record(anchor),
        "batch_checks": standing_batch_checks_record(anchor),
        "known_species": known_species_summary(anchor),
        "blend": blend_record_of(anchor),
        "persistence": persistence_summary(anchor),
        "tier_reasons": tier_reasons_of(anchor),
        "flat_series": standing_flat_series_record(anchor),
        "water_ladder": water_ladder_summary(anchor),
        "candidates": list(anchor.candidates or []),
        "members": member_arrays(members),
    }


def snapshot_anchor_meta(row: Any, sample_batch_id: str) -> dict:
    """A snapshot row in the shape the ledger route serves a live anchor in."""
    return {
        "batch_peak_id": row.batch_peak_id,
        "sample_batch_id": sample_batch_id,
        "ionization_mode_id": row.ionization_mode_id,
        "mz": row.mz,
        "consensus_formula": row.consensus_formula,
        "consensus_ion_formula": row.consensus_ion_formula,
        "ionization_mechanism_id": row.ionization_mechanism_id,
        "consensus_tier": row.consensus_tier,
        "consensus_role": row.consensus_role,
        "best_fit_score": row.best_fit_score,
        "support_fraction": row.support_fraction,
        "n_present": row.n_present,
        "is_ambiguous": bool(row.is_ambiguous),
        "intensity_variable": row.intensity_variable,
        # The partition the batch peak is in, beside its mode: the instrument
        # type its unit belongs to. A batch of both types has two.
        "instrument_type": instrument_type_of(row.intensity_variable),
        "max_intensity": row.max_intensity,
        "isotopologue_of": row.isotopologue_of,
        # As the live ledger's: the members' link, else the stamp's parent.
        "line_owner_id": owner_link(row.isotopologue_of, row.stamped_isotopologue_of),
        "curated": bool(row.curated),
        "seeded": bool(row.seeded),
        "engine_tier": row.engine_tier,
        "engine_claim": row.engine_claim,
        "engine_level": row.engine_level,
        "engine_evidence": row.engine_evidence,
        "engine_lift": row.engine_lift,
        "series": row.series,
        "co_variation": row.co_variation,
        "continuity_grade": row.continuity_grade,
        "continuity": row.continuity,
        # Against the snapshot's own consensus: a grade the run's ledger had
        # already moved past when it was captured.
        "continuity_stale": continuity_stale(row),
        "diagnostic_isotopologues": row.diagnostic_isotopologues,
        "stamped_isotopologue_of": row.stamped_isotopologue_of,
        "merged_isotopologue_of": row.merged_isotopologue_of,
        "merged_isotopologues": row.merged_isotopologues,
        "batch_checks": row.batch_checks,
        "known_species": row.known_species,
        "blend": row.blend,
        "persistence": row.persistence,
        "tier_reasons": row.tier_reasons,
        "flat_series": row.flat_series,
        "water_ladder": row.water_ladder,
        "reference_listing": consensus_listing(
            row.candidates,
            row.consensus_formula,
            row.consensus_ion_formula,
            row.ionization_mechanism_id,
        ),
        "batch_peak_run_id": row.batch_peak_run_id,
    }


def snapshot_series(row: Any, sample_item_ids: Optional[Iterable[str]] = None) -> dict:
    """A snapshot row's members in the series route's shape, optionally
    narrowed to some samples."""
    arrays = row.members if isinstance(row.members, dict) else {}
    keep = set(sample_item_ids) if sample_item_ids else None
    samples = arrays.get("sample_item_ids") or []
    picked = [i for i, sample in enumerate(samples) if keep is None or sample in keep]
    peaks = arrays.get("sample_peak_ids") or []
    intensities = arrays.get("intensities") or []
    tiers = arrays.get("tiers") or []
    return {
        "sample_item_ids": [samples[i] for i in picked],
        "sample_peak_ids": [peaks[i] for i in picked],
        "intensities": [intensities[i] for i in picked],
        "tiers": [tier_name(tiers[i]) for i in picked],
    }


def engine_tier_filters(
    table: Any, engine_tier: Optional[str], tier_disagrees: Optional[bool]
) -> list:
    """The ledger read's filters on the importing engine's own tier, for the
    live anchors (``BatchPeak``) or a snapshot's (``BatchPeakRunAnchor``).

    ``tier_disagrees`` keeps the anchors whose engine tier differs from the
    consensus tier (True) or equals it (False); an anchor with no engine tier
    is out either way, because absence is not agreement - the sample ledger's
    rule (``service.get_peaks_with_assignments``).
    """
    filters = []
    if engine_tier:
        filters.append(table.engine_tier == engine_tier)
    if tier_disagrees is not None:
        # No IS NOT NULL beside it: either comparison with a NULL engine tier
        # is NULL in SQL, which a WHERE drops, so the anchor is out both ways.
        filters.append(
            table.engine_tier != table.consensus_tier
            if tier_disagrees
            else table.engine_tier == table.consensus_tier
        )
    return filters


def run_record(run: Any) -> dict:
    """The run as the runs route serves it."""
    record = run.to_dict()
    record["current"] = bool(run.is_current)
    record["is_current"] = bool(run.is_current)
    return record


def runs_to_prune(runs: Iterable[Any], keep: int = BATCH_RUN_KEEP) -> list[str]:
    """Which of a batch's runs to drop: every non-current run beyond the
    ``keep`` newest ones. ``runs`` in any order."""
    others = sorted(
        (r for r in runs if not _get(r, "is_current")),
        key=lambda r: _get(r, "batch_peak_run_utc_created"),
        reverse=True,
    )
    return [_get(r, "batch_peak_run_id") for r in others[keep:]]


# --- the current run --------------------------------------------------------------


async def current_run(session, sample_batch_id: str) -> Optional[BatchPeakRun]:
    return (
        await session.execute(
            select(BatchPeakRun).where(
                BatchPeakRun.sample_batch_id == sample_batch_id,
                BatchPeakRun.is_current == 1,
            )
        )
    ).scalar_one_or_none()


async def ensure_current_run(session, sample_batch_id: str) -> BatchPeakRun:
    """The batch's current run, minting the implicit ``fold`` run when the
    ledger has none yet. Called under the batch's fold lock."""
    run = await current_run(session, sample_batch_id)
    if run is not None:
        return run
    now = dt.now(timezone.utc)
    run = BatchPeakRun(
        batch_peak_run_id=gen_id(),
        sample_batch_id=sample_batch_id,
        action=ACTION_FOLD,
        engine=IN_APP_ENGINE,
        engine_version=PEAK_ASSIGNMENT_ENGINE_VERSION,
        status=STATUS_COMPLETED,
        is_current=1,
        batch_peak_run_utc_created=now,
        batch_peak_run_utc_completed=now,
    )
    session.add(run)
    await session.flush()
    return run


async def touch_ledger(session, sample_batch_id: str) -> None:
    """Stamp the batch's current run with the time its live ledger was last
    written. Called by every write to the live ledger - a fold, a consensus
    recompute, a curation, a search's, an import's or a series detection's
    rows, a co-variation reading's, an isotopologue stamping's or the isotope
    checks' records, a merged-isotopologue reading's strips, a continuity
    pass's grades - in the write's own transaction, under the batch's fold
    lock, so the stamp commits with the write and never without it. A ledger
    with no current run yet has nothing to stamp: its first fold mints the run
    before it writes."""
    await session.execute(
        update(BatchPeakRun)
        .where(
            BatchPeakRun.sample_batch_id == sample_batch_id,
            BatchPeakRun.is_current == 1,
        )
        .values(ledger_utc_modified=dt.now(timezone.utc))
    )


async def running_run(session, sample_batch_id: str) -> Optional[BatchPeakRun]:
    return (
        (
            await session.execute(
                select(BatchPeakRun).where(
                    BatchPeakRun.sample_batch_id == sample_batch_id,
                    BatchPeakRun.status == STATUS_RUNNING,
                )
            )
        )
        .scalars()
        .first()
    )


@api_controller()
async def check_no_run_in_flight(sample_batch_id: str) -> dict:
    """The routes' synchronous refusal: a second batch-level operation on a
    ledger one is still rewriting is refused on the request, not in a
    notification.

    Refused while a batch run is in flight, and while a pass of the batch
    stages runs: a pass holds a stage row running from its start to its end,
    also where it holds no batch run (``batch_stages.BatchStageRunningException``).
    """
    # Imported here: the stages import this module.
    from mascope_backend.api.new.peak_assignments.batch_stages import (
        BatchStageRunningException,
        running_stage,
    )

    async with async_session() as session:
        stage = await running_stage(session, sample_batch_id)
        if stage is not None:
            raise BatchStageRunningException(sample_batch_id, stage)
        running = await running_run(session, sample_batch_id)
        if running is not None:
            raise BatchRunInFlightException(sample_batch_id, running)
    return {"status": "success", "message": "No batch run in flight", "results": 0}


# --- snapshots --------------------------------------------------------------------


async def _snapshot(session, run: BatchPeakRun) -> int:
    """Capture the live ledger under ``run``, replacing any earlier capture of
    it. Called under the fold lock. Returns the anchors captured."""
    anchors = (
        (
            await session.execute(
                select(BatchPeak).where(
                    BatchPeak.sample_batch_id == run.sample_batch_id
                )
            )
        )
        .scalars()
        .all()
    )
    by_anchor: dict[str, list] = {bp.batch_peak_id: [] for bp in anchors}
    if anchors:
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.batch_peak_id,
                    BatchPeakOccurrence.sample_item_id,
                    BatchPeakOccurrence.sample_peak_id,
                    BatchPeakOccurrence.mz_delta_ppm,
                    BatchPeakOccurrence.intensity,
                    BatchPeakOccurrence.candidate,
                    BatchPeakOccurrence.tier,
                    BatchPeakOccurrence.role,
                    BatchPeakOccurrence.fit_score,
                    BatchPeakOccurrence.p_correct,
                    BatchPeakOccurrence.owner_batch_peak_id,
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(BatchPeak.sample_batch_id == run.sample_batch_id)
            )
        ).all()
        for r in rows:
            by_anchor[r.batch_peak_id].append(r._mapping)
    await session.execute(
        delete(BatchPeakRunAnchor).where(
            BatchPeakRunAnchor.batch_peak_run_id == run.batch_peak_run_id
        )
    )
    snapshot = [
        anchor_snapshot_row(run.batch_peak_run_id, bp, by_anchor[bp.batch_peak_id])
        for bp in anchors
    ]
    for start in range(0, len(snapshot), _INSERT_CHUNK):
        await session.execute(
            insert(BatchPeakRunAnchor), snapshot[start : start + _INSERT_CHUNK]
        )
    run.snapshot_utc = dt.now(timezone.utc)
    return len(snapshot)


async def start_run(
    sample_batch_id: str,
    action: str,
    *,
    config: Optional[dict] = None,
    engine: str = IN_APP_ENGINE,
    engine_version: str = PEAK_ASSIGNMENT_ENGINE_VERSION,
    user_id: Optional[int] = None,
) -> str:
    """Open a batch run: refuse if one is in flight, snapshot the current run's
    state, insert the new run as running.

    :return: The new run's id.
    """
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        _acquire_batch_fold_lock,
    )

    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        running = await running_run(session, sample_batch_id)
        if running is not None:
            raise BatchRunInFlightException(sample_batch_id, running)
        current = await ensure_current_run(session, sample_batch_id)
        await _snapshot(session, current)
        run = BatchPeakRun(
            batch_peak_run_id=gen_id(),
            sample_batch_id=sample_batch_id,
            action=action,
            engine=engine,
            engine_version=engine_version,
            status=STATUS_RUNNING,
            is_current=0,
            config=config,
            created_by=user_id,
            batch_peak_run_utc_created=dt.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
        return run.batch_peak_run_id


async def complete_run(
    sample_batch_id: str, batch_peak_run_id: str, summary: Optional[dict] = None
) -> None:
    """Close a run as completed: it becomes the current run, the previous one
    keeps the snapshot taken when this one started, and the batch's older runs
    beyond :data:`BATCH_RUN_KEEP` are pruned."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        _acquire_batch_fold_lock,
    )

    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        runs = (
            (
                await session.execute(
                    select(BatchPeakRun).where(
                        BatchPeakRun.sample_batch_id == sample_batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
        now = dt.now(timezone.utc)
        for run in runs:
            if run.batch_peak_run_id == batch_peak_run_id:
                run.status = STATUS_COMPLETED
                run.is_current = 1
                run.summary = summary
                run.batch_peak_run_utc_completed = now
                # The live ledger is what this run wrote
                run.ledger_utc_modified = now
            elif run.is_current:
                run.is_current = 0
        for stale_id in runs_to_prune(runs, BATCH_RUN_KEEP):
            if stale_id == batch_peak_run_id:
                continue
            await session.execute(
                delete(BatchPeakRun).where(BatchPeakRun.batch_peak_run_id == stale_id)
            )
        await session.commit()


async def _close_run(
    sample_batch_id: str,
    batch_peak_run_id: str,
    status: str,
    error: Optional[str],
    summary: Optional[dict],
) -> None:
    async with async_session() as session:
        run = await session.get(BatchPeakRun, batch_peak_run_id)
        if run is None or run.sample_batch_id != sample_batch_id:
            return
        run.status = status
        if error is not None:
            run.error = error[:2000]
        if summary is not None:
            run.summary = summary
        run.batch_peak_run_utc_completed = dt.now(timezone.utc)
        await session.commit()


async def fail_run(
    sample_batch_id: str,
    batch_peak_run_id: str,
    error: str,
    summary: Optional[dict] = None,
) -> None:
    """Close a run as failed; the current run stays what it was. ``summary``,
    where given, is what it did before it failed: a pass's stages."""
    await _close_run(sample_batch_id, batch_peak_run_id, STATUS_FAILED, error, summary)


async def cancel_run(
    sample_batch_id: str,
    batch_peak_run_id: str,
    summary: Optional[dict] = None,
    reason: Optional[str] = None,
) -> None:
    """Close a pass as cancelled: what it wrote stands, and, as a failed run,
    it never becomes current, so the snapshot taken when it started still
    holds the ledger as it was before it."""
    await _close_run(
        sample_batch_id, batch_peak_run_id, STATUS_CANCELLED, reason, summary
    )


async def close_shielded(close: Awaitable[None], what: str) -> None:
    """A run's closing write, made while an interruption unwinds: shielded,
    so a cancellation does not stop it halfway (it goes on in its own task
    when the cancellation is level-triggered, as anyio's is), and logged
    rather than raised where it fails, so it never replaces what interrupted
    it."""
    try:
        await asyncio.shield(close)
    except asyncio.CancelledError:
        # The write goes on in its own task; the caller re-raises what
        # interrupted it.
        pass
    except Exception as exc:  # noqa: BLE001 - never replace what interrupted it
        runtime.logger.warning(f"Could not close {what}: {exc}")


async def in_batch_run(
    sample_batch_id: str,
    action: str,
    work: Callable[[str], Awaitable[Any]],
    *,
    batch_peak_run_id: Optional[str] = None,
    config: Optional[dict] = None,
    user_id: Optional[int] = None,
) -> Any:
    """Run one step's ``work`` inside a batch run: the one it is given, a
    pass's, which its start and end are the pass's business; else a run of its
    own, opened as ``action`` with ``config`` and closed with what ``work``
    answers as its summary, or as failed where it raises or is cancelled.

    :param work: The step, called with the run's id, which every record it
        writes names.
    :return: What ``work`` answers.
    """
    if batch_peak_run_id is not None:
        return await work(batch_peak_run_id)
    run_id = await start_run(sample_batch_id, action, config=config, user_id=user_id)
    try:
        result = await work(run_id)
    except BaseException as exc:
        # BaseException: a cancelled task escapes ``except Exception``, and a
        # run left running holds the ledger until the startup reaper runs.
        await close_shielded(
            fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}"),
            f"batch run '{run_id}' as failed",
        )
        raise
    await complete_run(sample_batch_id, run_id, summary=result)
    return result


async def record_progress(batch_peak_run_id: str, progress: dict) -> None:
    """Keep a running pass's progress on its run, as each of its stages
    starts: the process its packets go out as, the stage, which of how many.
    The batch's assignment status reads it, so another user's tab, or one
    that missed the packets, shows the pass rather than meeting a 409
    (``batch_assignment_status``). Kept in ``summary`` under ``progress``,
    which the pass's end replaces with its whole summary; a run already
    closed is left alone.

    Logged rather than raised where it fails: the pass's work does not hang
    on the status line.
    """
    try:
        async with async_session() as session:
            await session.execute(
                update(BatchPeakRun)
                .where(
                    BatchPeakRun.batch_peak_run_id == batch_peak_run_id,
                    BatchPeakRun.status == STATUS_RUNNING,
                )
                .values(summary={PROGRESS_KEY: progress})
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - the status line is not the pass
        runtime.logger.warning(
            f"Could not record the progress of batch run '{batch_peak_run_id}': {exc}"
        )


# --- cancelling a pass ------------------------------------------------------------


async def request_cancel(sample_batch_id: str) -> Optional[BatchPeakRun]:
    """Ask the batch's running pass to stop: the flag it reads between its
    stages, and between the search's samples. A rebuild, an import and a fold
    are no pass and read no flag.

    :return: The pass asked, or None where none runs.
    """
    async with async_session() as session:
        run = (
            (
                await session.execute(
                    select(BatchPeakRun)
                    .where(
                        BatchPeakRun.sample_batch_id == sample_batch_id,
                        BatchPeakRun.status == STATUS_RUNNING,
                        BatchPeakRun.action.not_in(sorted(NOT_A_PASS)),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if run is None:
            return None
        if run.cancel_requested_utc is None:
            run.cancel_requested_utc = dt.now(timezone.utc)
        await session.commit()
        await session.refresh(run)
        return run


async def cancel_requested(batch_peak_run_id: str) -> bool:
    """Whether a person asked the pass to stop."""
    async with async_session() as session:
        return (
            await session.execute(
                select(BatchPeakRun.cancel_requested_utc).where(
                    BatchPeakRun.batch_peak_run_id == batch_peak_run_id
                )
            )
        ).scalar_one_or_none() is not None


# --- reads ------------------------------------------------------------------------


@api_controller()
async def list_batch_runs(sample_batch_id: str) -> dict:
    """A batch's runs, newest first, the current one flagged."""
    async with async_session() as session:
        runs = (
            (
                await session.execute(
                    select(BatchPeakRun)
                    .where(BatchPeakRun.sample_batch_id == sample_batch_id)
                    .order_by(BatchPeakRun.batch_peak_run_utc_created.desc())
                )
            )
            .scalars()
            .all()
        )
        data = [run_record(run) for run in runs]
    return {
        "status": "success",
        "message": f"Retrieved {len(data)} batch run{'s' if len(data) != 1 else ''}",
        "results": len(data),
        "data": data,
    }


async def resolve_run(
    session, sample_batch_id: str, batch_peak_run_id: Optional[str]
) -> Optional[BatchPeakRun]:
    """The run a read names, or None for the live ledger: no id, or the id of
    the current run. An id of another batch, or of no run, is a 404."""
    if not batch_peak_run_id:
        return None
    run = await session.get(BatchPeakRun, batch_peak_run_id)
    if run is None or run.sample_batch_id != sample_batch_id:
        raise NotFoundException(
            f"Batch run '{batch_peak_run_id}' not found in this batch"
        )
    return None if run.is_current else run


async def snapshot_rows(
    session,
    run: BatchPeakRun,
    *,
    batch_peak_ids: Optional[Iterable[str]] = None,
    tier: Optional[str] = None,
    min_n_present: int = 1,
    engine_tier: Optional[str] = None,
    tier_disagrees: Optional[bool] = None,
) -> list:
    """The anchors a run's snapshot holds, filtered as the ledger reads filter,
    in m/z order."""
    query = select(BatchPeakRunAnchor).where(
        BatchPeakRunAnchor.batch_peak_run_id == run.batch_peak_run_id
    )
    if batch_peak_ids:
        query = query.where(BatchPeakRunAnchor.batch_peak_id.in_(list(batch_peak_ids)))
    if tier:
        query = query.where(BatchPeakRunAnchor.consensus_tier == tier)
    query = query.where(
        *engine_tier_filters(BatchPeakRunAnchor, engine_tier, tier_disagrees)
    )
    if min_n_present and min_n_present > 1:
        query = query.where(BatchPeakRunAnchor.n_present >= min_n_present)
    return (
        (await session.execute(query.order_by(BatchPeakRunAnchor.mz))).scalars().all()
    )


__all__ = [
    "ACTIONS",
    "ACTION_ASSIGN",
    "ACTION_BATCH_CHECKS",
    "ACTION_CO_VARIATION",
    "ACTION_FOLD",
    "ACTION_ISOTOPOLOGUES",
    "ACTION_IMPORT",
    "ACTION_REBUILD",
    "ACTION_SEARCH",
    "ACTION_SERIES",
    "ACTION_STRIP_ISOTOPOLOGUES",
    "BATCH_RUN_KEEP",
    "BatchRunInFlightException",
    "MEMBER_FIELDS",
    "NOT_A_PASS",
    "RUN_IN_FLIGHT_CODE",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "anchor_snapshot_row",
    "cancel_requested",
    "cancel_run",
    "check_no_run_in_flight",
    "close_shielded",
    "complete_run",
    "current_run",
    "engine_tier_filters",
    "ensure_current_run",
    "fail_run",
    "in_batch_run",
    "list_batch_runs",
    "member_arrays",
    "request_cancel",
    "resolve_run",
    "run_record",
    "running_run",
    "runs_to_prune",
    "snapshot_anchor_meta",
    "snapshot_rows",
    "snapshot_series",
    "start_run",
    "touch_ledger",
]
