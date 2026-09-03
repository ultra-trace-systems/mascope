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
  run. It is keyed instead by the client's own ``import_id``, which resolves to
  the run already created for it. That key is **required**, because the damage
  without one depends on the shape of the import and the worse case is silent:
  a chunked create leaves the second run non-terminal, so admission refuses it
  and the client is merely wedged, but a single-request create (rows and
  ``complete`` together, the slim-ledger shape) finishes as ``completed`` -
  which admission does not refuse - so the retry lands a duplicate ledger and a
  second batch fold-in with no error anywhere.
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
from collections import OrderedDict
from datetime import datetime as dt
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError

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
from mascope_backend.api.new.peak_assignments.engine import (
    plausibility_for,
    tier_for_evidence,
)
from mascope_backend.api.new.peak_assignments.import_validation import (
    OWNED_ROLE,
    chunk_offset_error,
    duplicate_peak_ids,
    is_chunk_replay,
    json_size_error,
    normalize_engine,
    owner_link_errors,
    owner_of_owner_error,
    row_evidence,
    strip_server_owned_provenance,
    tier_coherence_error,
    unknown_peak_ids,
    unresolved_owner_error,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_BELOW_ASSIGNABILITY,
    TIER_UNASSIGNED,
    normalize_tier_bands,
)
from mascope_backend.db import (
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    TargetCompound,
    TargetIon,
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


#: Peak id sets held for the life of one assembling import, keyed by run id.
#:
#: Every chunk validates its rows against the sample's peak ids, and re-reading
#: the peak file per chunk means tens of blocking zarr reads for one dense
#: ledger - each one inside the advisory claim, so a pooled connection is held
#: across it. The set does not change while an import is running: the ledger is
#: being written against exactly that set of peaks.
#:
#: Keyed by *run*, not by sample, deliberately. A sample-keyed cache would
#: outlive the import and go stale if the sample were re-processed; this one is
#: dropped when the run finalizes or is abandoned, so its lifetime is the window
#: in which the peak set has to be stable anyway. It is a cache, not a store: a
#: miss (another worker, or an entry evicted by the bound) just reads the file.
_PEAK_IDS_BY_RUN: "OrderedDict[str, set[str]]" = OrderedDict()

#: Assembling imports whose peak ids one worker holds. Bounded so a worker
#: cannot accumulate sets for runs it never sees finish - admission allows one
#: in-flight run per sample, so this is generous.
_PEAK_ID_CACHE_MAX_RUNS = 32


def _cache_peak_ids(run_id: str, peak_ids: set[str]) -> None:
    """Hold a run's peak id set for its remaining chunks.

    :param run_id: The run being assembled.
    :param peak_ids: The sample's peak ids.
    """
    _PEAK_IDS_BY_RUN[run_id] = peak_ids
    _PEAK_IDS_BY_RUN.move_to_end(run_id)
    while len(_PEAK_IDS_BY_RUN) > _PEAK_ID_CACHE_MAX_RUNS:
        _PEAK_IDS_BY_RUN.popitem(last=False)


def _release_peak_ids(run_id: str) -> None:
    """Drop a finished run's cached peak ids.

    :param run_id: The run that finalized or was abandoned.
    """
    _PEAK_IDS_BY_RUN.pop(run_id, None)


async def _known_peak_ids(sample, run_id: str | None) -> set[str]:
    """The sample's peak ids, from this run's cache when it has one.

    :param sample: The sample being imported into.
    :param run_id: The run being appended to, or None while creating one.
    :return: The sample's peak ids.
    """
    if run_id is not None and (cached := _PEAK_IDS_BY_RUN.get(run_id)) is not None:
        _PEAK_IDS_BY_RUN.move_to_end(run_id)
        return cached
    peak_ids = await _load_peak_ids(sample)
    if run_id is not None:
        _cache_peak_ids(run_id, peak_ids)
    return peak_ids


#: Every foreign-key column an imported row can populate, as
#: ``(payload field, model column)``. The insert is the only other thing that
#: would catch a bad id, and it catches all of them as one indistinguishable
#: IntegrityError - see :func:`_insert_refusal` for why that is not a usable
#: error to report. Adding a reference column to ``ImportAssignmentRow`` means
#: adding it here.
_REFERENCE_COLUMNS = (
    ("ionization_mechanism_id", IonizationMechanism.ionization_mechanism_id),
    ("target_compound_id", TargetCompound.target_compound_id),
    ("target_ion_id", TargetIon.target_ion_id),
)


async def _validate_references(rows, sample) -> None:
    """Reject supplied foreign keys this deployment cannot honour.

    Each of these ids is nullable - an external engine names adducts by
    notation rather than by a deployment's mechanism ids, and it may know
    nothing of the curated target library - but a *supplied* id must exist.
    Checked here so a bad id is a 422 naming the field and the value, rather
    than an IntegrityError out of the bulk insert once the whole payload has
    been uploaded, which arrives with no reliable way to say which of the three
    columns was at fault.

    ``ionization_mechanism_id`` carries one rule beyond existence: it must match
    the sample's polarity, which is what the in-app engine satisfies
    structurally. Membership of the sample's configured ionization *mode* is
    deliberately not required on top of that: a mode's mechanism list is a
    deployment's narrowing of what to search for rather than a property of the
    measurement, and a sample may carry no mode at all - so enforcing it would
    refuse a correct adduct for a reason that says nothing about the ledger.

    :param rows: The chunk's rows.
    :param sample: The sample being imported into.
    :raises UnprocessableImportException: On an unknown or mismatched id.
    """
    supplied = {
        field: {value for row in rows if (value := getattr(row, field)) is not None}
        for field, _ in _REFERENCE_COLUMNS
    }
    if not any(supplied.values()):
        return

    async with async_session() as session:
        for field, column in _REFERENCE_COLUMNS:
            if not supplied[field]:
                continue
            found = set(
                (
                    await session.execute(
                        select(column).where(column.in_(supplied[field]))
                    )
                )
                .scalars()
                .all()
            )
            if unknown := sorted(supplied[field] - found):
                raise UnprocessableImportException(
                    f"{field} {_names(unknown)} does not exist on this "
                    "deployment; leave it null when the reference cannot be "
                    "resolved (an adduct's notation still travels in ion_formula)"
                )

        if not supplied["ionization_mechanism_id"]:
            return
        mismatched = sorted(
            (
                await session.execute(
                    select(IonizationMechanism.ionization_mechanism_id).where(
                        IonizationMechanism.ionization_mechanism_id.in_(
                            supplied["ionization_mechanism_id"]
                        ),
                        IonizationMechanism.ionization_mechanism_polarity
                        != sample.polarity,
                    )
                )
            )
            .scalars()
            .all()
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
    if owner_errors := owner_link_errors(rows):
        raise UnprocessableImportException(owner_errors[0])

    for row in rows:
        # A row that states no tier has nothing to be incoherent with: the
        # server derives it below from the same evidence this check would have
        # used, so the invariant holds by construction instead of by refusal.
        #
        # `row.engine_tier` is deliberately NOT checked here, and that is the
        # point of the field rather than an oversight. It carries the tier the
        # producing engine reached its own way - peaky arbitrates on window
        # uniqueness, corroboration and mass degeneracy, any of which can demote
        # a peak below what its evidence alone earns - so checking it against
        # the bands would refuse exactly the disagreement it exists to record.
        # Nothing downstream ranks on it (see the column comment on the model).
        if row.tier is None:
            continue
        if error := tier_coherence_error(
            row.tier,
            row.fit_score,
            row.assigned_formula,
            bands["assigned"],
            bands["candidate"],
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

    # Read back from the run, so the keys are whatever was stored when it was
    # opened: an import that was mid-flight when the upper band was renamed from
    # 'identified' to 'assigned' carries the old key, and its remaining chunks
    # have to keep landing rather than fail on a key this build no longer writes.
    bands = normalize_tier_bands(run.tier_bands) or {}
    if "assigned" not in bands or "candidate" not in bands:
        raise UnprocessableImportException(
            f"run '{run.peak_assignment_run_id}' carries no tier_bands, so its "
            "rows' tiers cannot be checked"
        )
    return bands


def _resolved_tier(row, bands: dict) -> str:
    """This server's tier for an imported row: the one supplied, or derived.

    Deriving it is the preferred path, and the reason is where the rule lives.
    The tier is a pure function of ``fit_score``, ``assigned_formula`` and the
    run's declared bands - all of which this server already holds - and it
    computes that function anyway to check a supplied value. Asking a client for
    the answer therefore asks it to reimplement this deployment's chemical
    plausibility, which is a second copy of one rule; when the copies drift, the
    whole import is refused over a number the client had no reason to hold.
    Omitting the tier removes the class of failure rather than reporting it.

    A row with no evidence to band is the one case ``tier_for_evidence`` cannot
    answer alone: it maps a null to 'below_assignability', while the in-app
    ledger writes 'unassigned' for a peak nothing was proposed for. Those are
    the two tiers ``coherent_tiers`` admits, and the formula is what tells them
    apart - a row that committed one and could not score it was considered and
    found wanting; a row with no formula was never a claim at all. Deriving on
    the same split keeps an imported ledger the same shape as an in-app one.

    The evidence comes from ``row_evidence`` rather than ``evidence_for``, and
    that is what makes the split above reachable: ``evidence_for`` answers with
    the bare fit when the formula is absent, so a row carrying a fit score and
    no formula would never take the null branch at all and would be banded as
    an assignment. Same function as the coherence check uses, so a derived tier
    is by construction one that check would have admitted.

    :param row: The validated payload row.
    :param bands: The run's declared tier bands.
    :return: The tier to store.
    """
    if row.tier is not None:
        return row.tier
    evidence = row_evidence(row.fit_score, row.assigned_formula)
    if evidence is None:
        return TIER_BELOW_ASSIGNABILITY if row.assigned_formula else TIER_UNASSIGNED
    return tier_for_evidence(
        evidence,
        candidate_threshold=bands["candidate"],
        assigned_threshold=bands["assigned"],
    )


def _row_values(row, run_id: str, sample_item_id: str, bands: dict) -> dict:
    """Shape one imported row for the bulk insert.

    The ids are minted here and the owner link is left unresolved: a child can
    reference an owner that has not arrived yet, and the self-referential
    foreign key is validated per row at insert, so the reference is staged on
    ``owner_sample_peak_id`` and resolved once the whole import has landed.

    ``provenance.evidence`` is overwritten with the value the server derived
    while checking this row's tier, rather than being either trusted or dropped.
    It is the number the ledger shows beside the tier chip, and the tier was
    validated against the derived product - so leaving the payload's own figure
    there would put a number on screen that did not produce the tier next to it,
    and dropping it would blank the column on every imported row. Recomputing is
    free: ``formula_plausibility`` is memoized and the coherence check just called
    it for this same formula.

    Three fields are handled by three different rules, and stating them together
    is what keeps a later reader from "fixing" the inconsistency: ``provenance``
    loses the keys the server presents as its own judgement, ``tier`` is the
    server's (supplied and checked, or derived here), and ``engine_tier`` is
    taken verbatim - a value this server neither owns nor derives, only records.

    :param row: The validated payload row.
    :param run_id: The run being assembled.
    :param sample_item_id: The sample being imported into.
    :param bands: The run's declared tier bands, for a row that omitted its tier.
    :return: Column values for one ``PeakAssignment``.
    """
    provenance = strip_server_owned_provenance(row.provenance)
    # `row_evidence`, not `evidence_for`, and for the reason the tier uses it:
    # the number stored here is the one the tier chip displays, so it has to be
    # the number the tier was read off. A row with a fit and no formula has no
    # evidence, and pairing 'unassigned' with a percentage is exactly the
    # contradiction this key exists to prevent.
    evidence = row_evidence(row.fit_score, row.assigned_formula)
    if evidence is not None:
        provenance = {**(provenance or {}), "evidence": evidence}
    elif provenance:
        provenance.pop("evidence", None)
        provenance = provenance or None
    # `plausibility` is the other half of the product above and is treated the
    # same way: derived here and written over whatever the payload carried,
    # never taken from it. The peak inspector renders it as this server's
    # reading of the formula's chemistry, so an importer's own figure under that
    # name would be presented as ours - the hazard the reserved-key rule exists
    # for, in a key that rule does not cover because nothing flattens it onto a
    # column. Deriving it costs nothing: `formula_plausibility` is memoized and
    # `row_evidence` just called it for this same formula. Without it an
    # imported row shows a tier, an evidence and a blank where the number that
    # moved the one to the other should be.
    plausibility = plausibility_for(row.assigned_formula)
    if plausibility is not None:
        provenance = {**(provenance or {}), "plausibility": plausibility}
    elif provenance:
        provenance.pop("plausibility", None)
        provenance = provenance or None
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
        "tier": _resolved_tier(row, bands),
        "engine_tier": row.engine_tier,
        "target_compound_id": row.target_compound_id,
        "target_ion_id": row.target_ion_id,
        "owner_peak_assignment_id": None,
        "owner_sample_peak_id": row.owner_sample_peak_id,
        "alternatives": row.alternatives,
        "provenance": provenance,
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

    # The other half of the owner rule, which only the finished run can answer:
    # an owner may arrive in any chunk, so "an owner is not itself owned" cannot
    # be decided from one chunk's rows. With both halves in force the link is
    # exactly one level deep, which is what rules out chains and cycles.
    owned = PeakAssignment.__table__.alias("owned")
    owner_is_child = (
        (
            await session.execute(
                select(owned.c.sample_peak_id)
                .join(
                    PeakAssignment,
                    PeakAssignment.owner_peak_assignment_id
                    == owned.c.peak_assignment_id,
                )
                .where(
                    PeakAssignment.peak_assignment_run_id == run_id,
                    owned.c.role == OWNED_ROLE,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if owner_is_child:
        raise UnprocessableImportException(owner_of_owner_error(owner_is_child))


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


def _new_run(sample, body, engine: str) -> PeakAssignmentRun:
    """Build - but do not persist - the run an import's first request opens.

    Deliberately not committed here. The run and the first chunk's rows go into
    one transaction in :func:`_import_chunk`, so a refusal the *database* raises
    rolls the run back with them. Committing the run first (as this did) meant
    any insert-time refusal - a reference id that resolves to nothing, a value
    too long for its column - left an ``importing`` run behind holding no rows,
    which blocks every later import *and* in-app assign for the sample until
    someone abandons it or retention's grace expires. Validation refusals were
    already ordered ahead of run creation; this closes the rest.

    The id is minted here rather than by the database because the rows in the
    same transaction reference it.

    :param sample: The sample being imported into.
    :param body: The request body.
    :param engine: The validated engine name.
    :return: The unpersisted run.
    """
    return PeakAssignmentRun(
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
        import_key=body.chunk.import_id,
        peak_assignment_run_utc_created=dt.now(timezone.utc),
    )


#: The constraint that backstops single-owner-per-peak across chunks, which an
#: in-memory pass cannot see. Matched by name so it can be told apart from the
#: other ways the insert can be refused.
_DUPLICATE_PEAK_CONSTRAINT = "uq_peak_assignment_run_id_sample_peak_id"

#: SQLSTATE classes the *payload* can be responsible for: 23 is an integrity
#: constraint violation, 22 a data exception. Discriminated by class rather
#: than by exception type because this runs on asyncpg, whose SQLAlchemy
#: dialect maps only class 23 to a named error: its translation table sends
#: every other Postgres error to the unnamed ``dbapi.Error``, which
#: ``DBAPIError.instance`` cannot resolve to a subclass and so surfaces as a
#: bare ``DBAPIError``. Catching ``DataError`` here would therefore never fire,
#: and catching ``DBAPIError`` alone would answer a dropped connection as
#: though the client could fix it by sending different rows.
_PAYLOAD_SQLSTATE_CLASSES = ("22", "23")


def _insert_refusal(error: DBAPIError) -> UnprocessableImportException | None:
    """Turn a database refusal of the chunk insert into a 422 that fits it.

    Only one constraint violation here has a specific, useful explanation: the
    unique index on (run, peak), which is how a duplicate that spans two chunks
    is caught. Reporting *every* refusal as that one - as this did - tells a
    client to hunt for a duplicate peak when what it actually sent was, say, a
    ``target_ion_id`` no longer in the library, or when its run was abandoned
    underneath it. :func:`_validate_references` now catches the reference cases
    up front, so reaching this branch means either a genuine race or a
    constraint no payload rule models yet; either way the honest answer names
    what a client can check and the detail goes to the log.

    :param error: The refusal raised by the insert.
    :return: The exception to raise, or None when the error is not the
        payload's doing and should propagate untouched.
    """
    orig = getattr(error, "orig", None)
    detail = str(orig or error)
    sqlstate = getattr(orig, "sqlstate", None) or ""
    if sqlstate[:2] not in _PAYLOAD_SQLSTATE_CLASSES:
        # A pool timeout, a dropped connection, a serialization failure: not
        # something a different payload would fix, so it must not be dressed up
        # as a 422. Let it surface as the 500 it is.
        return None
    if _DUPLICATE_PEAK_CONSTRAINT in detail:
        return UnprocessableImportException(
            "a peak in this chunk is already assigned by an earlier chunk of "
            "this import; a run holds at most one row per peak"
        )
    runtime.logger.warning(
        f"Import chunk refused by the database (SQLSTATE {sqlstate}): {detail}"
    )
    return UnprocessableImportException(
        "this chunk was refused by the database and nothing was stored. A run "
        "holds at most one row per peak, every id a row carries into a "
        "reference column must still exist on this deployment, and the run "
        "must not have been abandoned while the chunk was in flight."
    )


def _validate_new_run(sample, body, allow_reserved_engine: str | None = None) -> str:
    """Check everything about a new import that does not depend on its rows.

    :param sample: The sample being imported into.
    :param body: The request body.
    :param allow_reserved_engine: A reserved engine name this caller may stamp;
        see :func:`import_assignment_run`.
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
    engine, engine_error = normalize_engine(
        body.engine, allow_reserved=allow_reserved_engine
    )
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
    allow_reserved_engine: str | None = None,
) -> dict:
    """Accept one request of an externally computed assignment run.

    Creates the run on the first request, appends rows on the next ones, and
    finalizes on the one marked complete. See the module docstring for the
    protocol and why it is shaped this way.

    :param sample_item_id: The sample the run assigns.
    :param body: The import request body.
    :param user_id: The importing user, for the log line.
    :param allow_reserved_engine: A reserved engine name this caller may stamp.
        The trusted entry for a server-side publisher of runs under a reserved
        identity, through this same pipeline; the HTTP route never forwards it,
        so an external client cannot reach it.
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
        return await _import_chunk(sample, body, user_id, allow_reserved_engine)


async def _import_chunk(
    sample, body, user_id: int | None, allow_reserved_engine: str | None = None
) -> dict:
    """Apply one import request to the run it addresses.

    :param sample: The sample being imported into.
    :param body: The request body.
    :param user_id: The importing user, for the log line.
    :param allow_reserved_engine: A reserved engine name this caller may stamp;
        see :func:`import_assignment_run`.
    :return: Status envelope for this request.
    """
    run: PeakAssignmentRun | None = None
    staged = 0
    engine = ""

    if body.chunk.run_id is not None:
        async with async_session() as session:
            run = await _load_import_run(session, body.chunk.run_id, sample)
        # An import addresses one run, so the two ids a follow-up carries have
        # to agree. Only checked when the run has a key at all: addressing an
        # in-app run by id is a different mistake, answered by the status
        # refusal below with the status it is actually in.
        if run.import_key is not None and run.import_key != body.chunk.import_id:
            raise UnprocessableImportException(
                f"chunk.import_id '{body.chunk.import_id}' is not the import "
                f"that created run '{run.peak_assignment_run_id}'; every chunk "
                "of one import carries the id its create did"
            )
    else:
        # A create the client may have sent before. Resolving it here turns the
        # retry into a replay of that run's first chunk, which the offset rules
        # below already answer correctly. This is the whole reason import_id is
        # required: a create carries no run id, so nothing else can tell a retry
        # of it from a second import - and the SDK retries POSTs on timeouts
        # with no way to opt out.
        async with async_session() as session:
            run = await _run_for_import_key(
                session, sample.sample_item_id, body.chunk.import_id
            )

    creating = run is None
    if creating:
        engine = _validate_new_run(sample, body, allow_reserved_engine)
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
    if body.rows:
        known_peak_ids = await _known_peak_ids(
            sample, run.peak_assignment_run_id if run is not None else None
        )
        total = staged + len(body.rows)
        if total > len(known_peak_ids):
            raise UnprocessableImportException(
                f"this import would hold {total} rows for a sample with "
                f"{len(known_peak_ids)} peak(s); a run holds at most one row "
                "per peak"
            )
        _validate_rows(body.rows, bands, known_peak_ids)
        await _validate_references(body.rows, sample)
    elif body.chunk.complete and staged == 0:
        # Caught before a run is opened: an import that carries nothing is a
        # 422, not a completed empty run.
        raise UnprocessableImportException(
            "an import must carry at least one row; an empty run is not a ledger"
        )

    new_run = _new_run(sample, body, engine) if creating else None
    run_id = (new_run or run).peak_assignment_run_id
    if new_run is not None and body.rows:
        # The create read the file before the run had an id; hand the set to
        # the run now so the chunks that follow do not read it again.
        _cache_peak_ids(run_id, known_peak_ids)

    # One transaction for the run and this chunk's rows. On a create that is
    # what keeps a refusal the database raises from stranding an 'importing'
    # run with no rows in it, blocking the sample; on a follow-up it is just
    # the insert.
    values = [
        _row_values(row, run_id, sample.sample_item_id, bands) for row in body.rows
    ]
    if new_run is not None or values:
        try:
            async with async_session() as session:
                if new_run is not None:
                    session.add(new_run)
                    await session.flush()
                if values:
                    await session.execute(insert(PeakAssignment), values)
                await session.commit()
        except DBAPIError as error:
            if (refusal := _insert_refusal(error)) is None:
                raise
            raise refusal from error
        staged += len(values)

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

    _release_peak_ids(run_id)
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

    Taken under the same advisory claim every other write on this sample holds.
    Without it a delete can land between a chunk's run lookup and its insert,
    cascading the staged rows away underneath a request that is still running:
    the insert then fails on the run's foreign key, and the client is told its
    chunk was refused by the database rather than that its run is gone. The
    claim makes the two mutually exclusive for the price of one lock.

    :param sample_item_id: The sample the run belongs to.
    :param peak_assignment_run_id: The run to abandon.
    :return: Status envelope naming the run and the rows reclaimed.
    :raises DuplicateException: When a chunk of this sample's import is in
        flight, or the run is not an unfinished import.
    """
    sample = await fetch_sample(sample_item_id)
    async with assignment_claim("sample", sample_item_id) as acquired:
        if not acquired:
            raise DuplicateException(
                f"A peak assignment request is in flight for sample "
                f"'{sample.sample_item_name}'. Retry when it has finished."
            )
        return await _abandon_run(sample, peak_assignment_run_id)


async def _abandon_run(sample, peak_assignment_run_id: str) -> dict:
    """Delete the addressed import and report what it reclaimed.

    :param sample: The sample the run belongs to.
    :param peak_assignment_run_id: The run to abandon.
    :return: Status envelope naming the run and the rows reclaimed.
    """
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

    _release_peak_ids(peak_assignment_run_id)
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
