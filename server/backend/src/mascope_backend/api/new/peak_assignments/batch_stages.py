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

One entry runs them (:func:`run_batch_stages`): from a given stage to the end,
or from the first stage that is not current, whichever comes first. Each
per-stage route starts it at its own stage, or at an earlier one that is not
current, so a click on any of them leaves every stage after it current too,
and no stage runs on what an earlier one has not settled.

**Staleness** (:data:`batch_peak_stage`, one row per batch and stage): a stage
is current while nothing it reads has moved since its last run finished, and
stale once something has:

- a fold (a sample's new run, an ingest), a rebuild, an engine's batch
  result imported, and a person's pin on a batch peak or its release rewrite
  members every stage reads, so each marks every stage stale
  (:func:`mark_stale` from the first);
- a stage that runs rewrites or records what the stages after it read, so its
  end marks every later stage stale until each runs again;
- a stage that stops partway, raised, cancelled or with samples it could not
  read, keeps what it wrote and is marked stale itself;
- a consensus recompute that leaves a record behind its batch peak's reading
  keeps the record, marked stale (``batch_peaks.compute_consensus``), and marks
  the stage that wrote it stale.

The ledger serves where each stage stands (:func:`stage_standing`).

**One pass at a time.** A pass holds a stage row ``running`` from its first
stage's start to its last stage's end: each later stage begins in the
transaction the stage before it ends in (:func:`hand_over`), so it holds one
also where it holds no batch run (judging samples again, stamping versions,
the ratio stability, the probable blends). Every step's route refuses while
one does (``batch_runs.check_no_run_in_flight``), and so does a pass's first
stage, which a second press can reach before the first pass's stage is
marked (:func:`begin_stage`). Every writer of the stage rows takes the
batch's fold lock first, as a fold does, so no two lock them in different
orders (:func:`_fold_lock`).

**Settings.** The search and the series take settings. A pass given some
runs both on them; a pass given none, which every step but the search's
dialog starts, runs each on the settings its own last run recorded, and on
the engine's defaults where it never ran (:func:`settings_for`).

Every run of a stage counts up its version, and every record it writes carries
the version (``version`` in the record): a batch decision is a versioned
record, which a sample's run can name (MAS-985).

Design: ``docs/dev/batch_assignment_one_action.md``, section 4 (the order),
and ``docs/dev/assignment_quality_plan.md``, milestone 7 (MAS-983).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from fastapi import status
from pydantic import ValidationError
from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    CodedHTTPException,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import STAGE_VERSION
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_BATCH_CHECKS,
    ACTION_CO_VARIATION,
    ACTION_ISOTOPOLOGUES,
    ACTION_KNOWN_SPECIES,
    ACTION_SEARCH,
    ACTION_SERIES,
    ACTION_STRIP_ISOTOPOLOGUES,
    ACTION_WATER_LADDERS,
    RUN_IN_FLIGHT_CODE,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.db import BatchPeak, BatchPeakRun, BatchPeakStage, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime


class BatchStage(StrEnum):
    """A batch stage, in the order every pass runs them."""

    WATER_LADDERS = "water_ladders"
    KNOWN_SPECIES = "known_species"
    SEARCH = "search"
    ISOTOPE_LINES = "isotope_lines"
    SERIES = "series"
    CO_VARIATION = "co_variation"
    CONTINUITY = "continuity"
    BATCH_CHECKS = "batch_checks"
    BLENDS = "blends"


#: What a stage's row says of it.
STATE_CURRENT = "current"
STATE_RUNNING = "running"
STATE_STALE = "stale"
#: What the ledger says of a stage with no row: it never ran on the batch.
STATE_NOT_RUN = "not_run"

#: Why a stage is stale.
REASON_FOLD = "fold"
REASON_REBUILD = "rebuild"
#: An engine's batch result imported (``batch_import.py``).
REASON_IMPORT = "import"
#: A person's pin on a batch peak, or its release (``batch_curation.py``).
REASON_CURATION = "curation"
REASON_INTERRUPTED = "interrupted"
REASON_PARTIAL = "partial"
REASON_CONSENSUS = "consensus"
#: An earlier stage ran again: ``upstream:<stage>``.
REASON_UPSTREAM = "upstream"

#: The phases the app groups the stages under (``batch_assignment_one_action.md``,
#: 3.7): words of the app, not of the order.
PHASE_SOURCE = "Recognise source ions"
PHASE_COMPOSITIONS = "Find compositions"
PHASE_CROSS_CHECK = "Cross-check across the batch"


@dataclass(frozen=True)
class StageCall:
    """What one stage of a pass is run with.

    :param sample_batch_id: The batch.
    :param config: The pass's settings: the search's and the series' bands
        and ranges. None where the pass was given none: a stage that takes
        settings then runs on the ones its last run recorded
        (:func:`settings_for`), and the rest on the engine's defaults.
    :param user_id: Who started the pass.
    :param parent_id: The pass's process id, which every stage's progress
        nests under.
    """

    sample_batch_id: str
    config: Optional[PeakAssignmentConfig]
    user_id: Optional[int]
    parent_id: Optional[str]


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
    :param settings_from: The batch run action whose recorded settings it
        runs on in a pass given none: a stage that takes settings (the
        search's, the series'); None for one that takes none.
    """

    stage: BatchStage
    label: str
    phase: str
    actions: tuple[str, ...]
    record_keys: tuple[str, ...]
    rewrites: bool
    run: Callable[[StageCall], Awaitable[list[dict]]]
    settings_from: Optional[str] = None


def _dependent(call: StageCall) -> dict:
    """The keyword arguments a stage's controller runs under inside a pass:
    a dependent task whose packets nest under the pass's process."""
    return {
        "sample_batch_id": call.sample_batch_id,
        "independent_transaction": False,
        "user_id": call.user_id,
        "process_id": gen_id(8),
        "parent_id": call.parent_id,
    }


# The stages' controllers are imported where they are called: each of their
# modules imports the fold, which marks the stages stale through this module.


async def _run_water_ladders(call: StageCall) -> list[dict]:
    from mascope_backend.api.new.peak_assignments.batch_water_ladder import (
        measure_batch_water_ladders,
    )

    return [await measure_batch_water_ladders(**_dependent(call))]


async def _run_known_species(call: StageCall) -> list[dict]:
    from mascope_backend.api.new.peak_assignments.batch_known_species import (
        decide_batch_known_species,
    )

    return [await decide_batch_known_species(**_dependent(call))]


async def _run_search(call: StageCall) -> list[dict]:
    from mascope_backend.api.new.peak_assignments.batch_untargeted import (
        search_batch_untargeted,
    )

    return [await search_batch_untargeted(config=call.config, **_dependent(call))]


async def _run_isotope_lines(call: StageCall) -> list[dict]:
    """The rows that are another row's isotope line, stripped to it, then the
    diagnostic lines of what stands stamped: a stripped row is no parent of
    lines of its own, and a stamp reads the parents the strip left."""
    from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
        stamp_batch_isotopologues,
    )
    from mascope_backend.api.new.peak_assignments.batch_merged_isotopologues import (
        strip_batch_isotopologue_rows,
    )

    return [
        await strip_batch_isotopologue_rows(**_dependent(call)),
        await stamp_batch_isotopologues(**_dependent(call)),
    ]


async def _run_series(call: StageCall) -> list[dict]:
    from mascope_backend.api.new.peak_assignments.batch_series import (
        detect_batch_series,
    )

    return [await detect_batch_series(config=call.config, **_dependent(call))]


async def _run_co_variation(call: StageCall) -> list[dict]:
    from mascope_backend.api.new.peak_assignments.batch_coherence import (
        read_batch_co_variation,
    )

    return [await read_batch_co_variation(**_dependent(call))]


async def _run_continuity(call: StageCall) -> list[dict]:
    from mascope_backend.api.new.peak_assignments.continuity import (
        recompute_batch_continuity,
    )

    return [await recompute_batch_continuity(**_dependent(call))]


async def _run_batch_checks(call: StageCall) -> list[dict]:
    from mascope_backend.api.new.peak_assignments.batch_checks import (
        read_batch_checks,
    )

    return [await read_batch_checks(**_dependent(call))]


async def _run_blends(call: StageCall) -> list[dict]:
    """The probable blends: the consensus of every batch peak recomputed, so
    each reads the members the stages before left it (``batch_blend.py``)."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        recompute_batch_consensus,
    )

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
        ("water_ladder",),
        True,
        _run_water_ladders,
    ),
    StageSpec(
        BatchStage.KNOWN_SPECIES,
        "Decide known species",
        PHASE_SOURCE,
        (ACTION_KNOWN_SPECIES,),
        ("known_species",),
        True,
        _run_known_species,
    ),
    StageSpec(
        BatchStage.SEARCH,
        "Search untargeted",
        PHASE_COMPOSITIONS,
        (ACTION_SEARCH,),
        ("persistence",),
        True,
        _run_search,
        settings_from=ACTION_SEARCH,
    ),
    StageSpec(
        BatchStage.ISOTOPE_LINES,
        "Isotope lines",
        PHASE_COMPOSITIONS,
        (ACTION_STRIP_ISOTOPOLOGUES, ACTION_ISOTOPOLOGUES),
        (
            "merged_isotopologue_of",
            "merged_isotopologues",
            "diagnostic_isotopologues",
            "stamped_isotopologue_of",
        ),
        True,
        _run_isotope_lines,
    ),
    StageSpec(
        BatchStage.SERIES,
        "Detect series",
        PHASE_COMPOSITIONS,
        (ACTION_SERIES,),
        ("series",),
        True,
        _run_series,
        settings_from=ACTION_SERIES,
    ),
    StageSpec(
        BatchStage.CO_VARIATION,
        "Read co-variation",
        PHASE_CROSS_CHECK,
        (ACTION_CO_VARIATION,),
        ("co_variation", "flat_series"),
        False,
        _run_co_variation,
    ),
    StageSpec(
        BatchStage.CONTINUITY,
        "Ratio stability",
        PHASE_CROSS_CHECK,
        (),
        (),
        False,
        _run_continuity,
    ),
    StageSpec(
        BatchStage.BATCH_CHECKS,
        "Read batch checks",
        PHASE_CROSS_CHECK,
        (ACTION_BATCH_CHECKS,),
        ("batch_checks",),
        False,
        _run_batch_checks,
    ),
    StageSpec(
        BatchStage.BLENDS,
        "Probable blends",
        PHASE_CROSS_CHECK,
        (),
        (),
        False,
        _run_blends,
    ),
)

#: Each stage's spec, by name.
STAGE_BY_NAME: dict[BatchStage, StageSpec] = {spec.stage: spec for spec in STAGES}

#: The stage that wrote each provenance key's records.
STAGE_OF_KEY: dict[str, BatchStage] = {
    key: spec.stage for spec in STAGES for key in spec.record_keys
}


# --- pure parts -------------------------------------------------------------------


def stages_from(first: BatchStage | str) -> tuple[BatchStage, ...]:
    """The stages from ``first`` to the end, in order."""
    names = [spec.stage for spec in STAGES]
    return tuple(names[names.index(BatchStage(first)) :])


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


def stopped_partway(outcome: Any) -> bool:
    """Whether a step's outcome says it left samples it could not read: what
    it wrote stands, and the stage is stale until it reads them."""
    data = outcome.get("data") if isinstance(outcome, dict) else None
    if not isinstance(data, dict):
        return False
    return bool(data.get("samples_failed"))


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


# --- the stages' standing ---------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BatchStageRunningException(CodedHTTPException):
    """A pass of the batch stages is running on this batch (409).

    A pass holds a stage row ``running`` from its first stage's start to its
    last stage's end, also where it holds no batch run: while it judges
    samples again, stamps versions, or runs the ratio stability and the
    probable blends. Each step's route, the pass's own, a rebuild and an
    import refuse while it does (``batch_runs.check_no_run_in_flight``), and
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


async def _fold_lock(session, sample_batch_id: str) -> None:
    """The batch's fold lock, taken before any stage row is written.

    A fold marks the stage rows inside its own transaction, under this lock,
    and so does a consensus recompute. A writer of more than one stage row
    that took the rows' own locks first could lock them in another order than
    the fold, and Postgres would abort one of the two to break the deadlock.
    Taken first by every writer, the lock orders them all.
    """
    # Imported here: the controller imports the fold, which imports this module.
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        _acquire_batch_fold_lock,
    )

    await _acquire_batch_fold_lock(session, sample_batch_id)


async def mark_stale(
    session,
    sample_batch_id: str,
    stages: Iterable[BatchStage | str],
    reason: str,
    *,
    at: Optional[datetime] = None,
    skip_running: bool = False,
) -> None:
    """Mark stages of a batch stale, on the caller's session and inside its
    transaction: the fold's, which writes what they read, and which holds
    the batch's fold lock (:func:`_fold_lock`).

    Only a stage that has run has a row to mark; one that never ran is not
    stale, it is not run. A running stage keeps running, and the mark is what
    its end reads (:func:`finish_stage`): it ends stale where something moved
    after it started. With ``skip_running`` a running stage is not marked at
    all: a consensus recompute's mark, which a running stage's own writes
    set off.
    """
    names = [BatchStage(stage).value for stage in stages]
    if not names:
        return
    conditions = [
        BatchPeakStage.sample_batch_id == sample_batch_id,
        BatchPeakStage.stage.in_(names),
    ]
    if skip_running:
        conditions.append(BatchPeakStage.state != STATE_RUNNING)
    await session.execute(
        update(BatchPeakStage)
        .where(*conditions)
        .values(
            state=case(
                (BatchPeakStage.state == STATE_RUNNING, STATE_RUNNING),
                else_=STATE_STALE,
            ),
            stale_reason=reason,
            utc_stale=at or _now(),
        )
        .execution_options(synchronize_session=False)
    )


async def mark_every_stage_stale(session, sample_batch_id: str, reason: str) -> None:
    """A fold's mark: every stage reads the members it rewrote."""
    await mark_stale(session, sample_batch_id, stages_from(STAGES[0].stage), reason)


async def mark_batch_stale(sample_batch_id: str, reason: str) -> None:
    """Mark every stage of a batch stale, in a transaction of its own under
    the fold lock: the mark of a writer whose rewrites commit sample by
    sample, a rebuild, an import, a pin. Shielded, and logged rather than
    raised where it fails: it is written while an interruption unwinds too,
    and must not replace what interrupted it."""

    async def write() -> None:
        async with async_session() as session:
            await _fold_lock(session, sample_batch_id)
            await mark_every_stage_stale(session, sample_batch_id, reason)
            await session.commit()

    try:
        await asyncio.shield(write())
    except Exception as exc:  # noqa: BLE001 - never replace what interrupted it
        runtime.logger.warning(
            f"Could not mark the batch stages of batch '{sample_batch_id}' stale "
            f"({reason}): {exc}"
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
) -> tuple[str, Optional[tuple[int, datetime]]]:
    """Record a stage's run as finished, mark every stage after it stale, and
    begin ``then``, the pass's next stage, in the same transaction: between
    two stages of a pass no moment passes with none of them running, so no
    second pass is admitted there.

    It is current unless something marked it stale after it started (a fold
    that landed while it ran) or the run itself stopped partway
    (``stale_reason``).

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


async def stage_states(session, sample_batch_id: str) -> dict[str, BatchPeakStage]:
    """The rows of a batch's stages, by stage name."""
    rows = (
        (
            await session.execute(
                select(BatchPeakStage).where(
                    BatchPeakStage.sample_batch_id == sample_batch_id
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.stage: row for row in rows}


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


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


async def _runs_since(
    sample_batch_id: str, actions: tuple[str, ...], since: datetime
) -> list[str]:
    """The batch runs of these actions started since ``since``: the runs one
    stage's run recorded itself as."""
    if not actions:
        return []
    async with async_session() as session:
        return list(
            (
                await session.execute(
                    select(BatchPeakRun.batch_peak_run_id)
                    .where(
                        BatchPeakRun.sample_batch_id == sample_batch_id,
                        BatchPeakRun.action.in_(actions),
                        BatchPeakRun.batch_peak_run_utc_created >= since,
                    )
                    .order_by(BatchPeakRun.batch_peak_run_utc_created)
                )
            )
            .scalars()
            .all()
        )


async def stamp_versions(
    sample_batch_id: str, keys: tuple[str, ...], run_ids: list[str], version: int
) -> int:
    """Stamp the records a stage's run wrote with the run's version, under the
    fold lock: a record names the run that wrote it, and the version says how
    many runs of its stage came before.

    :return: How many records were stamped.
    """
    # Imported here: the controller imports the fold, which imports this module.
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        _acquire_batch_fold_lock,
    )

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


async def recorded_settings(
    sample_batch_id: str, action: str
) -> Optional[PeakAssignmentConfig]:
    """The settings the batch's newest run of ``action`` recorded: what its
    step last ran with, the search's dialog's for the search. None where it
    never ran, or where what it recorded no longer reads as settings."""
    async with async_session() as session:
        recorded = (
            await session.execute(
                select(BatchPeakRun.config)
                .where(
                    BatchPeakRun.sample_batch_id == sample_batch_id,
                    BatchPeakRun.action == action,
                    BatchPeakRun.config.is_not(None),
                )
                .order_by(BatchPeakRun.batch_peak_run_utc_created.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if not isinstance(recorded, dict):
        return None
    try:
        return PeakAssignmentConfig.model_validate(recorded)
    except ValidationError as invalid:
        runtime.logger.warning(
            f"The settings the last '{action}' run of batch '{sample_batch_id}' "
            f"recorded no longer read as settings; running on the defaults: "
            f"{invalid.error_count()} error(s)."
        )
        return None


async def settings_for(spec: StageSpec, call: StageCall) -> StageCall:
    """What one stage runs with: the pass's settings where it was given
    some; else, for a stage that takes settings, the ones its own last run
    recorded, so a pass another step started searches with what the search's
    dialog last set, not the engine's defaults; else the defaults."""
    if call.config is not None or spec.settings_from is None:
        return call
    recorded = await recorded_settings(call.sample_batch_id, spec.settings_from)
    return replace(call, config=recorded) if recorded is not None else call


async def _run_stage(
    spec: StageSpec,
    call: StageCall,
    begun: tuple[int, datetime],
    then: Optional[BatchStage],
) -> tuple[dict, Optional[tuple[int, datetime]]]:
    """Run one stage, already begun: its steps run, its records stamped, and
    its standing written whichever way it ends. Its end begins ``then``, the
    pass's next stage, in the same transaction (:func:`hand_over`).

    :return: What it ran, and ``then``'s version and start.
    """
    version, started = begun
    token = STAGE_VERSION.set(version)
    try:
        call = await settings_for(spec, call)
        outcomes = await spec.run(call)
        run_ids = await _runs_since(call.sample_batch_id, spec.actions, started)
        await stamp_versions(call.sample_batch_id, spec.record_keys, run_ids, version)
        if spec.rewrites:
            outcomes.append(await judge_again(spec.stage, call, version, outcomes))
        # In the guard: a finish that fails leaves the stage stopped, not
        # running.
        state, after = await hand_over(
            call.sample_batch_id,
            spec.stage,
            started,
            run_id=run_ids[-1] if run_ids else None,
            stale_reason=(
                REASON_PARTIAL if any(stopped_partway(o) for o in outcomes) else None
            ),
            then=then,
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
    stage: BatchStage, call: StageCall, version: int, outcomes: list[dict]
) -> dict:
    """Judge every sample a rewriting stage wrote in again, as a run judges
    it (``batch_rejudge``): the members it marked, after the series the
    members its records corroborate, and where it withdrew an earlier
    decision the members it put back, take the tier the run's judging gives
    the reading they carry.

    :return: The step's outcome, as the stage's own steps report theirs.
    """
    from mascope_backend.api.new.peak_assignments.batch_rejudge import (
        rejudge_samples,
        samples_written,
        series_corroborated,
        species_restored,
    )

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
        call.sample_batch_id, samples, call.config or PeakAssignmentConfig()
    )
    judged = counts.get("samples_judged", 0)
    moved = counts.get("members_moved", 0)
    return {
        "status": "partial" if counts.get("samples_failed") else "success",
        "message": (
            f"Judged {judged} sample{'s' if judged != 1 else ''} again as a run "
            f"judges them: {moved} member{'s' if moved != 1 else ''} took "
            "another tier."
        ),
        "data": counts,
    }


def stages_outcome(
    sample_batch_id: str,
    ran: list[dict],
    first: Optional[BatchStage],
    asked: Optional[BatchStage] = None,
) -> dict:
    """The controller result the background-task decorator reports from.

    :param asked: The stage the pass was asked to start at, where it started
        at an earlier one that was not current.
    """
    notification_data = {"sample_batch_id": sample_batch_id}
    if not ran:
        return {
            "status": "skipped",
            "message": "Every batch stage is current; nothing ran.",
            "data": {"stages": []},
            "_notification_data": notification_data,
        }
    stale = [entry["label"] for entry in ran if entry["state"] != STATE_CURRENT]
    messages = " ".join(
        f"{entry['label']}: {outcome.get('message')}"
        for entry in ran
        for outcome in entry["outcomes"]
        if isinstance(outcome, dict) and outcome.get("message")
    )
    head = (
        f"Ran {len(ran)} batch stage{'s' if len(ran) != 1 else ''} from "
        f"{STAGE_BY_NAME[first].label}."
    )
    if asked is not None and asked != first:
        head += (
            f" {STAGE_BY_NAME[asked].label} was asked for; the stages before it "
            "were not current, so the pass started at the first of them."
        )
    tail = (
        f" Still stale: {', '.join(stale)}; run the batch stages again from there."
        if stale
        else ""
    )
    return {
        "status": "partial" if stale else "success",
        "message": f"{head} {messages}{tail}".strip(),
        "data": {
            "stages": [
                {
                    "stage": entry["stage"],
                    "version": entry["version"],
                    "state": entry["state"],
                    "outcomes": [
                        {"status": o.get("status"), "data": o.get("data")}
                        for o in entry["outcomes"]
                        if isinstance(o, dict)
                    ],
                }
                for entry in ran
            ]
        },
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def run_batch_stages(
    sample_batch_id: str,
    from_stage: Optional[str] = None,
    config: Optional[PeakAssignmentConfig] = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Run the batch stages in order: from ``from_stage`` to the end, or from
    the first stage that is not current where that is earlier or no stage is
    named (:func:`plan`). Each stage runs as the batch run its
    step always was; a stage that raises stops the pass there, marked stale,
    with every stage before it kept. Emits ``peak_assignment_reload`` at the
    end.

    :param sample_batch_id: The batch.
    :param from_stage: The stage to start at (:class:`BatchStage`); None
        starts at the first that is not current.
    :param config: The search's and the series' settings. When None, each
        of the two runs on the settings its own last run recorded, and on the
        engine's defaults where it never ran (:func:`settings_for`).
    :raises BatchStageRunningException: A pass already runs on the batch.
    """
    if from_stage is not None and from_stage not in {s.stage.value for s in STAGES}:
        raise ApiException(
            f"No batch stage is named '{from_stage}'.",
            {"from_stage": from_stage, "stages": [s.stage.value for s in STAGES]},
            422,
        )
    async with async_session() as session:
        rows = await stage_states(session, sample_batch_id)
    stages = plan({name: row.state for name, row in rows.items()}, from_stage)
    call = StageCall(
        sample_batch_id=sample_batch_id,
        config=config,
        user_id=user_id,
        parent_id=process_id,
    )
    ran: list[dict] = []
    # The first stage begins here, refused while another pass runs; each
    # later one as the stage before it ends.
    begun = await begin_stage(sample_batch_id, stages[0]) if stages else None
    for index, stage in enumerate(stages):
        then = stages[index + 1] if index + 1 < len(stages) else None
        entry, begun = await _run_stage(STAGE_BY_NAME[stage], call, begun, then)
        ran.append(entry)
    return stages_outcome(
        sample_batch_id,
        ran,
        stages[0] if stages else None,
        BatchStage(from_stage) if from_stage is not None else None,
    )


__all__ = [
    "BatchStage",
    "BatchStageRunningException",
    "STAGES",
    "STAGE_BY_NAME",
    "STAGE_OF_KEY",
    "StageCall",
    "StageSpec",
    "begin_stage",
    "finish_stage",
    "first_not_current",
    "hand_over",
    "mark_batch_stale",
    "mark_every_stage_stale",
    "mark_stale",
    "plan",
    "recorded_settings",
    "run_batch_stages",
    "running_stage",
    "settings_for",
    "stage_standing",
    "stage_states",
    "stages_after",
    "stages_from",
    "stages_outcome",
    "stamp_versions",
    "standing_entries",
    "stop_stage",
    "stopped_partway",
    "upstream_reason",
    "with_version",
]
