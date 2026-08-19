"""
Import of assignment runs computed outside Mascope.

An external engine reads a sample's peaks through the SDK, computes a ledger,
and publishes it back here as a first-class ``PeakAssignmentRun`` - same tables,
same read model, same batch fold-in as an in-app run, and always attributable
through the run's ``engine``. Design: ``docs/dev/sdk_peak_assignment.md`` §8.

**One logical import, one or more requests.** A dense sample's full ledger is
tens of thousands of rows, too large for one body and too much to serialize
through Pydantic in one go, so the endpoint caps rows per request and assembles:

- the first request creates the run in ``importing`` and returns its id;
- follow-ups carry that id and the next ``chunk.index``;
- the request marked ``chunk.complete`` finalizes - payload-wide validation,
  owner resolution, ``completed``, batch fold-in.

Every request has to survive being sent twice, because the SDK retries POSTs on
timeouts and exposes no way to opt out - and a read timeout is exactly the case
where the body *was* delivered and the server may already have applied it. Three
mechanisms cover the three kinds of request:

- an **append** is addressed by ``chunk.index``, an offset in rows: re-sending
  the chunk that produced the current row count is an idempotent no-op that
  reports that count, and any other mismatch is refused so the client
  resynchronises. Without it a retry duplicates rows onto the unique constraint
  and fails a healthy import.
- a **create** has no run id yet to be addressed by, so an offset cannot help:
  a retried create is byte-identical to the original and would mint a second
  run, which admission then refuses - wedging the client, which never learned
  the first run's id. It is keyed instead by the client's own ``import_id``,
  which resolves to the run already created for it.
- a **finalize** can be slow (payload-wide validation, owner resolution and the
  fold-in), which is what makes it a likely read-timeout victim, so re-sending
  it returns the original success rather than erroring - and does not run the
  fold-in a second time.

Assembly needs its own status. ``running`` would not do: the startup reaper
fails every ``running`` row on the correct assumption that nothing can
legitimately be running before workers spawn, which is false for a client-paced
upload - a routine deploy would kill one. ``importing`` is skipped by the reaper
and held by retention under its own grace instead, and the abandon endpoint
releases one that will never finish without waiting for that grace.

What an import is trusted with stops short of the values this server presents as
its own: an imported row may say what the engine *found* - formula, role, fit
score, tier under declared bands - but not the calibrated confidence the ledger
renders, and an imported run's verdicts stay out of the instrument-wide
calibration pool.
"""

import asyncio
from datetime import datetime as dt
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    DuplicateException,
    NotFoundException,
)
from mascope_backend.api.new.peak_assignments.admission import (
    assignment_claim,
    in_flight_run_id,
)
from mascope_backend.api.new.peak_assignments.import_validation import (
    chunk_offset_error,
    duplicate_peak_ids,
    is_chunk_replay,
    json_size_error,
    normalize_engine,
    self_owner_errors,
    strip_server_owned_provenance,
    tier_coherence_error,
    unknown_peak_ids,
    unresolved_owner_error,
)
from mascope_backend.db import (
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime


class UnprocessableImportException(HTTPException):
    """A well-formed import payload this server refuses to store (422).

    Distinct from a 409: a conflict says "not now", this says "not this". The
    client cannot fix a 422 by retrying, only by sending different rows.
    """

    def __init__(self, detail: str):
        super().__init__(status_code=422, detail=detail)


async def _load_peak_ids(sample) -> set[str]:
    """Every peak id the sample's peak file holds.

    Deliberately the id-only read: validation needs the id set, while the
    engine's full load additionally opens the raw data source per call - on
    every chunk, and again on every retry - only to compute an averaging
    divisor. Offloaded to a thread because it is blocking file I/O in a request
    handler.

    :param sample: The sample being imported into.
    :return: The sample's peak ids.
    """
    peak_data = await asyncio.to_thread(
        extract_peaks,
        sample.filename,
        sample.polarity,
        sample.t0,
        sample.t1,
        areas=False,
        heights=False,
        average=False,
    )
    return {str(peak_id) for peak_id in peak_data.peak_ids}


async def _validate_mechanisms(rows, sample) -> None:
    """Reject supplied ionization mechanism ids that cannot apply to this sample.

    ``ionization_mechanism_id`` is nullable - an external engine names adducts by
    notation, not by a deployment's mechanism ids - but a *supplied* id must
    exist and must carry the sample's polarity, which is what the in-app engine
    satisfies structurally. Checked here so a bad id is a 422 naming it, rather
    than an IntegrityError surfacing as a 500 out of the bulk insert once the
    whole payload has been uploaded.

    Membership of the sample's configured ionization *mode* is deliberately not
    required on top of polarity: a mode's mechanism list is a deployment's
    narrowing of what to search for rather than a property of the measurement,
    and a sample may carry no mode at all - so enforcing it would refuse a
    correct adduct for a reason that says nothing about the ledger.

    :param rows: The chunk's rows.
    :param sample: The sample being imported into.
    :raises UnprocessableImportException: On an unknown or mismatched id.
    """
    supplied = {
        row.ionization_mechanism_id
        for row in rows
        if row.ionization_mechanism_id is not None
    }
    if not supplied:
        return

    async with async_session() as session:
        found = (
            await session.execute(
                select(
                    IonizationMechanism.ionization_mechanism_id,
                    IonizationMechanism.ionization_mechanism_polarity,
                ).where(IonizationMechanism.ionization_mechanism_id.in_(supplied))
            )
        ).all()

    polarity_by_id = {mech_id: polarity for mech_id, polarity in found}
    if unknown := sorted(supplied - set(polarity_by_id)):
        raise UnprocessableImportException(
            f"ionization_mechanism_id {_names(unknown)} is not a known mechanism "
            "on this deployment; leave it null when the adduct cannot be "
            "resolved (the notation still travels in ion_formula)"
        )
    mismatched = sorted(
        mech_id
        for mech_id, polarity in polarity_by_id.items()
        if polarity != sample.polarity
    )
    if mismatched:
        raise UnprocessableImportException(
            f"ionization_mechanism_id {_names(mismatched)} does not match the "
            f"sample's polarity '{sample.polarity}'"
        )


def _names(values, limit: int = 5) -> str:
    """Render a few offending values for an error message.

    :param values: The values to name.
    :param limit: Most values to show.
    :return: A comma-separated, quoted list.
    """
    shown = ", ".join(f"'{value}'" for value in list(values)[:limit])
    extra = len(values) - limit
    return shown if extra <= 0 else f"{shown} (and {extra} more)"


def _validate_rows(rows, bands: dict, known_peak_ids: set[str]) -> None:
    """Apply the per-row payload rules to one chunk.

    :param rows: The chunk's rows.
    :param bands: The run's declared tier bands.
    :param known_peak_ids: Every peak id the sample has.
    :raises UnprocessableImportException: On the first rule a row breaks.
    """
    peak_ids = [row.sample_peak_id for row in rows]

    if duplicates := duplicate_peak_ids(peak_ids):
        raise UnprocessableImportException(
            f"peak {_names(duplicates)} is assigned more than once in this "
            "chunk; a run holds at most one row per peak"
        )
    if unknown := unknown_peak_ids(peak_ids, known_peak_ids):
        raise UnprocessableImportException(
            f"peak {_names(unknown)} is not in this sample's peak file"
        )
    if self_owned := self_owner_errors(rows):
        raise UnprocessableImportException(self_owned[0])

    for row in rows:
        if error := tier_coherence_error(
            row.tier, row.fit_score, bands["identified"], bands["candidate"]
        ):
            raise UnprocessableImportException(
                f"row for peak '{row.sample_peak_id}': {error}"
            )


def _resolved_bands(body, run: PeakAssignmentRun | None) -> dict:
    """The tier bands this chunk's rows are checked against.

    Taken from the body on the request that creates the run, and from the run
    itself afterwards, so every chunk of one import is judged by the same bands
    the run was opened with.

    :param body: The request body.
    :param run: The run being appended to, or None when creating one.
    :return: The bands as a dict.
    :raises UnprocessableImportException: When no bands are available.
    """
    if run is None:
        if body.tier_bands is None:
            raise UnprocessableImportException(
                "tier_bands is required: every row's tier is validated against "
                "the thresholds the producing engine tiered with"
            )
        return body.tier_bands.model_dump()

    bands = run.tier_bands or {}
    if "identified" not in bands or "candidate" not in bands:
        raise UnprocessableImportException(
            f"run '{run.peak_assignment_run_id}' carries no tier_bands, so its "
            "rows' tiers cannot be checked"
        )
    return bands


def _row_values(row, run_id: str, sample_item_id: str) -> dict:
    """Shape one imported row for the bulk insert.

    The ids are minted here and the owner link is left unresolved: a child can
    reference an owner that has not arrived yet, and the self-referential
    foreign key is validated per row at insert, so the reference is staged on
    ``owner_sample_peak_id`` and resolved once the whole import has landed.

    :param row: The validated payload row.
    :param run_id: The run being assembled.
    :param sample_item_id: The sample being imported into.
    :return: Column values for one ``PeakAssignment``.
    """
    return {
        "peak_assignment_id": gen_id(32),
        "peak_assignment_run_id": run_id,
        "sample_item_id": sample_item_id,
        "sample_peak_id": row.sample_peak_id,
        "sample_peak_mz": row.sample_peak_mz,
        "sample_peak_intensity": row.sample_peak_intensity,
        "sample_peak_tof": row.sample_peak_tof,
        "role": row.role,
        "assigned_formula": row.assigned_formula,
        "ion_formula": row.ion_formula,
        "ionization_mechanism_id": row.ionization_mechanism_id,
        "isotope_label": row.isotope_label,
        "isotope_formula": row.isotope_formula,
        "source": row.source,
        "fit_score": row.fit_score,
        "mz_error_ppm": row.mz_error_ppm,
        "abundance_error": row.abundance_error,
        "tier": row.tier,
        "target_compound_id": row.target_compound_id,
        "target_ion_id": row.target_ion_id,
        "owner_peak_assignment_id": None,
        "owner_sample_peak_id": row.owner_sample_peak_id,
        "alternatives": row.alternatives,
        "provenance": strip_server_owned_provenance(row.provenance),
    }


async def _resolve_owners(session, run_id: str) -> None:
    """Turn staged owner references into owner assignment ids.

    Set-based rather than a Python pass over the run's rows: an import can be
    tens of thousands of rows, and the reference is a plain join on
    ``sample_peak_id`` within the run.

    :param session: Open session; the caller commits.
    :param run_id: The run being finalized.
    :raises UnprocessableImportException: When a reference matches no row.
    """
    owner = PeakAssignment.__table__.alias("owner")
    await session.execute(
        update(PeakAssignment)
        .where(
            PeakAssignment.peak_assignment_run_id == run_id,
            PeakAssignment.owner_sample_peak_id.is_not(None),
            owner.c.peak_assignment_run_id == run_id,
            owner.c.sample_peak_id == PeakAssignment.owner_sample_peak_id,
        )
        .values(owner_peak_assignment_id=owner.c.peak_assignment_id)
    )

    unresolved = (
        (
            await session.execute(
                select(PeakAssignment.owner_sample_peak_id)
                .where(
                    PeakAssignment.peak_assignment_run_id == run_id,
                    PeakAssignment.owner_sample_peak_id.is_not(None),
                    PeakAssignment.owner_peak_assignment_id.is_(None),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if unresolved:
        raise UnprocessableImportException(unresolved_owner_error(unresolved))


async def _run_for_import_key(session, sample_item_id: str, import_key: str):
    """The run already created for this client's import id, if any.

    What makes the create idempotent: a retried create carries the same key, so
    it resolves here instead of minting a second run. Persisted on the run
    rather than held in memory, so the dedupe survives a worker restart.

    :param session: Open session.
    :param sample_item_id: The sample being imported into.
    :param import_key: The client's id for this logical import.
    :return: The run, or None when this key is new.
    """
    return (
        await session.execute(
            select(PeakAssignmentRun).where(
                PeakAssignmentRun.sample_item_id == sample_item_id,
                PeakAssignmentRun.import_key == import_key,
            )
        )
    ).scalar_one_or_none()


def _import_result(run_id: str, run_status: str, rows: int, message: str) -> dict:
    """Standard envelope for one import request.

    :param run_id: The run being assembled.
    :param run_status: The run's status after this request.
    :param rows: Rows the run now holds.
    :param message: Human-readable summary.
    :return: Status envelope.
    """
    return {
        "status": "success",
        "message": message,
        "results": 1,
        "data": [
            {
                "peak_assignment_run_id": run_id,
                "run_status": run_status,
                "rows": rows,
            }
        ],
    }


async def _load_import_run(session, run_id: str, sample) -> PeakAssignmentRun:
    """Fetch the run a follow-up chunk names, or refuse.

    :param session: Open session.
    :param run_id: The run id the client supplied.
    :param sample: The sample the request addressed.
    :return: The run.
    :raises NotFoundException: When the run is unknown or belongs elsewhere.
    """
    run = await session.get(PeakAssignmentRun, run_id)
    if run is None or run.sample_item_id != sample.sample_item_id:
        raise NotFoundException(
            f"Peak assignment run '{run_id}' not found for sample "
            f"'{sample.sample_item_name}'"
        )
    return run


async def _staged_row_count(session, run_id: str) -> int:
    """Rows an assembling run already holds.

    :param session: Open session.
    :param run_id: The run being assembled.
    :return: The row count.
    """
    return int(
        await session.scalar(
            select(func.count())
            .select_from(PeakAssignment)
            .where(PeakAssignment.peak_assignment_run_id == run_id)
        )
        or 0
    )


async def _open_run(sample, body, engine: str) -> PeakAssignmentRun:
    """Create the run the first request of an import assembles into.

    Called only once this request's rows have already passed validation, so a
    payload the server was going to refuse does not leave an ``importing`` run
    behind - which would block every later import and in-app assign for the
    sample until someone abandoned it.

    :param sample: The sample being imported into.
    :param body: The request body.
    :param engine: The validated engine name.
    :return: The created run.
    """
    run = PeakAssignmentRun(
        peak_assignment_run_id=gen_id(16),
        sample_item_id=sample.sample_item_id,
        engine=engine,
        engine_version=body.engine_version,
        status="importing",
        # Stored verbatim: the server never reads this and never writes into it,
        # so what is on the record is what the engine ran with. The client's
        # import id is therefore kept in its own column rather than folded in
        # here, for the same reason the calibration disclosure is.
        config=body.config,
        tier_bands=body.tier_bands.model_dump(),
        calibration=body.calibration,
        import_key=body.chunk.import_id or None,
        peak_assignment_run_utc_created=dt.now(timezone.utc),
    )
    async with async_session() as session:
        session.add(run)
        await session.commit()
    return run


def _validate_new_run(sample, body) -> str:
    """Check everything about a new import that does not depend on its rows.

    :param sample: The sample being imported into.
    :param body: The request body.
    :return: The validated engine name.
    :raises UnprocessableImportException: When the run may not be opened.
    """
    # Eligibility is stated, not inherited from peak validation: that argument
    # fails for a zero-row chunk, and "a blank has no peaks" is a consequence of
    # how blanks are ingested rather than an invariant anything enforces. The
    # calibration clause of the in-app rule is the one an import deliberately
    # bypasses - it calibrates client-side and discloses that on the run - so
    # only the blank clause applies here.
    if sample.instrument_function_id is None:
        raise UnprocessableImportException(
            f"Sample '{sample.sample_item_name}' is a blank (no measured peaks), "
            "so there is nothing for an imported run to assign."
        )
    if body.chunk.index != 0:
        raise UnprocessableImportException(
            f"chunk index {body.chunk.index} without a run_id: the request that "
            "creates a run starts at index 0"
        )
    if body.calibration is None:
        raise UnprocessableImportException(
            "calibration is required: an import bypasses the server-side m/z "
            "verification gate, so what the engine calibrated against is "
            "disclosed on the run instead. An engine that calibrated nothing "
            "records that."
        )
    engine, engine_error = normalize_engine(body.engine)
    if engine_error:
        raise UnprocessableImportException(engine_error)
    for field, value in (("config", body.config), ("calibration", body.calibration)):
        if error := json_size_error(field, value):
            raise UnprocessableImportException(error)
    return engine


@api_controller()
async def import_assignment_run(
    sample_item_id: str,
    body,
    user_id: int | None = None,
) -> dict:
    """Accept one request of an externally computed assignment run.

    Creates the run on the first request, appends rows on the next ones, and
    finalizes on the one marked complete. See the module docstring for the
    protocol and why it is shaped this way.

    :param sample_item_id: The sample the run assigns.
    :param body: The import request body.
    :param user_id: The importing user, for the log line.
    :return: Status envelope with the run id, its status, and its row count.
    """
    sample = await fetch_sample(sample_item_id)

    # One request is exactly the span the advisory claim is good for, and taking
    # it here is what stops two workers opening two runs for the same sample or
    # appending to one run at once. Durable run state is the part that spans
    # requests; this is the part that spans processes.
    async with assignment_claim("sample", sample_item_id) as acquired:
        if not acquired:
            raise DuplicateException(
                f"Another peak assignment operation is in progress for sample "
                f"'{sample.sample_item_name}'. Retry when it has finished."
            )
        return await _import_chunk(sample, body, user_id)


async def _import_chunk(sample, body, user_id: int | None) -> dict:
    """Apply one import request to the run it addresses.

    :param sample: The sample being imported into.
    :param body: The request body.
    :param user_id: The importing user, for the log line.
    :return: Status envelope for this request.
    """
    run: PeakAssignmentRun | None = None
    staged = 0
    engine = ""

    if body.chunk.run_id is not None:
        async with async_session() as session:
            run = await _load_import_run(session, body.chunk.run_id, sample)
    elif body.chunk.import_id:
        # A create the client may have sent before. Resolving it here turns the
        # retry into a replay of that run's first chunk, which the offset rules
        # below already answer correctly.
        async with async_session() as session:
            run = await _run_for_import_key(
                session, sample.sample_item_id, body.chunk.import_id
            )

    creating = run is None
    if creating:
        engine = _validate_new_run(sample, body)
        if (blocking_run := await in_flight_run_id(sample.sample_item_id)) is not None:
            raise DuplicateException(
                f"Peak assignment run '{blocking_run}' is already in flight for "
                f"sample '{sample.sample_item_name}'. Wait for it to finish, or "
                "delete it if it is an abandoned import."
            )
    else:
        async with async_session() as session:
            staged = await _staged_row_count(session, run.peak_assignment_run_id)
        if (replay := _completed_replay(run, body, staged)) is not None:
            return replay
        if run.status != "importing":
            raise DuplicateException(
                f"Peak assignment run '{run.peak_assignment_run_id}' is "
                f"'{run.status}', not an import in progress."
            )
        if error := chunk_offset_error(body.chunk.index, staged, len(body.rows)):
            raise DuplicateException(error)
        if is_chunk_replay(body.chunk.index, staged, len(body.rows)):
            return _import_result(
                run.peak_assignment_run_id,
                run.status,
                staged,
                f"Chunk at index {body.chunk.index} was already applied; run "
                f"'{run.peak_assignment_run_id}' holds {staged} row(s).",
            )

    bands = _resolved_bands(body, run)
    values: list[dict] = []
    if body.rows:
        known_peak_ids = await _load_peak_ids(sample)
        total = staged + len(body.rows)
        if total > len(known_peak_ids):
            raise UnprocessableImportException(
                f"this import would hold {total} rows for a sample with "
                f"{len(known_peak_ids)} peak(s); a run holds at most one row "
                "per peak"
            )
        _validate_rows(body.rows, bands, known_peak_ids)
        await _validate_mechanisms(body.rows, sample)
    elif body.chunk.complete and staged == 0:
        # Caught before a run is opened: an import that carries nothing is a
        # 422, not a completed empty run.
        raise UnprocessableImportException(
            "an import must carry at least one row; an empty run is not a ledger"
        )

    if creating:
        run = await _open_run(sample, body, engine)
    run_id = run.peak_assignment_run_id

    if body.rows:
        values = [_row_values(row, run_id, sample.sample_item_id) for row in body.rows]
        try:
            async with async_session() as session:
                await session.execute(insert(PeakAssignment), values)
                await session.commit()
        except IntegrityError as error:
            # The unique constraint on (run, peak) backstops the duplicate check
            # across chunks, where an in-memory pass cannot see the earlier
            # ones. Reported as the same 422 an in-chunk duplicate gets.
            raise UnprocessableImportException(
                "a peak in this chunk is already assigned by an earlier chunk of "
                "this import; a run holds at most one row per peak"
            ) from error
        staged += len(body.rows)

    if not body.chunk.complete:
        return _import_result(
            run_id,
            "importing",
            staged,
            f"Staged {len(body.rows)} row(s) into import run '{run_id}'; "
            f"{staged} row(s) so far.",
        )
    return await _finalize_import(sample, run_id, staged, user_id)


def _completed_replay(run: PeakAssignmentRun, body, staged: int) -> dict | None:
    """Answer a finalize whose response the client lost, if that is what this is.

    Finalizing is the slowest request of an import - payload-wide validation,
    owner resolution and the batch fold-in - which makes it the one most likely
    to outlive a client's read timeout and be retried underneath the server. So
    a repeat of it returns the original success instead of erroring, and returns
    before the fold-in, which must not run twice.

    A repeat is recognised by the offset it claims spanning exactly the rows the
    run holds. That covers both shapes a finalize takes: one carrying the last
    chunk of rows, and an empty one sent after the rows were all appended.

    :param run: The addressed run.
    :param body: The request body.
    :param staged: Rows the run holds.
    :return: The original answer, or None when this is not a replay.
    :raises DuplicateException: When a completed run is sent fresh rows.
    """
    if run.status != "completed":
        return None
    if body.chunk.complete and body.chunk.index + len(body.rows) == staged:
        return _import_result(
            run.peak_assignment_run_id,
            run.status,
            staged,
            f"Import of run '{run.peak_assignment_run_id}' was already completed "
            f"with {staged} row(s).",
        )
    raise DuplicateException(
        f"Peak assignment run '{run.peak_assignment_run_id}' is already "
        "completed and cannot take more rows."
    )


async def _finalize_import(sample, run_id: str, staged: int, user_id: int | None):
    """Close an assembled import: resolve owners, complete it, fold it in.

    :param sample: The sample being imported into.
    :param run_id: The run being finalized.
    :param staged: Rows the run holds.
    :param user_id: The importing user, for the log line.
    :return: Status envelope for the finalizing request.
    """
    if staged == 0:
        raise UnprocessableImportException(
            "an import must carry at least one row; an empty run is not a ledger"
        )

    async with async_session() as session:
        await _resolve_owners(session, run_id)
        await session.execute(
            update(PeakAssignmentRun)
            .where(PeakAssignmentRun.peak_assignment_run_id == run_id)
            .values(
                status="completed",
                peak_assignment_run_utc_completed=dt.now(timezone.utc),
            )
        )
        await session.commit()

    runtime.logger.info(
        f"Imported peak assignment run '{run_id}' for sample "
        f"'{sample.sample_item_name}': {staged} row(s), by user {user_id}"
    )

    # Fold the completed run into the batch peaks, exactly as an in-app run's
    # completion does, so an imported run reaches the batch overview the moment
    # it lands. Isolated the same way: a fold-in failure is logged and never
    # fails or un-completes the import. Note what the fold-in does with a
    # partial import - it replaces this sample's whole contribution to the batch
    # overview, so peaks the import left out are withdrawn from it.
    try:
        from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
            fold_sample_into_batch_peaks,
        )

        await fold_sample_into_batch_peaks(sample.sample_item_id)
    except Exception as fold_error:  # noqa: BLE001 - fold-in is best-effort
        runtime.logger.warning(
            f"Batch-peak fold-in failed for sample '{sample.sample_item_id}' "
            f"(imported run '{run_id}'): {fold_error}"
        )

    return _import_result(
        run_id,
        "completed",
        staged,
        f"Imported peak assignment run '{run_id}' for sample "
        f"'{sample.sample_item_name}' with {staged} row(s).",
    )


@api_controller()
async def abandon_import_run(sample_item_id: str, peak_assignment_run_id: str) -> dict:
    """Delete an import that will never finish, with its staged rows.

    The retention grace alone is not an answer: admission refuses on any
    non-terminal run and the reaper deliberately skips ``importing``, so a
    client that died mid-upload - or that simply lost the run id it was handed -
    would block every later import *and* in-app assign for this sample until the
    next nightly prune. A client that still holds the id can always carry on
    appending; this is the way out for one that cannot.

    Restricted to ``importing`` on purpose: a completed run is ledger data, and
    removing that is retention's business, not a client's.

    :param sample_item_id: The sample the run belongs to.
    :param peak_assignment_run_id: The run to abandon.
    :return: Status envelope naming the run and the rows reclaimed.
    """
    sample = await fetch_sample(sample_item_id)
    async with async_session() as session:
        run = await _load_import_run(session, peak_assignment_run_id, sample)
        if run.status != "importing":
            raise DuplicateException(
                f"Peak assignment run '{peak_assignment_run_id}' is "
                f"'{run.status}', not an import in progress. Only an unfinished "
                "import can be abandoned; completed runs age out through "
                "retention."
            )
        staged = await _staged_row_count(session, peak_assignment_run_id)
        # The ledger rows go with it: peak_assignment.peak_assignment_run_id is
        # ON DELETE CASCADE.
        await session.execute(
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.peak_assignment_run_id == peak_assignment_run_id
            )
        )
        await session.commit()

    message = (
        f"Abandoned import run '{peak_assignment_run_id}' for sample "
        f"'{sample.sample_item_name}', releasing {staged} staged row(s)."
    )
    runtime.logger.info(message)
    return {
        "status": "success",
        "message": message,
        "results": 1,
        "data": [
            {
                "peak_assignment_run_id": peak_assignment_run_id,
                "run_status": "abandoned",
                "rows": staged,
            }
        ],
    }
