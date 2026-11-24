"""The batch stages: the batch passes in one fixed order, with one staleness rule.

The batch ledger is read and rewritten by nine passes, each built as a step of
its own. Five of them rewrite members and four only record a reading, and each
reads what another wrote: the search skips what the source's claims took, the
series walks from what the search committed, the co-variation normalises by
the reagent ions the source's claims named, the ratio stability grades a drift
by the co-variation, the batch checks read the stamps and the co-variation.
Run in any order, they leave a ledger that depends on the order they were
clicked in (MAS-974, finding 1). So they run here as stages of one list
(:data:`STAGES`), in the order two rules give: a stage runs after every stage
whose records or claims it reads, and every stage that rewrites members runs
before every stage that only records a reading, since a record stands only
while its batch peak's consensus names the reading it was made for.

1. **Water ladders** (``batch_water_ladder.py``) and 2. **known species**
   (``batch_known_species.py``): the source's own ions, claimed where the
   batch decides them.
3. **Search** (``batch_untargeted.py``): the untargeted search of the batch
   peaks nothing has assigned.
4. **Isotope lines**: the rows that are another row's isotope line, stripped
   (``batch_merged_isotopologues.py``), then the diagnostic lines stamped
   (``batch_isotopologues.py``), on the line model.
5. **Series** (``batch_series.py``): the homologous series walked from what
   the stages before committed.
6. **Co-variation** (``batch_coherence.py``), 7. **ratio stability**
   (``continuity.py``), 8. **batch checks** (``batch_checks.py``) and 9.
   **probable blends** (the consensus of every batch peak recomputed): the
   readings, which move no member.

One entry runs them (:func:`assign_batch`, a *pass*): from a given stage to
the end, or from the first stage that is not current, whichever comes first.
Each per-stage route starts it at its own stage, or at an earlier one that is
not current, so a click on any of them leaves every stage after it current
too, and no stage runs on what an earlier one has not settled.

**A pass is one batch run** (MAS-1011): one ``start_run``, one snapshot, one
end. Each stage's steps write into the pass's run instead of opening their
own (``batch_runs.in_batch_run``), and the run's ``summary`` holds every
stage under ``stages`` with its status, start, end and outcome, beside what
the pass read: the samples it covered and the library (:func:`library_read`).
A pass started as one is action ``assign``; one a step's own route starts
keeps that step's action. A pass folds every sample first, today's rebuild,
when asked to (``fold="always"``) or when the ledger cannot be a starting
point (:func:`needs_fold`). It reports one ``assign_batch`` process, one
pending packet at each stage's start, each stage's own packets nested under
it, and a ``peak_assignment_reload`` after each stage that rewrites members.
A person's cancel (``batch_runs.request_cancel``) is read between stages and
between the search's samples: the pass ends ``cancelled``, keeps what it
wrote, and the stages it did not finish are stale.

**Staleness** (:data:`batch_peak_stage`, one row per batch and stage): a stage
is current while nothing it reads has moved since its last run finished, and
stale once something has:

- a fold (a sample's new run, an ingest), a rebuild, an engine's batch
  result imported, and a person's pin on a batch peak or its release rewrite
  members every stage reads, so each marks every stage stale
  (:func:`mark_stale` from the first);
- a stage that runs rewrites or records what the stages after it read, so its
  end marks every later stage stale until each runs again;
- a stage that stops partway, raised or cancelled, keeps what it wrote and is
  marked stale itself;
- a stage that could not read some of the batch's samples skipped them and is
  current with them: its row names them, and one whose file reads again makes
  it stale (``batch_skipped``, MAS-1060);
- a consensus recompute that leaves a record behind its batch peak's reading
  keeps the record, marked stale (``batch_peaks.compute_consensus``), and marks
  the stage that wrote it stale.

The ledger serves where each stage stands (:func:`stage_standing`).

**One pass at a time.** A pass holds a stage row ``running`` from its first
stage's start to its last stage's end: each later stage begins in the
transaction the stage before it ends in (:func:`hand_over`), so it holds one
also where it holds no batch run (judging samples again, stamping versions,
the ratio stability, the probable blends). Every step's route refuses while
one does (``batch_stages.check_no_run_in_flight``), and so does a pass's first
stage, which a second press can reach before the first pass's stage is
marked (:func:`begin_stage`). Every writer of the stage rows takes the
batch's fold lock first, as a fold does, so no two lock them in different
orders (:func:`_fold_lock`).

**Settings.** The search and the series take settings, and every stage that
rewrites members judges its samples again under their tier bands. They are
the batch's own (``SampleBatch.assignment_settings``, MAS-1014): a pass runs
every stage on them, the engine's defaults where the batch saved none
(:func:`batch_settings`). A step's own route given settings in its request
runs its pass on those instead, and the run records that it did
(``settings_source`` in its ``config``). A pass records the settings in its
run's ``config`` and their hash in its ``summary``; a pass run under other
settings than the last pass's starts at the first stage, as the batch's
assignment status says ``settings_changed`` until one has
(:func:`plan_pass`).

Every run of a stage counts up its version, and every record it writes carries
the version (``version`` in the record): a batch decision is a versioned
record, which a sample's run can name (MAS-985).

Design: ``docs/dev/batch_assignment_one_action.md``, section 4 (the order),
and ``docs/dev/assignment_quality_plan.md``, milestone 7 (MAS-983).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any, Optional

from fastapi import status
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    CodedHTTPException,
)
from mascope_backend.api.lib.utils import handle_reloads
from mascope_backend.api.new.peak_assignments.batch_checks import read_batch_checks
from mascope_backend.api.new.peak_assignments.batch_coherence import (
    read_batch_co_variation,
)
from mascope_backend.api.new.peak_assignments.batch_fold import rebuild_batch_ledger
from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
    stamp_batch_isotopologues,
)
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    decide_batch_known_species,
)
from mascope_backend.api.new.peak_assignments.batch_library import _iso, library_read
from mascope_backend.api.new.peak_assignments.batch_lock import _acquire_batch_fold_lock
from mascope_backend.api.new.peak_assignments.batch_merged_isotopologues import (
    strip_batch_isotopologue_rows,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import STAGE_VERSION
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_rejudge import (
    judged_message,
    rejudge_samples,
    samples_written,
    series_corroborated,
    species_restored,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_ASSIGN,
    ACTION_BATCH_CHECKS,
    ACTION_CO_VARIATION,
    ACTION_IMPORT,
    ACTION_ISOTOPOLOGUES,
    ACTION_KNOWN_SPECIES,
    ACTION_SEARCH,
    ACTION_SERIES,
    ACTION_STRIP_ISOTOPOLOGUES,
    ACTION_WATER_LADDERS,
    RUN_IN_FLIGHT_CODE,
    STATUS_COMPLETED,
    BatchRunInFlightException,
    cancel_requested,
    cancel_run,
    close_shielded,
    complete_run,
    current_run,
    fail_run,
    record_progress,
    running_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_series import detect_batch_series
from mascope_backend.api.new.peak_assignments.batch_skipped import (
    skipped_in,
    skipped_record,
    stage_rows,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import (
    REASON_INTERRUPTED,
    REASON_PARTIAL,
    REASON_UPSTREAM,
    STAGE_RECORD_KEYS,
    STATE_CURRENT,
    STATE_NOT_RUN,
    STATE_RUNNING,
    STATE_STALE,
    BatchStage,
    _fold_lock,
    _now,
    mark_stale,
    stage_states,
    stages_from,
)
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    search_batch_untargeted,
)
from mascope_backend.api.new.peak_assignments.batch_water_ladder import (
    measure_batch_water_ladders,
)
from mascope_backend.api.new.peak_assignments.config import (
    PEAK_ASSIGNMENT_ENGINE_VERSION,
    PeakAssignmentConfig,
    batch_settings_config,
    settings_hash,
)
from mascope_backend.api.new.peak_assignments.continuity import (
    recompute_batch_continuity,
)
from mascope_backend.api.new.peak_assignments.kept_measurement import KeptMeasurement
from mascope_backend.db import (
    BatchPeak,
    BatchPeakRun,
    BatchPeakStage,
    Sample,
    SampleBatch,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications.schemas import UserNotification
from mascope_backend.socket.notifications.service import send_progress_user_notification


#: The phases the app groups the stages under (``batch_assignment_one_action.md``,
#: 3.7): words of the app, not of the order.
PHASE_GATHER = "Gather samples"
PHASE_SOURCE = "Recognise source ions"
PHASE_COMPOSITIONS = "Find compositions"
PHASE_CROSS_CHECK = "Cross-check across the batch"


@dataclass(frozen=True)
class StageCall:
    """What one stage of a pass is run with.

    :param sample_batch_id: The batch.
    :param config: The pass's settings: the search's and the series' bands
        and ranges, and the tier bands every rewriting stage judges its
        samples again under. The batch's own, or those a step's route was
        given; None runs on the engine's defaults.
    :param user_id: Who started the pass.
    :param parent_id: The pass's process id, which every stage's progress
        nests under.
    :param batch_peak_run_id: The pass's batch run, which every step writes
        into and every record it writes names.
    :param cancelled: Whether a person asked the pass to stop; None where
        nobody can (``batch_runs.cancel_requested``).
    :param kept: What the pass measured of its samples' readings, which each
        judging of a sample again scores only where it has not measured one
        at the same inputs (``kept_measurement``).
    """

    sample_batch_id: str
    config: Optional[PeakAssignmentConfig]
    user_id: Optional[int]
    parent_id: Optional[str]
    batch_peak_run_id: Optional[str] = None
    cancelled: Optional[Callable[[], Awaitable[bool]]] = None
    kept: KeptMeasurement = field(default_factory=KeptMeasurement)


@dataclass(frozen=True)
class StageSpec:
    """One stage of the list.

    :param stage: Its name.
    :param label: What the app calls the step that runs it.
    :param phase: The app's phase it belongs to.
    :param actions: The batch run actions it records itself as, in order.
    :param record_keys: The batch peak provenance keys of the records it
        writes, each stamped with the run's version.
    :param rewrites: Whether it rewrites members, which a sample's judging
        then reads again.
    :param run: Runs it; its answer is the outcome of each step it ran.
    :param takes_settings: Whether its steps run on the pass's settings
        (the search's, the series'), which its entry in the pass's summary
        then records.
    """

    stage: BatchStage
    label: str
    phase: str
    actions: tuple[str, ...]
    record_keys: tuple[str, ...]
    rewrites: bool
    run: Callable[[StageCall], Awaitable[list[dict]]]
    takes_settings: bool = False


class PassCancelled(BaseException):
    """A person asked the pass to stop, and it stops here.

    A BaseException, as a cancellation is: it has to pass the ``except
    Exception`` arms that keep one bad sample from stopping a step, and the
    background-task decorator, which would report it as the step's failure.
    The pass catches it (:func:`assign_batch`)."""


async def stop_if_cancelled(call: StageCall) -> None:
    """Raise :class:`PassCancelled` where a person asked the pass to stop: the
    search's check between two samples."""
    if call.cancelled is not None and await call.cancelled():
        raise PassCancelled()


def _dependent(call: StageCall) -> dict:
    """The keyword arguments a stage's controller runs under inside a pass:
    a dependent task whose packets nest under the pass's process, writing
    into the pass's run."""
    return {
        "sample_batch_id": call.sample_batch_id,
        "independent_transaction": False,
        "user_id": call.user_id,
        "process_id": gen_id(8),
        "parent_id": call.parent_id,
        "batch_peak_run_id": call.batch_peak_run_id,
    }


# The stages' controllers are imported where they are called: each of their
# modules imports the fold, which marks the stages stale through this module.


async def _run_water_ladders(call: StageCall) -> list[dict]:
    return [await measure_batch_water_ladders(**_dependent(call))]


async def _run_known_species(call: StageCall) -> list[dict]:
    return [await decide_batch_known_species(**_dependent(call))]


async def _run_search(call: StageCall) -> list[dict]:
    return [
        await search_batch_untargeted(
            config=call.config,
            stop=lambda: stop_if_cancelled(call),
            **_dependent(call),
        )
    ]


async def _run_isotope_lines(call: StageCall) -> list[dict]:
    """The rows that are another row's isotope line, stripped to it, then the
    diagnostic lines of what stands stamped: a stripped row is no parent of
    lines of its own, and a stamp reads the parents the strip left."""

    return [
        await strip_batch_isotopologue_rows(**_dependent(call)),
        await stamp_batch_isotopologues(**_dependent(call)),
    ]


async def _run_series(call: StageCall) -> list[dict]:
    return [await detect_batch_series(config=call.config, **_dependent(call))]


async def _run_co_variation(call: StageCall) -> list[dict]:
    return [await read_batch_co_variation(**_dependent(call))]


async def _run_continuity(call: StageCall) -> list[dict]:
    # It opens no batch run and records nothing that names one.
    kwargs = _dependent(call)
    kwargs.pop("batch_peak_run_id")
    return [await recompute_batch_continuity(**kwargs)]


async def _run_batch_checks(call: StageCall) -> list[dict]:
    return [await read_batch_checks(**_dependent(call))]


async def _run_blends(call: StageCall) -> list[dict]:
    """The probable blends: the consensus of every batch peak recomputed, so
    each reads the members the stages before left it (``batch_blend.py``)."""

    async with async_session() as session:
        ids = set(
            (
                await session.execute(
                    select(BatchPeak.batch_peak_id).where(
                        BatchPeak.sample_batch_id == call.sample_batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
    await recompute_batch_consensus(call.sample_batch_id, ids)
    return [
        {
            "status": "success",
            "message": f"Read the consensus of {len(ids)} batch peaks again.",
            "data": {"anchors": len(ids)},
        }
    ]


#: The stages, in order.
STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        BatchStage.WATER_LADDERS,
        "Measure water ladders",
        PHASE_SOURCE,
        (ACTION_WATER_LADDERS,),
        STAGE_RECORD_KEYS[BatchStage.WATER_LADDERS],
        True,
        _run_water_ladders,
    ),
    StageSpec(
        BatchStage.KNOWN_SPECIES,
        "Decide known species",
        PHASE_SOURCE,
        (ACTION_KNOWN_SPECIES,),
        STAGE_RECORD_KEYS[BatchStage.KNOWN_SPECIES],
        True,
        _run_known_species,
    ),
    StageSpec(
        BatchStage.SEARCH,
        "Search untargeted",
        PHASE_COMPOSITIONS,
        (ACTION_SEARCH,),
        STAGE_RECORD_KEYS[BatchStage.SEARCH],
        True,
        _run_search,
        takes_settings=True,
    ),
    StageSpec(
        BatchStage.ISOTOPE_LINES,
        "Isotope lines",
        PHASE_COMPOSITIONS,
        (ACTION_STRIP_ISOTOPOLOGUES, ACTION_ISOTOPOLOGUES),
        STAGE_RECORD_KEYS[BatchStage.ISOTOPE_LINES],
        True,
        _run_isotope_lines,
    ),
    StageSpec(
        BatchStage.SERIES,
        "Detect series",
        PHASE_COMPOSITIONS,
        (ACTION_SERIES,),
        STAGE_RECORD_KEYS[BatchStage.SERIES],
        True,
        _run_series,
        takes_settings=True,
    ),
    StageSpec(
        BatchStage.CO_VARIATION,
        "Read co-variation",
        PHASE_CROSS_CHECK,
        (ACTION_CO_VARIATION,),
        STAGE_RECORD_KEYS[BatchStage.CO_VARIATION],
        False,
        _run_co_variation,
    ),
    StageSpec(
        BatchStage.CONTINUITY,
        "Ratio stability",
        PHASE_CROSS_CHECK,
        (),
        STAGE_RECORD_KEYS[BatchStage.CONTINUITY],
        False,
        _run_continuity,
    ),
    StageSpec(
        BatchStage.BATCH_CHECKS,
        "Read batch checks",
        PHASE_CROSS_CHECK,
        (ACTION_BATCH_CHECKS,),
        STAGE_RECORD_KEYS[BatchStage.BATCH_CHECKS],
        False,
        _run_batch_checks,
    ),
    StageSpec(
        BatchStage.BLENDS,
        "Probable blends",
        PHASE_CROSS_CHECK,
        (),
        STAGE_RECORD_KEYS[BatchStage.BLENDS],
        False,
        _run_blends,
    ),
)

#: Each stage's spec, by name.
STAGE_BY_NAME: dict[BatchStage, StageSpec] = {spec.stage: spec for spec in STAGES}


def stages_after(stage: BatchStage | str) -> tuple[BatchStage, ...]:
    """The stages after ``stage``, in order."""
    return stages_from(stage)[1:]


def first_not_current(states: dict[str, str]) -> Optional[BatchStage]:
    """The first stage, in order, that is not current: stale, running, or
    never run on the batch.

    :param states: Stage name -> the state its row holds; a stage with no row
        is absent.
    :return: None where every stage is current.
    """
    for spec in STAGES:
        if states.get(spec.stage) != STATE_CURRENT:
            return spec.stage
    return None


def plan(states: dict[str, str], from_stage: BatchStage | str | None) -> tuple:
    """The stages a pass runs: from the first that is not current, or from
    ``from_stage`` where every stage before it is current; none where no stage
    is named and every stage is current.

    A stage never runs on what an earlier stage has not settled: a stage
    reads what the stages before it wrote, and one that ran before them - a
    claim made before the claims it yields to, a reading made before the
    claims it reads - leaves a ledger that depends on the order the steps
    were pressed in (MAS-983's Verify). So a step pressed while an earlier
    stage is stale, or has never run on the batch, starts the pass there.
    """
    first = first_not_current(states)
    if from_stage is None:
        return () if first is None else stages_from(first)
    asked = BatchStage(from_stage)
    order = [spec.stage for spec in STAGES]
    if first is not None and order.index(first) < order.index(asked):
        return stages_from(first)
    return stages_from(asked)


def upstream_reason(stage: BatchStage | str) -> str:
    """The stale reason a stage's run leaves on the stages after it."""
    return f"{REASON_UPSTREAM}:{BatchStage(stage).value}"


def with_version(record: Any, run_ids: set[str], version: int) -> Optional[dict]:
    """The record stamped with its stage's version, where one of the stage's
    runs ``run_ids`` wrote it; None where it is not one of them or already
    carries the version."""
    if not isinstance(record, dict):
        return None
    if record.get("batch_peak_run_id") not in run_ids:
        return None
    if record.get("version") == version:
        return None
    return {**record, "version": version}


class BatchStageRunningException(CodedHTTPException):
    """A pass of the batch stages is running on this batch (409).

    A pass holds a stage row ``running`` from its first stage's start to its
    last stage's end, also where it holds no batch run: while it judges
    samples again, stamps versions, or runs the ratio stability and the
    probable blends. Each step's route, the pass's own, a rebuild and an
    import refuse while it does (``batch_stages.check_no_run_in_flight``), and
    so does a pass's first stage (:func:`begin_stage`).
    """

    error_code = RUN_IN_FLIGHT_CODE

    def __init__(self, sample_batch_id: str, running: Any):
        spec = STAGE_BY_NAME.get(running.stage)
        label = spec.label if spec is not None else running.stage
        started = (
            f", started {running.utc_started:%Y-%m-%d %H:%M} UTC"
            if running.utc_started is not None
            else ""
        )
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The batch stages are running on this batch ({label}{started}); "
                "wait for them to finish"
            ),
        )


async def running_stage(session, sample_batch_id: str) -> Optional[BatchPeakStage]:
    """The batch's stage a pass is running, if one is."""
    return (
        (
            await session.execute(
                select(BatchPeakStage).where(
                    BatchPeakStage.sample_batch_id == sample_batch_id,
                    BatchPeakStage.state == STATE_RUNNING,
                )
            )
        )
        .scalars()
        .first()
    )


async def _begin(session, sample_batch_id: str, stage: BatchStage) -> tuple:
    """A stage's row written as running, its version counted up, on the
    caller's session under its fold lock."""
    started = _now()
    statement = pg_insert(BatchPeakStage).values(
        sample_batch_id=sample_batch_id,
        stage=stage.value,
        version=1,
        state=STATE_RUNNING,
        utc_started=started,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[BatchPeakStage.sample_batch_id, BatchPeakStage.stage],
        set_={
            "version": BatchPeakStage.version + 1,
            "state": STATE_RUNNING,
            "utc_started": started,
        },
    ).returning(BatchPeakStage.version)
    version = (await session.execute(statement)).scalar_one()
    return version, started


async def begin_stage(sample_batch_id: str, stage: BatchStage) -> tuple[int, datetime]:
    """Record a stage's run as started: its version counted up, its state
    running. Refused while another of the batch's stages runs: a pass's first
    stage, started by a second press that came in before the first pass's
    own stage was marked (the routes' refusal reads the same rows, and a
    request can reach it in between). Each later stage of a pass begins as
    the one before it ends (:func:`hand_over`), so a pass holds a running
    stage from its start to its end.

    :return: The run's version, and when it started.
    :raises BatchStageRunningException: Another stage of the batch runs.
    """
    async with async_session() as session:
        await _fold_lock(session, sample_batch_id)
        running = await running_stage(session, sample_batch_id)
        if running is not None:
            raise BatchStageRunningException(sample_batch_id, running)
        begun = await _begin(session, sample_batch_id, stage)
        await session.commit()
    return begun


async def hand_over(
    sample_batch_id: str,
    stage: BatchStage,
    started: datetime,
    *,
    run_id: Optional[str],
    stale_reason: Optional[str] = None,
    then: Optional[BatchStage] = None,
    skipped: Optional[list[dict]] = None,
) -> tuple[str, Optional[tuple[int, datetime]]]:
    """Record a stage's run as finished, mark every stage after it stale, and
    begin ``then``, the pass's next stage, in the same transaction: between
    two stages of a pass no moment passes with none of them running, so no
    second pass is admitted there.

    It is current unless something marked it stale after it started (a fold
    that landed while it ran) or the run itself stopped partway
    (``stale_reason``). ``skipped`` is what it records of the samples it
    could not read (``batch_skipped.skipped_record``): none clears it.

    :return: The state it ends in, and ``then``'s version and start where it
        began.
    """
    now = _now()
    async with async_session() as session:
        await _fold_lock(session, sample_batch_id)
        row = (
            await session.execute(
                select(BatchPeakStage)
                .where(
                    BatchPeakStage.sample_batch_id == sample_batch_id,
                    BatchPeakStage.stage == stage.value,
                )
                .with_for_update()
            )
        ).scalar_one()
        moved = row.utc_stale is not None and row.utc_stale >= started
        row.utc_ran = now
        row.batch_peak_run_id = run_id
        row.skipped_samples = skipped
        if stale_reason is not None:
            row.state = STATE_STALE
            row.stale_reason = stale_reason
            row.utc_stale = now
        elif moved:
            row.state = STATE_STALE
        else:
            row.state = STATE_CURRENT
            row.stale_reason = None
            row.utc_stale = None
        state = row.state
        # ``then`` runs next, on what this stage wrote: it is begun, not
        # marked; the stages after it read what it will write.
        await mark_stale(
            session,
            sample_batch_id,
            [later for later in stages_after(stage) if later != then],
            upstream_reason(stage),
            at=now,
        )
        await session.flush()
        begun = await _begin(session, sample_batch_id, then) if then else None
        await session.commit()
    return state, begun


async def finish_stage(
    sample_batch_id: str,
    stage: BatchStage,
    started: datetime,
    *,
    run_id: Optional[str],
    stale_reason: Optional[str] = None,
) -> str:
    """Record a stage's run as finished, and mark every stage after it stale
    (:func:`hand_over`, with no stage to begin next).

    :return: The state it ends in.
    """
    state, _ = await hand_over(
        sample_batch_id, stage, started, run_id=run_id, stale_reason=stale_reason
    )
    return state


async def stop_stage(
    sample_batch_id: str, stage: BatchStage, *, then: Optional[BatchStage] = None
) -> None:
    """Record a stage's run as stopped partway: what it wrote stands, and it is
    stale until it runs again. The stages after it read what it left, so they
    are stale too. ``then``, the pass's next stage, is stopped as well where
    the hand-over to it committed before the interruption: it began, and
    nothing will end it. Shielded: it is written while a cancellation
    unwinds."""

    async def write() -> None:
        now = _now()
        async with async_session() as session:
            await _fold_lock(session, sample_batch_id)
            await mark_stale(
                session,
                sample_batch_id,
                stages_after(stage),
                upstream_reason(stage),
                at=now,
            )
            stopped = [stage.value] + ([then.value] if then is not None else [])
            await session.execute(
                update(BatchPeakStage)
                .where(
                    BatchPeakStage.sample_batch_id == sample_batch_id,
                    BatchPeakStage.stage.in_(stopped),
                    # ``then`` only where it began: else the upstream mark
                    # above says why it is stale.
                    (BatchPeakStage.stage == stage.value)
                    | (BatchPeakStage.state == STATE_RUNNING),
                )
                .values(
                    state=STATE_STALE, stale_reason=REASON_INTERRUPTED, utc_stale=now
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    try:
        await asyncio.shield(write())
    except Exception as exc:  # noqa: BLE001 - never replace what interrupted it
        runtime.logger.warning(
            f"Could not mark batch stage '{stage.value}' of batch "
            f"'{sample_batch_id}' as stopped: {exc}"
        )


def standing_entries(rows: dict[str, Any]) -> list[dict]:
    """Where each stage stands, in order, as the ledger serves it.

    :param rows: Stage name -> its row (``BatchPeakStage`` or alike).
    """
    entries = []
    for spec in STAGES:
        row = rows.get(spec.stage)
        entries.append(
            {
                "stage": spec.stage.value,
                "label": spec.label,
                "phase": spec.phase,
                "state": row.state if row is not None else STATE_NOT_RUN,
                "version": row.version if row is not None else 0,
                "stale_reason": row.stale_reason if row is not None else None,
                "utc_ran": _iso(row.utc_ran) if row is not None else None,
                "utc_stale": _iso(row.utc_stale) if row is not None else None,
                # What it could not read and skipped (``batch_skipped``).
                "skipped_samples": list(row.skipped_samples or []) if row else [],
            }
        )
    return entries


async def stage_standing(sample_batch_id: str) -> dict:
    """Where a batch's stages stand: every stage in order with its state, and
    the stale ones by name, the first of which a pass re-runs from."""
    async with async_session() as session:
        entries = standing_entries(await stage_states(session, sample_batch_id))
    return {
        "stages": entries,
        "stale_stages": [e["stage"] for e in entries if e["state"] == STATE_STALE],
    }


# --- versions ---------------------------------------------------------------------


async def stamp_versions(
    sample_batch_id: str, keys: tuple[str, ...], run_ids: list[str], version: int
) -> int:
    """Stamp the records a stage's run wrote with the run's version, under the
    fold lock: a record names the run that wrote it, and the version says how
    many runs of its stage came before.

    :return: How many records were stamped.
    """

    if not keys or not run_ids:
        return 0
    runs = set(run_ids)
    stamped = 0
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.provenance.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for anchor in anchors:
            provenance = anchor.provenance
            if not isinstance(provenance, dict):
                continue
            changed = {}
            for key in keys:
                record = with_version(provenance.get(key), runs, version)
                if record is not None:
                    changed[key] = record
            if changed:
                anchor.provenance = {**provenance, **changed}
                stamped += len(changed)
        await session.commit()
    return stamped


# --- the pass ---------------------------------------------------------------------


#: Where a pass's settings came from: the batch's own, or the request that
#: started a step's own route, which overrides them for that pass.
SETTINGS_FROM_BATCH = "batch"
SETTINGS_FROM_REQUEST = "request"


def pass_config(
    from_stage: Optional[str],
    fold: str,
    config: Optional[PeakAssignmentConfig],
    settings_source: str = SETTINGS_FROM_BATCH,
) -> dict:
    """What a pass records it was asked for: where to start, whether to fold,
    the settings it runs on, and where they came from (``batch``, or
    ``request`` where a step's route was given settings that override the
    batch's)."""
    return {
        "from_stage": from_stage,
        "fold": fold,
        "settings": (config or PeakAssignmentConfig()).model_dump(
            mode="json", exclude={"decoy"}
        ),
        "settings_source": settings_source,
    }


async def batch_settings(session, sample_batch_id: str) -> PeakAssignmentConfig:
    """The batch's own assignment settings, as a run config: the engine's
    defaults where it saved none, or where what it saved no longer reads as
    settings (a field since removed or bounded tighter), which is logged."""
    stored = (
        await session.execute(
            select(SampleBatch.assignment_settings).where(
                SampleBatch.sample_batch_id == sample_batch_id
            )
        )
    ).scalar_one_or_none()
    try:
        return batch_settings_config(stored)
    except ValidationError as invalid:
        runtime.logger.warning(
            f"The assignment settings batch '{sample_batch_id}' saved no longer "
            f"read as settings; running on the defaults: "
            f"{invalid.error_count()} error(s)."
        )
        return PeakAssignmentConfig()


async def pass_settings(
    sample_batch_id: str, config: Optional[PeakAssignmentConfig]
) -> tuple[PeakAssignmentConfig, str]:
    """The settings a pass runs on, and where they came from: ``config``
    where a step's route was given some, else the batch's own."""
    if config is not None:
        return config, SETTINGS_FROM_REQUEST
    async with async_session() as session:
        return await batch_settings(session, sample_batch_id), SETTINGS_FROM_BATCH


def _settings_of(spec: StageSpec, call: StageCall) -> Optional[dict]:
    """The settings a stage that takes some ran on, as its entry records
    them: the engine's defaults where it ran on none."""
    if not spec.takes_settings:
        return None
    return (call.config or PeakAssignmentConfig()).model_dump(mode="json")


async def _run_stage(
    spec: StageSpec,
    call: StageCall,
    begun: tuple[int, datetime],
    then: Optional[BatchStage],
    announce: Optional[Callable[..., Awaitable[None]]] = None,
    index: int = 0,
) -> tuple[dict, Optional[tuple[int, datetime]]]:
    """Run one stage, already begun: its steps run, its records stamped, and
    its standing written whichever way it ends. Its end begins ``then``, the
    pass's next stage, in the same transaction (:func:`hand_over`), unless a
    person asked the pass to stop meanwhile: then nothing begins, and every
    stage after it is left stale.

    :param announce: The pass's progress (``assign_batch``), told how far
        into the stage, its ``index``-th, the judging of its samples is.

    :return: What it ran, and ``then``'s version and start; None where
        ``then`` did not begin.
    """
    version, started = begun
    token = STAGE_VERSION.set(version)
    try:
        outcomes = await spec.run(call)
        run_ids = [call.batch_peak_run_id] if call.batch_peak_run_id else []
        await stamp_versions(call.sample_batch_id, spec.record_keys, run_ids, version)
        if spec.rewrites:
            progress = None
            if announce is not None:
                progress = partial(
                    announce, spec.stage.value, spec.label, spec.phase, index
                )
            outcomes.append(
                await judge_again(spec.stage, call, version, outcomes, progress)
            )
        if then is not None and call.cancelled is not None and await call.cancelled():
            then = None
        # In the guard: a finish that fails leaves the stage stopped, not
        # running. Samples it skipped by name leave it current with them;
        # ones counted without names leave it stale (``batch_skipped``).
        skipped, unnamed = skipped_in(outcomes)
        state, after = await hand_over(
            call.sample_batch_id,
            spec.stage,
            started,
            run_id=call.batch_peak_run_id,
            stale_reason=REASON_PARTIAL if unnamed else None,
            then=then,
            skipped=await skipped_record(skipped),
        )
    except BaseException:
        await stop_stage(call.sample_batch_id, spec.stage, then=then)
        raise
    finally:
        STAGE_VERSION.reset(token)
    entry = {
        "stage": spec.stage.value,
        "label": spec.label,
        "version": version,
        "state": state,
        "outcomes": outcomes,
        "settings": _settings_of(spec, call),
    }
    return entry, after


#: The key a step's outcome data names, per sample, the batch peaks whose
#: earlier claims it withdrew under (``batch_water_ladder.RESTORED_SAMPLES``).
RESTORED_SAMPLES = "restored_samples"


def restored_in(outcomes: list[dict]) -> dict[str, set[str]]:
    """Sample -> the batch peaks a step's outcome says it withdrew claims at."""
    restored: dict[str, set[str]] = {}
    for outcome in outcomes:
        data = outcome.get("data") if isinstance(outcome, dict) else None
        named = data.get(RESTORED_SAMPLES) if isinstance(data, dict) else None
        for sample, anchors in (named or {}).items():
            restored.setdefault(sample, set()).update(anchors)
    return restored


async def judge_again(
    stage: BatchStage,
    call: StageCall,
    version: int,
    outcomes: list[dict],
    progress: Optional[Callable[[float], Awaitable[None]]] = None,
) -> dict:
    """Judge every sample a rewriting stage wrote in again, as a run judges
    it (``batch_rejudge``): the members it marked, after the series the
    members its records corroborate, and where it withdrew an earlier
    decision the members it put back, take the tier the run's judging gives
    the reading they carry. Each scores only the readings the pass has not
    measured at the same inputs (``StageCall.kept``).

    :param progress: Told how far through the samples the judging is, a
        share, before each sample and after the last.
    :return: The step's outcome, as the stage's own steps report theirs.
    """

    samples: dict[str, frozenset[str]] = {
        sample: frozenset()
        for sample in await samples_written(call.sample_batch_id, stage.value, version)
    }
    # The decisions a sample's judging reads without a member rewritten: a
    # series record that corroborates a member's reading, and a withdrawn
    # known species, whose members read again what they read before it.
    extra = {
        BatchStage.SERIES: series_corroborated,
        BatchStage.KNOWN_SPECIES: species_restored,
    }.get(stage)
    named = restored_in(outcomes)
    if extra is not None:
        for sample, anchors in (await extra(call.sample_batch_id, version)).items():
            named.setdefault(sample, set()).update(anchors)
    for sample, anchors in named.items():
        samples[sample] = samples.get(sample, frozenset()) | frozenset(anchors)
    counts = await rejudge_samples(
        call.sample_batch_id,
        samples,
        call.config or PeakAssignmentConfig(),
        kept=call.kept,
        progress=progress,
    )
    return {
        "status": "partial" if counts.get("samples_failed") else "success",
        "message": judged_message(counts),
        "data": counts,
    }


# --- folding first ----------------------------------------------------------------

#: The pass's first stage, which no stage row tracks: every sample folded again,
#: today's rebuild (``batch_fold.rebuild_batch_ledger``).
FOLD_STAGE = "fold"
FOLD_LABEL = "Fold the samples"

#: When a pass folds every sample first: where the ledger cannot be a
#: starting point (:func:`needs_fold`), or always.
FOLD_AUTO = "auto"
FOLD_ALWAYS = "always"
FOLD_MODES = (FOLD_AUTO, FOLD_ALWAYS)

#: Why a pass folds first.
FOLD_NO_BATCH_PEAKS = "no_batch_peaks"
FOLD_IMPORTED = "import"
FOLD_LIBRARY_CHANGED = "library_changed"
FOLD_ENGINE_CHANGED = "engine_changed"
#: ``fold="always"``: the person asked for it.
FOLD_ASKED = "asked"


def fold_reasons(
    *,
    n_batch_peaks: int,
    current_action: Optional[str],
    last_pass: Optional[dict],
    library: dict,
    engine_version: str = PEAK_ASSIGNMENT_ENGINE_VERSION,
) -> list[str]:
    """Why the ledger cannot be a starting point for a pass, which then folds
    every sample first; none where it can.

    - The batch has no batch peaks: there is nothing to start from.
    - The current run is an import: the pass puts Mascope's own view back.
    - A target collection or reference list changed since the last pass:
      only a fold reads them. Not the batch's ``rematch`` status, which
      belongs to the targeted matcher and which a rematch clears, not a fold.
    - The last pass ran under another engine version.

    :param last_pass: The batch's last completed pass (:func:`last_pass`):
        its ``library`` and ``engine_version``; None where no pass completed,
        and the ledger is what the folds at ingest built.
    :param library: The library as it reads now (:func:`library_read`).
    """
    reasons = []
    if not n_batch_peaks:
        reasons.append(FOLD_NO_BATCH_PEAKS)
    if current_action == ACTION_IMPORT:
        reasons.append(FOLD_IMPORTED)
    if last_pass is not None:
        read = last_pass.get("library")
        if isinstance(read, dict) and read != library:
            reasons.append(FOLD_LIBRARY_CHANGED)
        if last_pass.get("engine_version") not in (None, engine_version):
            reasons.append(FOLD_ENGINE_CHANGED)
    return reasons


def is_pass_summary(summary: Any) -> bool:
    """Whether a run's summary is a pass's: one that lists its stages."""
    return isinstance(summary, dict) and isinstance(summary.get("stages"), list)


async def last_pass(session, sample_batch_id: str) -> Optional[dict]:
    """The batch's newest completed pass: its run id, engine version, the
    samples it covered and the library it read. A pass that failed or was
    cancelled is not one: what it left need not agree with what it read."""
    runs = (
        (
            await session.execute(
                select(BatchPeakRun)
                .where(
                    BatchPeakRun.sample_batch_id == sample_batch_id,
                    BatchPeakRun.status == STATUS_COMPLETED,
                )
                .order_by(BatchPeakRun.batch_peak_run_utc_created.desc())
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        if is_pass_summary(run.summary):
            return {
                "batch_peak_run_id": run.batch_peak_run_id,
                "engine_version": run.engine_version,
                "samples": run.summary.get("samples"),
                "library": run.summary.get("library"),
                # None for a pass from before the batch kept settings
                # (MAS-1014): read as the defaults' (pass_settings_hash).
                "settings_hash": run.summary.get("settings_hash"),
            }
    return None


async def needs_fold(sample_batch_id: str) -> list[str]:
    """Why a pass of the batch has to fold every sample first
    (:func:`fold_reasons`); empty where the ledger can be its starting point.
    The one rule the pass and the batch's assignment status read."""
    async with async_session() as session:
        n_batch_peaks = (
            await session.execute(
                select(func.count())
                .select_from(BatchPeak)
                .where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        ).scalar_one()
        current = await current_run(session, sample_batch_id)
        last = await last_pass(session, sample_batch_id)
        library = await library_read(session, sample_batch_id)
    return fold_reasons(
        n_batch_peaks=n_batch_peaks,
        current_action=current.action if current is not None else None,
        last_pass=last,
        library=library,
    )


async def batch_samples(session, sample_batch_id: str) -> list[str]:
    """The batch's samples, by id: what a pass covers."""
    return list(
        (
            await session.execute(
                select(Sample.sample_item_id)
                .where(Sample.sample_batch_id == sample_batch_id)
                .order_by(Sample.sample_item_id)
            )
        )
        .scalars()
        .all()
    )


# --- the pass's record ------------------------------------------------------------

#: What a pass's summary says of each of its stages.
ENTRY_DONE = "done"
ENTRY_FAILED = "failed"
ENTRY_CANCELLED = "cancelled"
ENTRY_NOT_RUN = "not_run"


def _outcome_record(outcome: Any) -> Optional[dict]:
    """One step's outcome as a pass's summary keeps it: its status, its
    result line, its counts, and the settings or thresholds it ran on."""
    if not isinstance(outcome, dict):
        return None
    record = {
        "status": outcome.get("status"),
        "message": outcome.get("message"),
        "data": outcome.get("data"),
    }
    if outcome.get("config") is not None:
        record["config"] = outcome["config"]
    return record


class PassRecord:
    """A pass's summary as it goes: every stage in order, the fold first,
    each with its status, start, end and outcome."""

    def __init__(self, from_stage: Optional[str], settings_hash: Optional[str] = None):
        self.from_stage = from_stage
        self.fold: dict = {"ran": False, "reasons": []}
        self.samples: list[str] = []
        self.library: Optional[dict] = None
        self.settings_hash = settings_hash
        self.entries: dict[str, dict] = {}
        for name, label, phase in [(FOLD_STAGE, FOLD_LABEL, PHASE_GATHER)] + [
            (spec.stage.value, spec.label, spec.phase) for spec in STAGES
        ]:
            self.entries[name] = {
                "stage": name,
                "label": label,
                "phase": phase,
                "status": ENTRY_NOT_RUN,
                "utc_started": None,
                "utc_ended": None,
            }
        self.planned: list[str] = []
        self.running: Optional[str] = None

    def plan(self, stages: Iterable[str]) -> None:
        """The stages the pass runs, in order, the fold first where it folds."""
        self.planned = list(stages)

    def start(self, stage: str, at: Optional[datetime] = None) -> None:
        self.running = stage
        self.entries[stage]["utc_started"] = _iso(at or _now())

    def done(self, stage: str, outcomes: list, **extra: Any) -> None:
        entry = self.entries[stage]
        entry.update(extra)
        entry["status"] = ENTRY_DONE
        entry["utc_ended"] = _iso(_now())
        entry["outcomes"] = [r for r in map(_outcome_record, outcomes) if r]
        self.running = None

    def _unfinished(self) -> list[str]:
        return [s for s in self.planned if self.entries[s]["status"] == ENTRY_NOT_RUN]

    def stop(self, status: str, error: Optional[str] = None) -> None:
        """The pass stopped: the stage it was in ends ``status``; a cancel
        leaves every planned stage it did not reach cancelled too, a failure
        leaves them not run."""
        if self.running is not None:
            entry = self.entries[self.running]
            entry["status"] = status
            entry["utc_ended"] = _iso(_now())
            if error is not None:
                entry["error"] = error[:2000]
            self.running = None
        if status == ENTRY_CANCELLED:
            for stage in self._unfinished():
                self.entries[stage]["status"] = ENTRY_CANCELLED

    @property
    def first_stage(self) -> Optional[str]:
        return self.planned[0] if self.planned else None

    def summary(self) -> dict:
        return {
            "from_stage": self.from_stage,
            "first_stage": self.first_stage,
            "fold": self.fold,
            "samples": self.samples,
            "library": self.library,
            "settings_hash": self.settings_hash,
            "stages": list(self.entries.values()),
        }


def pass_outcome(
    sample_batch_id: str,
    record: Optional[PassRecord],
    run_id: Optional[str],
    asked: Optional[BatchStage] = None,
) -> dict:
    """The controller result the background-task decorator reports from.

    :param record: The pass's record; None where nothing was to run, and no
        run was opened.
    :param asked: The stage the pass was asked to start at, where it started
        at an earlier one that was not current.
    """
    notification_data = {"sample_batch_id": sample_batch_id}
    ran = (
        [e for e in record.entries.values() if e["status"] != ENTRY_NOT_RUN]
        if record is not None
        else []
    )
    if not ran:
        return {
            "status": "skipped",
            "message": "Every batch stage is current; nothing ran.",
            "data": {"batch_peak_run_id": run_id, "stages": []},
            "_notification_data": notification_data,
        }
    done = [e for e in ran if e["status"] == ENTRY_DONE]
    cancelled = any(e["status"] == ENTRY_CANCELLED for e in ran)
    stale = [
        e["label"]
        for e in done
        if e["stage"] != FOLD_STAGE and e.get("state") != STATE_CURRENT
    ]
    messages = " ".join(
        f"{entry['label']}: {outcome.get('message')}"
        for entry in done
        for outcome in entry.get("outcomes", [])
        if outcome.get("message")
    )
    head = (
        f"Ran {len(done)} batch stage{'s' if len(done) != 1 else ''} from "
        f"{ran[0]['label']}."
    )
    if asked is not None and record.first_stage not in (asked.value, FOLD_STAGE):
        head += (
            f" {STAGE_BY_NAME[asked].label} was asked for; the stages before it "
            "were not current, so the pass started at the first of them."
        )
    tail = (
        f" Still stale: {', '.join(stale)}; run the batch stages again from there."
        if stale
        else ""
    )
    if cancelled:
        tail += (
            " Cancelled: what the stages before the cancel wrote stands, and the "
            "stages the pass did not finish are stale."
        )
    return {
        "status": "cancelled" if cancelled else "partial" if stale else "success",
        "message": f"{head} {messages}{tail}".strip(),
        "data": {
            "batch_peak_run_id": run_id,
            "first_stage": record.first_stage,
            "stages": [
                {
                    "stage": entry["stage"],
                    "status": entry["status"],
                    "version": entry.get("version"),
                    "state": entry.get("state"),
                    "outcomes": [
                        {"status": o.get("status"), "data": o.get("data")}
                        for o in entry.get("outcomes", [])
                    ],
                }
                for entry in ran
            ],
        },
        "_notification_data": notification_data,
    }


# --- the pass ---------------------------------------------------------------------

#: The pass's notification type: its progress packets and its end.
NOTIFICATION_TYPE = "assign_batch"


def stage_packet(
    sample_batch_id: str,
    run_id: str,
    *,
    stage: str,
    label: str,
    phase: str,
    stage_index: int,
    n_stages: int,
    user_id: Optional[int],
    process_id: str,
    parent_id: Optional[str],
    stage_progress: float = 0.0,
) -> UserNotification:
    """The pending packet a pass sends as one of its stages starts, and as a
    stage judges its samples again (``stage_progress``, how far into the
    stage it is): one progress entry for the whole pass, the stage's own
    packets nested under it."""
    return UserNotification(
        process_id=process_id,
        parent_id=parent_id,
        type=NOTIFICATION_TYPE,
        status="pending",
        message=f"Assigning the batch: {label} ({stage_index + 1}/{n_stages}).",
        data={
            "sample_batch_id": sample_batch_id,
            "batch_peak_run_id": run_id,
            "stage": stage,
            "stage_index": stage_index,
            "n_stages": n_stages,
            "phase": phase,
            "stage_progress": stage_progress,
            "_room_ids": [sample_batch_id],
            "_user_id": user_id,
        },
    )


#: The error a pass interrupted by its task's cancellation is closed with.
INTERRUPTED_ERROR = (
    "Interrupted: the pass's task was cancelled (a server shutdown, or a "
    "dropped connection); what its stages wrote stands."
)


def pass_settings_hash(last: Optional[dict]) -> Optional[str]:
    """The settings hash the batch's last completed pass ran on; None where
    there is no pass. A pass from before the batch kept settings recorded no
    hash and reads as one on the defaults: no batch turns behind when the
    column arrives, every column being null, and the first save of other
    settings does."""
    if last is None:
        return None
    return last.get("settings_hash") or settings_hash(None)


def settings_moved(last: Optional[dict], hash_now: Optional[str]) -> bool:
    """Whether a pass on settings of hash ``hash_now`` runs on other settings
    than the batch's last completed pass did (:func:`pass_settings_hash`).
    No where there is no pass, or no settings to compare."""
    recorded = pass_settings_hash(last)
    return recorded is not None and hash_now is not None and recorded != hash_now


async def plan_pass(
    sample_batch_id: str,
    from_stage: Optional[str],
    fold: str,
    settings: Optional[PeakAssignmentConfig] = None,
) -> tuple[list[str], tuple]:
    """What a pass would do now: why it folds first (none where it does
    not), and the stages it runs, every one after a fold.

    A pass on other settings than the last pass's runs every stage, from
    the first: the search and the series read them, and every stage that
    rewrites members judges its samples again under their tier bands. Its
    stages may all be current: saving settings marks none stale, and the
    batch's assignment status says ``settings_changed`` instead.

    :param settings: The settings the pass runs on (:func:`pass_settings`);
        None compares nothing.
    :return: The fold's reasons, and the stages in order.
    """
    reasons = await needs_fold(sample_batch_id)
    if fold == FOLD_ALWAYS:
        reasons = [FOLD_ASKED, *reasons]
    if reasons:
        return reasons, stages_from(STAGES[0].stage)
    async with async_session() as session:
        # A stage one of whose skipped samples reads again is stale here.
        rows = await stage_rows(session, sample_batch_id)
        last = (
            await last_pass(session, sample_batch_id) if settings is not None else None
        )
    if settings is not None and settings_moved(last, settings_hash(settings)):
        return reasons, stages_from(STAGES[0].stage)
    return reasons, plan({name: row.state for name, row in rows.items()}, from_stage)


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
    error_reload=[("peak_assignment", "sample_batch_id")],
)
async def assign_batch(
    sample_batch_id: str,
    from_stage: Optional[str] = None,
    fold: str = FOLD_AUTO,
    config: Optional[PeakAssignmentConfig] = None,
    settings_source: Optional[str] = None,
    action: str = ACTION_ASSIGN,
    batch_peak_run_id: str | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """A pass of the batch stages, as one batch run.

    Folds every sample first where ``fold`` is ``always``, or ``auto`` and the
    ledger cannot be a starting point (:func:`needs_fold`); then runs the
    stages in order: from ``from_stage`` to the end, or from the first stage
    that is not current where that is earlier or no stage is named
    (:func:`plan`); after a fold, from the first. A stage that raises stops
    the pass there, marked stale, with every stage before it kept; a cancel
    asked for stops it between stages, or between the search's samples.

    One ``start_run`` (one snapshot), one end: completed, failed, or
    cancelled, never left running, whatever stops it. Its summary lists every
    stage with its status, start, end and outcome, and the samples and
    library it read. Nothing to do, no run: every stage current and nothing
    to fold.

    :param sample_batch_id: The batch.
    :param from_stage: The stage to start at (:class:`BatchStage`); None
        starts at the first that is not current.
    :param fold: ``auto`` or ``always``.
    :param config: The settings to run on in place of the batch's own: the
        ones a step's route was given. None runs on the batch's
        (:func:`pass_settings`).
    :param settings_source: Where ``config`` came from, as the run records
        it; None: ``request`` where ``config`` is given. The route that
        opens the run reads the batch's settings itself and hands them on
        as ``batch``, so the pass runs what its run records.
    :param action: The run's action: ``assign``, or the step whose own route
        started the pass.
    :param batch_peak_run_id: The run the route that started the pass opened
        (:func:`start_run`) and answered with: the pass runs it and closes it,
        whatever there is to do by then, so a client waiting on it sees it
        end. None: the pass opens its own, or none where there is nothing to
        do.
    :raises BatchStageRunningException: A pass already runs on the batch.
    """
    try:
        if from_stage is not None and from_stage not in {s.stage.value for s in STAGES}:
            raise ApiException(
                f"No batch stage is named '{from_stage}'.",
                {"from_stage": from_stage, "stages": [s.stage.value for s in STAGES]},
                422,
            )
        if fold not in FOLD_MODES:
            raise ApiException(
                f"A pass folds 'auto' or 'always', not '{fold}'.",
                {"fold": fold, "modes": list(FOLD_MODES)},
                422,
            )
    except ApiException as exc:
        if batch_peak_run_id is not None:
            # The route opened the run: refused, it is closed all the same,
            # never left running to hold the batch.
            await fail_run(
                sample_batch_id, batch_peak_run_id, f"{type(exc).__name__}: {exc}"
            )
        raise
    asked = BatchStage(from_stage) if from_stage is not None else None
    if config is None or settings_source is None:
        config, settings_source = await pass_settings(sample_batch_id, config)
    if batch_peak_run_id is not None:
        run_id = batch_peak_run_id
    else:
        # Nothing to fold and every stage current: no run, so a press that
        # has nothing to do prunes no pass from the history.
        reasons, stages = await plan_pass(sample_batch_id, from_stage, fold, config)
        if not reasons and not stages:
            return pass_outcome(sample_batch_id, None, None)
        run_id = await start_run(
            sample_batch_id,
            action,
            config=pass_config(from_stage, fold, config, settings_source),
            user_id=user_id,
        )
    record = PassRecord(from_stage, settings_hash(config))
    call = StageCall(
        sample_batch_id=sample_batch_id,
        config=config,
        user_id=user_id,
        parent_id=process_id,
        batch_peak_run_id=run_id,
        cancelled=lambda: cancel_requested(run_id),
    )

    async def reload() -> None:
        # After each stage that rewrites members; the decorator sends the
        # one at the end, as it sends the pass's own notification.
        if independent_transaction:
            await handle_reloads(
                f"Stage reload {NOTIFICATION_TYPE}",
                [("peak_assignment", "sample_batch_id")],
                {"sample_batch_id": sample_batch_id},
                None,
            )

    try:
        # Read again under the run, which no second pass, rebuild or import
        # gets past: what the pass does is what it records.
        reasons, stages = await plan_pass(sample_batch_id, from_stage, fold, config)
        async with async_session() as session:
            record.samples = await batch_samples(session, sample_batch_id)
            record.library = await library_read(session, sample_batch_id)
        record.fold = {"ran": bool(reasons), "reasons": reasons}
        record.plan(([FOLD_STAGE] if reasons else []) + [s.value for s in stages])
        n_stages = len(record.planned)

        async def announce(stage: str, label: str, phase: str, index: int, share=0.0):
            # On the run first, for the batch's assignment status; then the
            # packet, for whoever follows the process.
            await record_progress(
                run_id,
                {
                    "process_id": process_id,
                    "stage": stage,
                    "label": label,
                    "phase": phase,
                    "stage_index": index,
                    "n_stages": n_stages,
                    "stage_progress": share,
                    "utc_stage_started": record.entries[stage]["utc_started"],
                },
            )
            if process_id is None:
                return
            await send_progress_user_notification(
                stage_packet(
                    sample_batch_id,
                    run_id,
                    stage=stage,
                    label=label,
                    phase=phase,
                    stage_index=index,
                    n_stages=n_stages,
                    stage_progress=share,
                    user_id=user_id,
                    process_id=process_id,
                    parent_id=parent_id,
                )
            )

        index = 0
        if reasons:
            record.start(FOLD_STAGE)
            await announce(FOLD_STAGE, FOLD_LABEL, PHASE_GATHER, index)
            outcome = await rebuild_batch_ledger(
                sample_batch_id,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
                batch_peak_run_id=run_id,
            )
            record.done(FOLD_STAGE, [outcome], reasons=reasons)
            await reload()
            index += 1
            await stop_if_cancelled(call)

        # The first stage begins here, refused while another pass runs; each
        # later one as the stage before it ends.
        begun = await begin_stage(sample_batch_id, stages[0]) if stages else None
        for position, stage in enumerate(stages):
            spec = STAGE_BY_NAME[stage]
            then = stages[position + 1] if position + 1 < len(stages) else None
            record.start(stage.value, begun[1])
            await announce(stage.value, spec.label, spec.phase, index)
            entry, begun = await _run_stage(spec, call, begun, then, announce, index)
            record.done(
                stage.value,
                entry["outcomes"],
                version=entry["version"],
                state=entry["state"],
                settings=entry["settings"],
            )
            if spec.rewrites:
                await reload()
            index += 1
            if then is not None and begun is None:
                # Asked to stop as this stage ended: nothing began, and the
                # stages after it are stale (:func:`hand_over`).
                raise PassCancelled()
        await complete_run(sample_batch_id, run_id, summary=record.summary())
    except PassCancelled:
        record.stop(ENTRY_CANCELLED)
        await close_shielded(
            cancel_run(sample_batch_id, run_id, record.summary()),
            f"pass '{run_id}' as cancelled",
        )
        return pass_outcome(sample_batch_id, record, run_id, asked)
    except asyncio.CancelledError:
        # Not a person's cancel: the task's, a shutdown's. The run is closed
        # all the same, never left running to hold the batch until the
        # startup reaper runs.
        record.stop(ENTRY_CANCELLED, INTERRUPTED_ERROR)
        await close_shielded(
            cancel_run(sample_batch_id, run_id, record.summary(), INTERRUPTED_ERROR),
            f"pass '{run_id}' as cancelled",
        )
        raise
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        record.stop(ENTRY_FAILED, error)
        await close_shielded(
            fail_run(sample_batch_id, run_id, error, record.summary()),
            f"pass '{run_id}' as failed",
        )
        raise
    return pass_outcome(sample_batch_id, record, run_id, asked)


@api_controller()
async def check_no_run_in_flight(sample_batch_id: str) -> dict:
    """The routes' synchronous refusal: a second batch-level operation on a
    ledger one is still rewriting is refused on the request, not in a
    notification.

    Refused while a batch run is in flight, and while a pass of the batch
    stages runs: a pass holds a stage row running from its start to its end,
    also where it holds no batch run (:class:`BatchStageRunningException`).
    """
    async with async_session() as session:
        stage = await running_stage(session, sample_batch_id)
        if stage is not None:
            raise BatchStageRunningException(sample_batch_id, stage)
        running = await running_run(session, sample_batch_id)
        if running is not None:
            raise BatchRunInFlightException(sample_batch_id, running)
    return {"status": "success", "message": "No batch run in flight", "results": 0}


__all__ = [
    "ENTRY_CANCELLED",
    "ENTRY_DONE",
    "ENTRY_FAILED",
    "ENTRY_NOT_RUN",
    "FOLD_ALWAYS",
    "FOLD_ASKED",
    "FOLD_AUTO",
    "FOLD_ENGINE_CHANGED",
    "FOLD_IMPORTED",
    "FOLD_LABEL",
    "FOLD_LIBRARY_CHANGED",
    "FOLD_MODES",
    "FOLD_NO_BATCH_PEAKS",
    "FOLD_STAGE",
    "NOTIFICATION_TYPE",
    "BatchStageRunningException",
    "PassCancelled",
    "PassRecord",
    "STAGES",
    "STAGE_BY_NAME",
    "StageCall",
    "StageSpec",
    "SETTINGS_FROM_BATCH",
    "SETTINGS_FROM_REQUEST",
    "assign_batch",
    "batch_settings",
    "batch_samples",
    "begin_stage",
    "finish_stage",
    "first_not_current",
    "fold_reasons",
    "hand_over",
    "is_pass_summary",
    "last_pass",
    "library_read",
    "needs_fold",
    "pass_config",
    "pass_outcome",
    "pass_settings",
    "plan",
    "plan_pass",
    "settings_moved",
    "running_stage",
    "stage_standing",
    "stages_after",
    "stage_packet",
    "stamp_versions",
    "standing_entries",
    "stop_if_cancelled",
    "stop_stage",
    "upstream_reason",
    "with_version",
    "check_no_run_in_flight",
]
