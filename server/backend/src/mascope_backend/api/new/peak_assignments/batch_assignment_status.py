"""The batch's assignment status: where the batch stands, and why it is behind.

The app's *Assign batch* control says where a batch stands before it offers
anything (``docs/dev/batch_assignment_one_action.md``, 3.1 and 5.3), and two
users or two tabs have to see the same thing. So the answer is the server's,
read in one place (:func:`get_batch_assignment_status`), from what the batch
runs and the stage rows record:

- ``state``: ``running`` while a batch run or a stage runs; ``failed`` or
  ``cancelled`` where the batch's newest pass stopped; ``none`` with no batch
  peaks; ``folded_only`` where no pass has completed, so the ledger is what
  the folds at ingest built; ``behind`` where a reason below holds; else
  ``current``.
- ``behind``: the reasons, each measured against the newest completed pass
  (``batch_stages.last_pass``), which recorded the samples it covered, the
  library it read and the engine it ran under (MAS-1011):

  - ``new_samples`` and ``removed_samples``: the batch's samples against the
    pass's;
  - ``resampled``: samples of the pass whose newest in-app run read older
    batch decisions than the current ones. A run does not record the
    decisions it read yet (MAS-985), so this counts the samples with an
    in-app run completed after the pass started;
  - ``library_changed``: a target collection attached, detached or edited, a
    reference list loaded or dropped, against the pass's record;
  - ``settings_changed``: the batch's assignment settings
    (``SampleBatch.assignment_settings``) hash otherwise than the settings
    the pass ran on, whether they were saved since or the pass ran on a
    step's own settings in their place (MAS-1014);
  - ``engine_changed``: the engine version is not the pass's;
  - ``stages_stale``: a stage stale for a reason the others do not name.

- ``needs_fold``: whether the next pass folds every sample first, by the
  pass's own rule (``batch_stages.fold_reasons``), and which conditions hold.

A curation, a verdict and an import are not reasons: each acts on the
current run by design, and an import is a run of its own. A pin or its
release marks every stage stale (``curation``), and the consensus it
recomputes can mark some again (``consensus``); an import marks them
``import``. So a stage stale for one of those, or for a consensus recompute
after the pass, is no reason; a fold's mark is one only where no sample
reason explains it. ``stale_stages`` still lists every stale stage: the next
pass starts at the first.

The read costs one query per kind of thing it reads, never one per sample,
so a batch of thousands of samples answers at once.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func, select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.new.peak_assignments.batch_runs import (
    NOT_A_PASS,
    PROGRESS_KEY,
    STATUS_CANCELLED,
    STATUS_FAILED,
    current_run,
    running_run,
)
from mascope_backend.api.new.peak_assignments.batch_stages import (
    REASON_CONSENSUS,
    REASON_CURATION,
    REASON_FOLD,
    REASON_IMPORT,
    STAGES,
    STATE_RUNNING,
    STATE_STALE,
    batch_samples,
    batch_settings,
    fold_reasons,
    last_pass,
    library_read,
    settings_moved,
    stage_states,
    standing_entries,
)
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    PEAK_ASSIGNMENT_ENGINE_VERSION,
    settings_hash,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakRun,
    PeakAssignmentRun,
    SampleItem,
    User,
    async_session,
)


#: Where a batch stands.
STATE_NONE = "none"
STATE_FOLDED_ONLY = "folded_only"
STATE_IS_RUNNING = "running"
STATE_CURRENT = "current"
STATE_BEHIND = "behind"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATES = (
    STATE_NONE,
    STATE_FOLDED_ONLY,
    STATE_IS_RUNNING,
    STATE_CURRENT,
    STATE_BEHIND,
    STATE_FAILED,
    STATE_CANCELLED,
)

#: Why a batch is behind its last pass.
BEHIND_NEW_SAMPLES = "new_samples"
BEHIND_REMOVED_SAMPLES = "removed_samples"
BEHIND_RESAMPLED = "resampled"
BEHIND_LIBRARY_CHANGED = "library_changed"
BEHIND_SETTINGS_CHANGED = "settings_changed"
BEHIND_ENGINE_CHANGED = "engine_changed"
BEHIND_STAGES_STALE = "stages_stale"
#: The reasons the batch's samples give.
SAMPLE_REASONS = frozenset(
    {BEHIND_NEW_SAMPLES, BEHIND_REMOVED_SAMPLES, BEHIND_RESAMPLED}
)

#: The stale marks that are never a reason: a person's pin or its release,
#: and an engine's result imported, each of which acts on the current run.
NOT_A_REASON = frozenset({REASON_CURATION, REASON_IMPORT})

#: At most this many sample ids are listed under a sample reason; its
#: ``count`` is the whole.
SAMPLE_IDS_CAP = 100


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


# --- pure parts -------------------------------------------------------------------


def sample_reasons(
    covered: Iterable[str], now: Iterable[str], resampled: Iterable[str]
) -> list[dict]:
    """What the batch's samples say against the samples a pass covered.

    :param covered: The samples the pass covered.
    :param now: The batch's samples now.
    :param resampled: The samples with an in-app run completed after the pass
        started; a sample new since the pass is new, not resampled.
    :return: ``new_samples``, ``removed_samples`` and ``resampled``, each
        where it holds, with its count and the first sample ids.
    """
    covered, now = set(covered), set(now)
    groups = [
        (BEHIND_NEW_SAMPLES, now - covered),
        (BEHIND_REMOVED_SAMPLES, covered - now),
        (BEHIND_RESAMPLED, (set(resampled) & covered) & now),
    ]
    return [
        {
            "reason": reason,
            "count": len(ids),
            "sample_item_ids": sorted(ids)[:SAMPLE_IDS_CAP],
        }
        for reason, ids in groups
        if ids
    ]


def _changed_keys(was: Any, now: Any) -> dict[str, list[str]]:
    was = was if isinstance(was, dict) else {}
    now = now if isinstance(now, dict) else {}
    return {
        "added": sorted(set(now) - set(was)),
        "removed": sorted(set(was) - set(now)),
        "changed": sorted(k for k in set(was) & set(now) if was[k] != now[k]),
    }


def library_reason(read: Any, library: dict) -> Optional[dict]:
    """``library_changed`` where the library reads otherwise than the pass
    recorded it (``batch_stages.library_read``): which target collections
    were attached, detached or edited, which reference lists were loaded,
    dropped or reloaded. None where it reads the same, or where the pass
    recorded none.

    The test is the fold rule's own (``batch_stages.fold_reasons``): the two
    records compared whole.
    """
    if not isinstance(read, dict) or read == library:
        return None
    collections = _changed_keys(
        read.get("target_collections"), library.get("target_collections")
    )
    lists = _changed_keys(read.get("reference_lists"), library.get("reference_lists"))
    count = sum(len(ids) for part in (collections, lists) for ids in part.values())
    return {
        "reason": BEHIND_LIBRARY_CHANGED,
        "count": count,
        "detail": {
            "target_collections": {
                "attached": collections["added"],
                "detached": collections["removed"],
                "edited": collections["changed"],
            },
            "reference_lists": lists,
        },
    }


def engine_reason(
    pass_version: Optional[str], engine_version: str = PEAK_ASSIGNMENT_ENGINE_VERSION
) -> Optional[dict]:
    """``engine_changed`` where the pass ran under another engine version."""
    if pass_version in (None, engine_version):
        return None
    return {
        "reason": BEHIND_ENGINE_CHANGED,
        "count": 1,
        "detail": {"pass": pass_version, "now": engine_version},
    }


def settings_reason(last: Optional[dict], hash_now: str) -> Optional[dict]:
    """``settings_changed`` where the batch's settings hash otherwise than
    the settings the pass ran on: the test the next pass plans by
    (``batch_stages.settings_moved``), so the status and the pass agree.
    None for a pass that recorded no hash (one from before the batch kept
    settings)."""
    if not settings_moved(last, hash_now):
        return None
    return {
        "reason": BEHIND_SETTINGS_CHANGED,
        "count": 1,
        "detail": {"pass": last.get("settings_hash"), "now": hash_now},
    }


def stale_reason(
    rows: dict[str, Any], pass_ended: Optional[datetime], samples_explain: bool
) -> Optional[dict]:
    """``stages_stale`` where a stage is stale for a reason no other names.

    - A curation's and an import's marks are no reason (:data:`NOT_A_REASON`).
    - A consensus recompute's mark made after the pass ended is the mark of
      a write after it: a fold, which the samples' reasons name, or a
      curation or an import, which are none. One made before is the pass's
      own leftover, and stands.
    - A fold's mark is a reason only where no sample reason holds.
    - Any other mark is one: a rebuild, an earlier stage run again, a stage
      that stopped partway.

    :param rows: Stage name -> its row.
    :param pass_ended: When the last completed pass ended.
    :param samples_explain: Whether a sample reason holds.
    """
    stages = []
    for spec in STAGES:
        row = rows.get(spec.stage.value)
        if row is None or row.state != STATE_STALE:
            continue
        reason = row.stale_reason
        if reason in NOT_A_REASON:
            continue
        if reason == REASON_FOLD and samples_explain:
            continue
        if (
            reason == REASON_CONSENSUS
            and pass_ended is not None
            and row.utc_stale is not None
            and row.utc_stale > pass_ended
        ):
            continue
        stages.append({"stage": spec.stage.value, "stale_reason": reason})
    if not stages:
        return None
    return {"reason": BEHIND_STAGES_STALE, "count": len(stages), "detail": stages}


def state_of(
    *,
    running: bool,
    stopped: Optional[str],
    n_batch_peaks: int,
    has_pass: bool,
    behind: list[dict],
) -> str:
    """The batch's state, the first that holds: running, the newest pass
    stopped (``failed``, ``cancelled``), no batch peaks, no completed pass,
    behind, current."""
    if running:
        return STATE_IS_RUNNING
    if stopped in (STATUS_FAILED, STATUS_CANCELLED):
        return STATE_FAILED if stopped == STATUS_FAILED else STATE_CANCELLED
    if not n_batch_peaks:
        return STATE_NONE
    if not has_pass:
        return STATE_FOLDED_ONLY
    return STATE_BEHIND if behind else STATE_CURRENT


def _user(users: dict[int, str], user_id: Optional[int]) -> Optional[dict]:
    if user_id is None:
        return None
    return {"id": user_id, "username": users.get(user_id)}


def _stopped_at(summary: Any) -> Optional[str]:
    """The stage a stopped pass stopped in: the first entry that failed or
    was cancelled."""
    stages = summary.get("stages") if isinstance(summary, dict) else None
    for entry in stages if isinstance(stages, list) else []:
        if isinstance(entry, dict) and entry.get("status") in (
            STATUS_FAILED,
            STATUS_CANCELLED,
        ):
            return entry.get("stage")
    return None


def running_record(
    run: Optional[Any], stage_row: Optional[Any], users: dict[int, str]
) -> Optional[dict]:
    """The pass or batch run in flight, as the status serves it: its run, the
    process its progress goes out as, who started it and when, and the stage
    it is in, which of how many, and how far through its stages it is.

    A pass keeps that under :data:`PROGRESS_KEY` in its summary as each stage
    starts; a rebuild or an import on its own keeps none, and a stage row
    running with no run (never so since MAS-1011, but the routes still refuse
    on it) gives its stage alone.
    """
    if run is None and stage_row is None:
        return None
    progress = {}
    if run is not None and isinstance(run.summary, dict):
        progress = run.summary.get(PROGRESS_KEY) or {}
    stage = progress.get("stage")
    if stage is None and stage_row is not None:
        stage = stage_row.stage
    index, n_stages = progress.get("stage_index"), progress.get("n_stages")
    spec = next((s for s in STAGES if s.stage.value == stage), None)
    return {
        "batch_peak_run_id": run.batch_peak_run_id if run is not None else None,
        "action": run.action if run is not None else None,
        "process_id": progress.get("process_id"),
        "started_by": _user(users, run.created_by if run is not None else None),
        "utc_started": _iso(
            run.batch_peak_run_utc_created if run is not None else stage_row.utc_started
        ),
        "stage": stage,
        "label": progress.get("label") or (spec.label if spec is not None else None),
        "stage_index": index,
        "n_stages": n_stages,
        "utc_stage_started": progress.get("utc_stage_started"),
        "progress": (index / n_stages if isinstance(index, int) and n_stages else None),
        "cancel_requested_utc": _iso(
            run.cancel_requested_utc if run is not None else None
        ),
    }


# --- the read ---------------------------------------------------------------------


async def _newest_pass(session, sample_batch_id: str) -> Optional[BatchPeakRun]:
    """The batch's newest pass, whatever its status: where it failed or was
    cancelled, the batch stands there until a pass completes after it."""
    return (
        (
            await session.execute(
                select(BatchPeakRun)
                .where(
                    BatchPeakRun.sample_batch_id == sample_batch_id,
                    BatchPeakRun.action.not_in(sorted(NOT_A_PASS)),
                )
                .order_by(BatchPeakRun.batch_peak_run_utc_created.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _resampled(session, sample_batch_id: str, since: datetime) -> list[str]:
    """The batch's samples with an in-app run of their own completed since
    ``since``: one query, whatever the batch's size. A decoy run is no run of
    the sample's (it folds nothing)."""
    return list(
        (
            await session.execute(
                select(PeakAssignmentRun.sample_item_id)
                .join(
                    SampleItem,
                    SampleItem.sample_item_id == PeakAssignmentRun.sample_item_id,
                )
                .where(
                    SampleItem.sample_batch_id == sample_batch_id,
                    PeakAssignmentRun.engine == IN_APP_ENGINE,
                    PeakAssignmentRun.status == "completed",
                    PeakAssignmentRun.decoy.is_(None),
                    PeakAssignmentRun.peak_assignment_run_utc_completed > since,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )


async def _usernames(session, user_ids: Iterable[Optional[int]]) -> dict[int, str]:
    ids = sorted({i for i in user_ids if i is not None})
    if not ids:
        return {}
    rows = (
        await session.execute(select(User.id, User.username).where(User.id.in_(ids)))
    ).all()
    return {row.id: row.username for row in rows}


@api_controller()
async def get_batch_assignment_status(sample_batch_id: str) -> dict:
    """Where a batch's assignment stands, and why it is behind its last pass.

    :param sample_batch_id: The batch.
    :return: Status envelope; ``data`` holds ``state``, ``last_pass``,
        ``stopped_pass``, ``running``, ``stages``, ``stale_stages``,
        ``needs_fold``, ``fold_reasons`` and ``behind``.
    """
    async with async_session() as session:
        n_batch_peaks = (
            await session.execute(
                select(func.count())
                .select_from(BatchPeak)
                .where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        ).scalar_one()
        current = await current_run(session, sample_batch_id)
        running = await running_run(session, sample_batch_id)
        rows = await stage_states(session, sample_batch_id)
        last = await last_pass(session, sample_batch_id)
        newest = await _newest_pass(session, sample_batch_id)
        library = await library_read(session, sample_batch_id)
        samples = await batch_samples(session, sample_batch_id)
        hash_now = settings_hash(await batch_settings(session, sample_batch_id))
        last_run = (
            await session.get(BatchPeakRun, last["batch_peak_run_id"])
            if last is not None
            else None
        )
        resampled = (
            await _resampled(
                session, sample_batch_id, last_run.batch_peak_run_utc_created
            )
            if last_run is not None
            else []
        )
        users = await _usernames(
            session,
            [r.created_by for r in (running, newest, last_run) if r is not None],
        )

    reasons = fold_reasons(
        n_batch_peaks=n_batch_peaks,
        current_action=current.action if current is not None else None,
        last_pass=last,
        library=library,
    )
    behind: list[dict] = []
    if last is not None:
        behind += sample_reasons(last.get("samples") or [], samples, resampled)
        for reason in (
            library_reason(last.get("library"), library),
            settings_reason(last, hash_now),
            engine_reason(last.get("engine_version")),
        ):
            if reason is not None:
                behind.append(reason)
        stale = stale_reason(
            rows,
            last_run.batch_peak_run_utc_completed,
            any(r["reason"] in SAMPLE_REASONS for r in behind),
        )
        if stale is not None:
            behind.append(stale)

    # The newest pass, where it stopped: newer than any that completed.
    stopped = (
        newest
        if newest is not None and newest.status in (STATUS_FAILED, STATUS_CANCELLED)
        else None
    )
    stage_row = next((r for r in rows.values() if r.state == STATE_RUNNING), None)
    entries = standing_entries(rows)
    state = state_of(
        running=running is not None or stage_row is not None,
        stopped=stopped.status if stopped is not None else None,
        n_batch_peaks=n_batch_peaks,
        has_pass=last is not None,
        behind=behind,
    )
    data = {
        "sample_batch_id": sample_batch_id,
        "state": state,
        "engine_version": PEAK_ASSIGNMENT_ENGINE_VERSION,
        "settings_hash": hash_now,
        "n_samples": len(samples),
        "n_batch_peaks": int(n_batch_peaks),
        "last_pass": (
            {
                "batch_peak_run_id": last_run.batch_peak_run_id,
                "action": last_run.action,
                "first_stage": (last_run.summary or {}).get("first_stage"),
                "utc_started": _iso(last_run.batch_peak_run_utc_created),
                "utc_completed": _iso(last_run.batch_peak_run_utc_completed),
                "started_by": _user(users, last_run.created_by),
                "engine_version": last_run.engine_version,
                # None for a pass from before the batch kept settings.
                "settings_hash": last.get("settings_hash"),
                "n_samples": len(last.get("samples") or []),
            }
            if last_run is not None
            else None
        ),
        "stopped_pass": (
            {
                "batch_peak_run_id": stopped.batch_peak_run_id,
                "action": stopped.action,
                "status": stopped.status,
                "first_stage": (stopped.summary or {}).get("first_stage"),
                "stopped_at": _stopped_at(stopped.summary),
                "error": stopped.error,
                "utc_started": _iso(stopped.batch_peak_run_utc_created),
                "utc_completed": _iso(stopped.batch_peak_run_utc_completed),
                "started_by": _user(users, stopped.created_by),
            }
            if stopped is not None
            else None
        ),
        "running": running_record(running, stage_row, users),
        "stages": entries,
        "stale_stages": [e["stage"] for e in entries if e["state"] == STATE_STALE],
        "needs_fold": bool(reasons),
        "fold_reasons": reasons,
        "behind": behind,
    }
    return {
        "status": "success",
        "message": f"The batch's assignment is {state.replace('_', ' ')}",
        "results": 1,
        "data": data,
    }


__all__ = [
    "BEHIND_ENGINE_CHANGED",
    "BEHIND_LIBRARY_CHANGED",
    "BEHIND_NEW_SAMPLES",
    "BEHIND_REMOVED_SAMPLES",
    "BEHIND_RESAMPLED",
    "BEHIND_SETTINGS_CHANGED",
    "BEHIND_STAGES_STALE",
    "NOT_A_REASON",
    "SAMPLE_IDS_CAP",
    "STATES",
    "engine_reason",
    "get_batch_assignment_status",
    "library_reason",
    "running_record",
    "settings_reason",
    "sample_reasons",
    "stale_reason",
    "state_of",
]
