"""
A sample's assignment run: its lifecycle, from the request to the last row.

A run is created (pending, or running), claimed, run through the stage list
(``pipeline.RUN``, ``pipeline.DECOY`` for a decoy arm) and finalized; the
ingest hook assigns a sample as it arrives, or folds it into its batch ledger
without a run (``batch_fold``). What a run does:

- Stage A (database-first): every peak is matched against the known target
  isotopologue library by reusing the targeted matcher, then the result is
  inverted from target-anchored to peak-anchored rows (source='database';
  ``stage_a_pass``).
- Stage B (untargeted): peaks Stage A left unexplained are run through the
  mascope_tools composition finder (source='untargeted').
- Remaining peaks are persisted as 'unassigned' so each run is a complete,
  queryable ledger with a single owner per peak.

The reads of that ledger are ``ledger_reads``; a person's verdicts on it,
``verification``.
"""

import asyncio
from dataclasses import replace
from datetime import datetime as dt
from datetime import timezone

from sqlalchemy import insert, update

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.ionization.modes.versions import (
    move_to_current_versions,
    sample_setup,
)
from mascope_backend.api.new.peak_assignments import run_config
from mascope_backend.api.new.peak_assignments.admission import (
    _sample_assignments_in_flight,
    assignment_claim,
    in_flight_run_id,
)
from mascope_backend.api.new.peak_assignments.batch_decisions import (
    control_source_claims,
)
from mascope_backend.api.new.peak_assignments.batch_fold import (
    fold_sample_peaks_without_run,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    INGEST_LEDGER_BATCH,
    PEAK_ASSIGNMENT_ENGINE_VERSION,
    PeakAssignmentConfig,
    peak_assignment_enabled,
    peak_assignment_ingest_ledger,
    peak_assignment_ingest_max_peaks,
    peak_assignment_on_ingest,
)
from mascope_backend.api.new.peak_assignments.decoy import (
    DecoyPlan,
    DecoyRefused,
    context_differs,
    declared_channels,
    plan_decoy,
    swap_channels,
    wrong_adducts,
)
from mascope_backend.api.new.peak_assignments.engine import (
    measurement_model_record,
    scoring_snapshot,
)
from mascope_backend.api.new.peak_assignments.model_readers import _latest_in_app_run
from mascope_backend.api.new.peak_assignments.pipeline import (
    DECOY,
    RUN,
    RunContext,
    ledger_rows,
    run_stages,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    RESOLVED_PROFILE_KEY,
    resolve_profile,
)
from mascope_backend.api.new.peak_assignments.row_terms import SOURCE_DATABASE
from mascope_backend.api.new.peak_assignments.run_config import _stored_run_config
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    count_sample_peaks,
    ineligible_reason,
)
from mascope_backend.api.new.peak_assignments.sample_mechanisms import (
    fetch_mechanisms_by_notation,
    fetch_sample_mechanisms,
)
from mascope_backend.db import PeakAssignment, PeakAssignmentRun, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.db.retry import retry_on_connection_error
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import UserNotification
from mascope_file.name import get_instrument_type
from mascope_match import shifted_mz_axis
from mascope_tools.composition.profiles import get_reagent_profile


#: The state a run is created in when the request that asked for it answers
#: before the engine starts. It is a durable "this sample is taken" marker from
#: the moment the caller learns the run id, which is what lets the 202 name a run
#: a client can poll and what stops a second request slipping in behind it.
#:
#: In flight but not yet executing, so it is reclaimed exactly like 'running':
#: the startup reaper fails both (nothing can legitimately be held by a task
#: before workers spawn) and retention sweeps both under `keep_running_hours`.
PENDING_RUN_STATUS = "pending"

#: The state the engine holds a run in while it works.
RUNNING_RUN_STATUS = "running"


async def _create_run(
    sample_item_id: str,
    config: PeakAssignmentConfig,
    status: str = RUNNING_RUN_STATUS,
) -> PeakAssignmentRun:
    """Create and commit a PeakAssignmentRun row in a non-terminal state."""
    run = PeakAssignmentRun(
        peak_assignment_run_id=gen_id(16),
        sample_item_id=sample_item_id,
        # Attribution, so an imported run beside this one is distinguishable and
        # the retention budget and calibration pool can tell them apart.
        engine=IN_APP_ENGINE,
        engine_version=PEAK_ASSIGNMENT_ENGINE_VERSION,
        status=status,
        config=_stored_run_config(
            config, ionization_setup=await sample_setup(sample_item_id)
        ),
        # The same thresholds an import has to declare, recorded here too so a
        # tier means the same thing on either engine's run without reading into
        # the config blob.
        tier_bands=config.tier_bands(),
        # On the row and not only in the config, since every reader of a
        # sample's latest run filters on it.
        decoy=None if config.decoy is None else config.decoy.arm,
        peak_assignment_run_utc_created=dt.now(timezone.utc),
    )
    async with async_session() as session:
        session.add(run)
        await session.commit()
    return run


async def create_pending_run(
    sample_item_id: str, config: PeakAssignmentConfig | None = None
) -> PeakAssignmentRun:
    """Create the run a request is about to hand to the assignment engine.

    The assign endpoint answers with the run id, so the row has to exist before
    the response does. Creating it here also closes the window the response would
    otherwise open: from this commit on, durable admission sees the sample as
    taken, so a second request is refused rather than racing the background task
    that has not started yet.

    :param sample_item_id: The sample the run will assign.
    :param config: The run configuration; engine defaults when omitted.
    :return: The created run, in :data:`PENDING_RUN_STATUS`.
    """
    # Before the row: the run is recorded under the version it will search.
    await move_to_current_versions(sample_item_ids=[sample_item_id])
    return await _create_run(
        sample_item_id,
        config or PeakAssignmentConfig(),
        status=PENDING_RUN_STATUS,
    )


async def _adopt_run(peak_assignment_run_id: str) -> PeakAssignmentRun:
    """Take over a run created by the request, and mark it running.

    The status is what a user reads in the run selector, so it has to say which
    of the two things is true: waiting for a worker, or being computed. The
    reclamation path is the same either way.

    :param peak_assignment_run_id: The run the caller created and handed over.
    :return: The adopted run.
    :raises NotFoundException: When the run is gone - reclaimed by retention, or
        deleted with its sample - which must not be answered by silently minting
        a replacement the caller could never have learned the id of.
    """
    async with async_session() as session:
        run = await session.get(PeakAssignmentRun, peak_assignment_run_id)
        if run is None:
            raise NotFoundException(
                f"Peak assignment run '{peak_assignment_run_id}' no longer exists"
            )
        run.status = RUNNING_RUN_STATUS
        await session.commit()
        session.expunge(run)
    return run


async def _finalize_run(
    peak_assignment_run_id: str,
    status: str,
    error: str | None = None,
) -> None:
    """Mark a run completed/failed with its completion timestamp.

    Retried while the database refuses a connection
    (:func:`~mascope_backend.db.retry.retry_on_connection_error`): the failure
    that ends a run is often that refusal itself, and a run whose end is lost
    blocks its sample. The
    timestamp is the run's end, taken before the first attempt, not the moment
    the database answered again. Any other error is raised at once.

    Deliberately not where the run's ``confidence_calibration`` is recorded:
    the ledger is committed in an earlier transaction, so a run interrupted
    between the two keeps its rows and never reaches here - and a reader that
    names such a run by id is served those rows. The curve is committed with
    the rows instead (see :func:`_run_sample_assignment`), so the two cannot
    disagree whatever status the run ends in.
    """
    completed = dt.now(timezone.utc)

    async def _write() -> None:
        async with async_session() as session:
            await session.execute(
                update(PeakAssignmentRun)
                .where(
                    PeakAssignmentRun.peak_assignment_run_id == peak_assignment_run_id
                )
                .values(
                    status=status,
                    error=error,
                    peak_assignment_run_utc_completed=completed,
                )
            )
            await session.commit()

    await retry_on_connection_error(
        _write, f"mark peak assignment run '{peak_assignment_run_id}' {status}"
    )


async def _end_run(peak_assignment_run_id: str, status: str, error: str) -> None:
    """Finalize a run that ended on an exception, never raising one of its own.

    The handler that calls this re-raises what ended the run: a write that
    failed too must not take that error's place, as the error it would put
    there says nothing about the run, and a batch caller counts the run by it.
    """
    try:
        await _finalize_run(peak_assignment_run_id, status, error=error)
    except Exception as finalize_error:  # noqa: BLE001 - logged, the cause is raised
        runtime.logger.error(
            f"Peak assignment run '{peak_assignment_run_id}' could not be marked "
            f"{status}: {finalize_error!r}. It stays non-terminal until the "
            "startup reaper or retention reclaims it."
        )


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
    # Emit peak_assignment_reload to the sample's batch room on completion, so
    # the frontend run store refreshes via the useData events framework (mirrors
    # how rematch_sample emits match_reload). The room id resolves from the
    # returned _notification_data.sample_batch_id.
    success_reload=[("peak_assignment", "sample_batch_id")],
)
async def assign_sample_peaks(
    sample_item_id: str,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    """
    Run the two-stage peak assignment engine over a sample.

    Refuses immediately when the sample already has work in flight - by this
    worker (in-flight set, no round trip), by any other process sharing the
    database (advisory-lock claim), or by a run the database still records as
    non-terminal (durable admission) - rather than queueing a second full run
    behind the first. See :func:`_run_sample_assignment` for the engine.

    The three are complements. The claim is crash-safe but lives and dies with
    one process, so it cannot see an import assembling across several requests;
    durable run state can, which is what makes the two paths refuse each other
    instead of both writing a ledger for the same sample. A run left
    non-terminal by a process that died is released by the startup reaper, by
    the import abandon endpoint, or by retention's grace.

    :param sample_item_id: ID of the sample item to assign
    :param config: Optional run configuration; defaults are used when omitted
    :param independent_transaction: Flag for transaction handling
    :param user_id: Current user triggered operation (for user notifications)
    :param process_id: Process identifier for progress tracking
    :param parent_id: Parent process identifier
    :param run_id: A run the caller already created and is handing over, so the
        response it has already sent names the run this executes. Admission then
        asks whether *another* run holds the sample - the handed-over one is
        itself non-terminal, and would otherwise refuse its own engine.
    :return: A dictionary with run summary and status message
    """
    # Claimed before the first await: checking and adding either side of one would
    # let two concurrent requests both pass the check.
    if sample_item_id in _sample_assignments_in_flight:
        return await _already_running_result(sample_item_id, exclude_run_id=run_id)
    _sample_assignments_in_flight.add(sample_item_id)
    try:
        async with assignment_claim("sample", sample_item_id) as acquired:
            if not acquired:
                # Another worker holds the claim - the same refusal, discovered
                # one process further out.
                return await _already_running_result(
                    sample_item_id, exclude_run_id=run_id
                )
            # Checked under the claim so the read and the run creation that
            # follows it cannot interleave with another worker's pair.
            if (
                await in_flight_run_id(sample_item_id, exclude_run_id=run_id)
                is not None
            ):
                return await _already_running_result(
                    sample_item_id, exclude_run_id=run_id
                )
            return await _run_sample_assignment(
                sample_item_id=sample_item_id,
                config=config,
                user_id=user_id,
                process_id=process_id,
                parent_id=parent_id,
                run_id=run_id,
            )
    finally:
        _sample_assignments_in_flight.discard(sample_item_id)


async def _already_running_result(
    sample_item_id: str, exclude_run_id: str | None = None
) -> dict:
    """Refusal payload for a sample whose assignment is already in flight.

    Reports the in-flight run when one is visible, so a client can follow the
    run that is actually producing the ledger instead of just being declined.
    (A cross-worker refusal can race the other worker's run creation, so the
    id is best-effort.) An import assembling for this sample counts as in
    flight and is named here like any other run.

    :param sample_item_id: The sample that was refused.
    :param exclude_run_id: A run the caller owns, which is never what blocks it.
    """
    sample = await fetch_sample(sample_item_id)
    blocking_run_id = await in_flight_run_id(
        sample_item_id, exclude_run_id=exclude_run_id
    )
    message = (
        f"Peak assignment is already running for sample '{sample.sample_item_name}'."
    )
    runtime.logger.info(message)
    result: dict = {"status": "skipped", "message": message}
    if blocking_run_id is not None:
        result["data"] = {"peak_assignment_run_id": blocking_run_id}
    return result


async def plan_sample_decoy(sample, config: PeakAssignmentConfig) -> DecoyPlan:
    """What a decoy run of a sample inherits, and the channels it searches.

    Asked by the assign request, so a decoy that cannot bound its control is
    refused before a run exists, and again by the engine, which runs the plan
    it gets: the control may have moved on in between.

    :param sample: The sample view row.
    :param config: The decoy run's configuration; its ``decoy`` is set.
    :raises DecoyRefused: The decoy cannot bound the sample's latest run.
    :return: The plan (:class:`~mascope_backend.api.new.peak_assignments.decoy.DecoyPlan`).
    """
    control_run_id, control_config = await _latest_in_app_run(sample.sample_item_id)
    plan = plan_decoy(config.decoy, config, control_run_id, control_config)
    # What the control claimed on its batch's word, which the arm claims: a
    # run claims it after Stage A, on what its own rows read, and an arm's
    # rows read otherwise (`batch_decisions.claim_source_ions`).
    plan = replace(
        plan, control_source_claims=await control_source_claims(control_run_id)
    )
    # The context it searches under is its control's: resolved under the
    # control's settings and the context the control's batch and dataset named
    # (`decoy.recorded_named_context`), never what they name now. Refused where
    # even so its reagent profile or its context would not be the control's,
    # which a control records.
    channels = None
    if (control_config.get(RESOLVED_PROFILE_KEY) or {}).get("context") is not None:
        _, channels = await fetch_sample_mechanisms(sample)
        try:
            instrument_type = get_instrument_type(sample.filename)
        except ValueError:
            instrument_type = None
        differ = context_differs(
            resolve_profile(
                config,
                mechanism_notations=[m.ionization_mechanism for m in channels],
                instrument_type=instrument_type,
                polarity=sample.polarity,
                named_context=plan.named_context,
            ),
            control_config,
        )
        if differ is not None:
            raise DecoyRefused(f"{differ} (run '{control_run_id}')")
    if not config.decoy.swap_adducts:
        return plan
    if channels is None:
        _, channels = await fetch_sample_mechanisms(sample)
    profile_name = (control_config.get(RESOLVED_PROFILE_KEY) or {}).get("profile")
    if not channels or profile_name is None:
        raise DecoyRefused(
            "its control searched no channel of the sample's mode to swap"
        )
    candidates = wrong_adducts(
        sample.polarity,
        declared_channels(profile_name, [m.ionization_mechanism for m in channels]),
    )
    searched, swapped = swap_channels(
        channels,
        candidates,
        await fetch_mechanisms_by_notation(candidates, sample.polarity),
        fingerprint=get_reagent_profile(profile_name).detection,
    )
    if not searched:
        raise DecoyRefused(
            "the deployment holds no adduct of the sample's polarity that its "
            f"profile '{profile_name}' does not declare (of {', '.join(candidates)})"
            if candidates
            else f"its profile '{profile_name}' declares every adduct a decoy "
            "would swap in"
        )
    return replace(plan, mechanisms=searched, swapped=swapped)


async def _run_sample_assignment(
    sample_item_id: str,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    """:func:`_assign_sample` under the sample's current ionization setup.

    A sample bound under a version an edit has replaced moves onto the current
    one first, and the stage list reads labelled ions at the purity that
    version carries (``pipeline.run_stages``). A decoy run's shift is set here
    (``mascope_match.shifted_mz_axis``).
    """
    await move_to_current_versions(sample_item_ids=[sample_item_id])
    # A decoy that shifts the axis shifts it for every read the run makes, the
    # peak table and the targeted matcher's series alike; any other run reads
    # the axis as stored.
    decoy = config.decoy if config is not None else None
    with shifted_mz_axis(
        ppm=None if decoy is None else decoy.mz_shift_ppm,
        da=None if decoy is None else decoy.mz_shift_da,
    ):
        return await _assign_sample(
            sample_item_id,
            config,
            independent_transaction,
            user_id,
            process_id,
            parent_id,
            run_id,
        )


async def _assign_sample(
    sample_item_id: str,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    """
    Run the two-stage peak assignment engine over a sample.

    Every observed peak of the sample gets exactly one PeakAssignment row in
    a PeakAssignmentRun: Stage A assigns from the known target library,
    Stage B (optional) assigns the remainder via untargeted composition
    search, and leftover peaks are persisted as unassigned. The stages are the
    stage list's (``pipeline.RUN``, ``pipeline.DECOY`` for a decoy arm); this
    admits the sample, plans a decoy, and owns the run row's life.

    :param sample_item_id: ID of the sample item to assign
    :param config: Optional run configuration; defaults are used when omitted
    :param independent_transaction: Flag for transaction handling
    :param user_id: Current user triggered operation (for user notifications)
    :param process_id: Process identifier for progress tracking
    :param parent_id: Parent process identifier
    :param run_id: A run created by the caller to adopt instead of minting one,
        so a caller that already answered with a run id executes *that* run. The
        callers with nobody to answer (auto-processing on sample arrival, the
        batch loop) leave it unset and get a run of their own.
    :return: A dictionary with run summary and status message
    """
    sample = await fetch_sample(sample_item_id)
    config = config or PeakAssignmentConfig()

    reason = ineligible_reason(sample)
    decoy_plan: DecoyPlan | None = None
    if reason is None and config.decoy is not None:
        # Planned before the run, and refused as an ineligible sample is: a
        # decoy that cannot bound its control is not a run that failed.
        try:
            decoy_plan = await plan_sample_decoy(sample, config)
        except DecoyRefused as refused:
            reason = f"no decoy: {refused}"
    if reason is not None:
        # Returned rather than raised: raise_api_warning always raises, which
        # made the skip payload unreachable and its _notification_data - the
        # reload the decorator's success path delivers - silently lost. The
        # batch path reports its skips through returns for the same reason.
        #
        # The per-sample endpoint answers 422 on this before creating a run, so
        # an adopted run reaching here means the sample stopped being eligible in
        # between. Finalize it: leaving an adopted run non-terminal would block
        # its sample against every later run until the next startup.
        if run_id is not None:
            await _finalize_run(run_id, "failed", error=reason)
        message = (
            f"Peak assignment skipped for sample '{sample.sample_item_name}': {reason}."
        )
        runtime.logger.warning(message)
        return {
            "status": "skipped",
            "message": message,
            "_notification_data": {
                "sample_batch_id": sample.sample_batch_id,
                "sample_item_id": sample_item_id,
            },
        }

    run = (
        await _adopt_run(run_id)
        if run_id is not None
        else await _create_run(sample_item_id, config)
    )

    # Everything from here runs under the failure finalizer: an exception past
    # this point must mark the run 'failed', or it stays non-terminal - and
    # therefore blocks its sample - until the startup reaper. An adopted run
    # exists before this function is even called, so every fallible step it needs
    # belongs inside, not in front of, the try.
    try:
        runtime.logger.info(
            f"Starting peak assignment run '{run.peak_assignment_run_id}' "
            f"for sample '{sample.sample_item_name}'"
        )

        ctx = await run_stages(
            RunContext(
                path=RUN if decoy_plan is None else DECOY,
                sample=sample,
                config=config,
                run_id=run.peak_assignment_run_id,
                keep=keep_run,
                run=run,
                decoy_plan=decoy_plan,
                notification=UserNotification(
                    process_id=process_id,
                    parent_id=parent_id,
                    type="assign_sample_peaks",
                    status="pending",
                    message=f"Assigning peaks for sample '{sample.sample_item_name}'.",
                    data={
                        "sample_item_id": sample_item_id,
                        "peak_assignment_run_id": run.peak_assignment_run_id,
                        "_user_id": user_id,
                    },
                ),
            )
        )
        return ctx.result
    except asyncio.CancelledError:
        # CancelledError is a BaseException, so the failure handler below never
        # sees it - without this branch a cancelled run stayed 'running' until
        # the startup reaper, invisible to the read model and (correctly)
        # refused by the retention prune. Finalize to the distinct 'cancelled'
        # state and re-raise so cancellation still propagates. Shielded: the
        # task is already cancelled, and a second cancellation arriving during
        # the finalizer's own awaits must not abort the terminal write.
        await asyncio.shield(
            _end_run(run.peak_assignment_run_id, "cancelled", "Run was cancelled")
        )
        runtime.logger.info(
            f"Peak assignment run '{run.peak_assignment_run_id}' was cancelled"
        )
        raise
    except Exception as e:
        # Logged first, so the cause is on record whatever the write meets.
        runtime.logger.error(
            f"Peak assignment run '{run.peak_assignment_run_id}' failed: {e}"
        )
        await _end_run(run.peak_assignment_run_id, "failed", str(e))
        raise


async def auto_assign_sample_peaks(
    sample_item_id: str,
    user_id: int | None = None,
    parent_id: str | None = None,
) -> None:
    """Run Stage-A-only peak assignment as part of sample auto-processing.

    The auto-processing pipeline runs the assignment engine database-first
    (`run_untargeted=False`) only: Stage A reuses the matcher that already ran
    for the sample and is cheap on the small ACQUISITION target collections,
    whereas Stage B (untargeted composition enumeration) is the documented
    scaling risk and stays opt-in via an explicit sample/batch run.

    A failure here must never fail auto-processing - the sample is created,
    calibrated, and matched regardless - so any error is logged and swallowed.
    Called with ``independent_transaction=False`` so its progress nests under
    the parent process and no per-sample success toast is emitted; the parent
    orchestrator owns the ``peak_assignment_reload`` UI refresh.

    Does nothing unless the peak-assignment feature is enabled for this
    environment: ingest is the one path that would create assignment runs for
    every sample without anyone asking for them, so an env that has not opted in
    processes samples exactly as it did before the feature landed. An explicit
    sample or batch run stays available regardless.

    Two further settings bound what ingest writes without switching the feature
    off - see :func:`peak_assignment_on_ingest` and
    :func:`peak_assignment_ingest_max_peaks`. A sample they leave unassigned is
    logged and stays assignable explicitly; the peak count behind the ceiling is
    read from the peak file before any run row exists, so a skipped sample
    leaves nothing behind.

    :param sample_item_id: ID of the sample item to assign
    :param user_id: Current user triggered operation (for user notifications)
    :param parent_id: Parent process identifier for progress nesting
    """
    if not peak_assignment_enabled():
        return
    if not peak_assignment_on_ingest():
        runtime.logger.debug(
            f"Ingest-time peak assignment is off; sample '{sample_item_id}' is "
            "left for an explicit run."
        )
        return
    try:
        ceiling = peak_assignment_ingest_max_peaks()
        if ceiling:
            sample = await fetch_sample(sample_item_id)
            n_peaks = await asyncio.to_thread(count_sample_peaks, sample)
            if n_peaks > ceiling:
                runtime.logger.warning(
                    f"Skipping ingest-time peak assignment for sample "
                    f"'{sample.sample_item_name}': {n_peaks} detected peaks exceed "
                    f"the ingest ceiling of {ceiling} "
                    "(peak_assignment_ingest_max_peaks). The sample can still be "
                    "assigned with an explicit run."
                )
                return
        if peak_assignment_ingest_ledger() == INGEST_LEDGER_BATCH:
            # Fold straight into the batch ledger and write no per-sample run;
            # the Sample view is served from the members instead (fold_view).
            await fold_sample_peaks_without_run(sample_item_id)
            return
        # assign_sample_peaks folds the completed run into the batch peaks itself,
        # so the batch overview stays current as samples arrive.
        await assign_sample_peaks(
            sample_item_id=sample_item_id,
            config=PeakAssignmentConfig(run_untargeted=False),
            independent_transaction=False,
            user_id=user_id,
            process_id=gen_id(8),
            parent_id=parent_id,
        )
    except Exception as e:
        # Isolate assignment failures from the processing lifecycle.
        runtime.logger.warning(
            f"Auto peak assignment failed for sample '{sample_item_id}': {e}"
        )


async def keep_run(ctx: RunContext) -> dict:
    """A run keeps its ledger: what it recorded of each stage on its run, the
    rows, and then its run completed and folded into the batch ledger.

    :return: The run's outcome, as the assign task reports it.
    """
    sample = ctx.sample
    judged = ctx.judged
    plan = ctx.decoy_plan
    search = ctx.search
    fragments = ctx.fragments
    await run_config._record_resolved_profile(
        ctx.run_id,
        ctx.config,
        ctx.resolved_profile,
        None if search is None else search.scope,
        measurement_model_record(
            judged.model,
            ranked_at=ctx.model,
            scoring=(
                search.snapshot
                if search is not None and search.snapshot is not None
                else scoring_snapshot(ctx.scoring)
            ),
            gate=judged.mass_calibration,
        ),
        judged.cross_channel,
        ctx.tiering,
        judged.partner_gate,
        fragments.summary if fragments.summary["ladders"] else None,
        ctx.ionization_setup,
        ctx.envelope.record(),
        None if plan is None else plan.record(),
        ctx.batch.record(ctx.batch_applied),
    )
    await ctx.progress(0.8)
    all_assignments, committed_rows, unassigned_assignments = ledger_rows(ctx)
    # Insert owners before children: owner_peak_assignment_id is a
    # self-referential FK validated per row during the bulk insert.
    all_assignments.sort(key=lambda row: row["owner_peak_assignment_id"] is not None)
    if all_assignments:
        async with async_session() as session:
            await session.execute(insert(PeakAssignment), all_assignments)
            # The curve the rows were scored against, committed with them. It is
            # the run's to record - one curve serves a whole run - but it has to
            # land in the same transaction as the ledger, because a run
            # interrupted after this point keeps its rows without ever being
            # finalized: the startup reaper marks it failed and leaves the
            # ledger standing, and a read that names that run by id serves rows
            # whose P(correct) would then name no curve at all.
            await session.execute(
                update(PeakAssignmentRun)
                .where(PeakAssignmentRun.peak_assignment_run_id == ctx.run_id)
                .values(confidence_calibration=ctx.confidence_calibration)
            )
            await session.commit()

    await _finalize_run(ctx.run_id, "completed")
    await ctx.progress(1.0)

    # Fold this completed run into the batch peaks so the batch overview
    # reflects every assignment path: sample arrival, an explicit single-sample
    # re-assign, and a batch assign (which loops the run). Isolated -- a fold-in
    # failure must never fail or un-complete the assignment itself. A decoy is
    # never folded: its rows are the sample read wrong on purpose, and the
    # batch ledger is the sample's result.
    try:
        if plan is None:
            await fold_sample_into_batch_peaks(sample.sample_item_id)
    except Exception as fold_error:  # noqa: BLE001 - fold-in is best-effort
        runtime.logger.warning(
            f"Batch-peak fold-in failed for sample '{sample.sample_item_id}' "
            f"(run '{ctx.run_id}'): {fold_error}"
        )

    database_assigned = sum(
        1 for row in committed_rows if row.get("source") == SOURCE_DATABASE
    )
    untargeted_assigned = len(committed_rows) - database_assigned
    message = (
        f"Assigned peaks for sample '{sample.sample_item_name}': "
        f"{database_assigned} from the target library, "
        f"{untargeted_assigned} untargeted, "
        f"{len(unassigned_assignments)} unassigned "
        f"({len(all_assignments)} peaks total)."
    )
    runtime.logger.info(message)
    return {
        "status": "success",
        "message": message,
        "data": {
            "peak_assignment_run_id": ctx.run_id,
            "total_peaks": len(all_assignments),
            "database_assigned": database_assigned,
            "untargeted_assigned": untargeted_assigned,
            "unassigned": len(unassigned_assignments),
        },
        "_notification_data": {
            "sample_batch_id": sample.sample_batch_id,
            "sample_item_id": sample.sample_item_id,
            "peak_assignment_run_id": ctx.run_id,
        },
    }
