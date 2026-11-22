"""A person's verdicts on assigned peaks, and the queue that asks for them.

Writing a verdict on a row (refused on a decoy run's), reading a row's, a
batch's or a dataset's verdicts, and ranking the rows a person should look at
next (``verification_queue.rank_queue``).
"""

from datetime import datetime as dt
from datetime import timezone

from fastapi import status
from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    CodedHTTPException,
    NotFoundException,
)
from mascope_backend.api.new.peak_assignments.batch_peak_verification import (
    claim_of,
    is_stale,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    mz_from_delta,
    resolve_candidate,
)
from mascope_backend.api.new.peak_assignments.claims import claim_level, mapping_context
from mascope_backend.api.new.peak_assignments.config import IN_APP_ENGINE
from mascope_backend.api.new.peak_assignments.fold_view import (
    fold_id_target,
    fold_member,
    is_fold_id,
    verification_target,
)
from mascope_backend.api.new.peak_assignments.ledger_reads import _ledger_run
from mascope_backend.api.new.peak_assignments.recalibration import (
    _instrument_class,
    _label_profile,
    _live_label_rows,
    _run_profile,
)
from mascope_backend.api.new.peak_assignments.tiers import normalize_tier_bands
from mascope_backend.api.new.peak_assignments.verification_queue import (
    REASON_BAND_EDGE,
    REASON_SPOT_CHECK,
    REASON_STALE,
    rank_queue,
)
from mascope_backend.db import (
    AssignmentVerification,
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakVerification,
    PeakAssignment,
    PeakAssignmentRun,
    Sample,
    SampleBatch,
    async_session,
)
from mascope_backend.db.id import gen_id


#: Code carried in the error payload when a verdict is sent for a decoy run's
#: row. A client that verifies rows it read by run id has to tell this apart
#: from a row that is gone.
DECOY_RUN_VERDICT_CODE = "decoy_run_verdict"

#: Namespace discriminator for the per-identity verdict-write advisory lock, hashed into the
#: first int of the two-int lock key space so it cannot collide with the other advisory-lock
#: users (the match-write locks, the assignment admission claim).
_VERIFICATION_WRITE_LOCK_NAMESPACE = "mascope_assignment_verification_write"


class DecoyRunVerdictException(CodedHTTPException):
    """A verdict was sent for a row of a decoy run (422).

    A decoy run assigns the sample with its chemistry made wrong on purpose,
    so a verdict on one of its rows judges a reading nobody claims. Taken,
    it would also supersede the live verdict on the same identity - which is
    the peak, the formula and the adduct, whatever run they came from - and
    feed the calibration from verdicts a label about a deliberate error.

    :param peak_assignment_run_id: The decoy run the row belongs to.
    """

    error_code = DECOY_RUN_VERDICT_CODE

    def __init__(self, peak_assignment_run_id: str):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Assignment run '{peak_assignment_run_id}' is a decoy run, whose "
                "rows read the sample with its chemistry made wrong on purpose; "
                "they take no verdict. Verify the row in the sample's own run."
            ),
        )


# -------------------------------------------------------------------
# Verification capture (verification-calibration loop, V1)
# -------------------------------------------------------------------


@api_controller()
async def create_verification(
    sample_item_id: str,
    peak_assignment_id: str,
    verdict: str,
    evidence_level: str | None = None,
    note: str | None = None,
    user_id: int | None = None,
    claimed_inchikey: str | None = None,
    claimed_name: str | None = None,
) -> dict:
    """Record a user's verdict on an assignment, snapshotting its score.

    Looks up the judged assignment (must belong to the sample), captures its stable identity
    (sample_peak_id + formula + adduct) and the score at this moment (fit_score / evidence /
    p_correct), and inserts an :class:`AssignmentVerification`. The snapshot is what makes the
    later calibration pair `(score, label)` stable even after a re-run changes the assignment.

    Storage stays append-only, but the verdict this replaces (if any) is stamped
    ``superseded_utc`` in the same transaction, so exactly one row per identity is live and no
    consumer has to re-derive which one that is. The superseded row is kept: it holds the score
    the user judged *then*, which is a valid calibration pair for that score even once a later
    verdict supersedes it as the current answer.

    :param sample_item_id: Sample the assignment belongs to.
    :param peak_assignment_id: The assignment being verified.
    :param verdict: confirmed | rejected | unsure.
    :param evidence_level: Basis for the verdict (see schemas.EvidenceLevel); the schema
        requires it for 'confirmed'.
    :param note: Optional free-text note.
    :param user_id: The verifying user (attribution).
    :param claimed_inchikey: The structure a confirmation names, if any: an
        identification claim (``claims``). The schema applies its rules.
    :param claimed_name: The name given with it.
    :return: Status envelope with the created verification record, with the
        level the verdict states fixed on it.
    """
    level = claim_level(verdict, evidence_level, claimed_inchikey)
    sample = await fetch_sample(sample_item_id)
    async with async_session() as session:
        if is_fold_id(peak_assignment_id):
            # A derived row: the member carries the identity and the score the
            # snapshot needs, and the row-level links stay NULL - as they do
            # for a verdict whose run was pruned afterwards.
            found = await fold_member(
                session, sample_item_id, fold_id_target(peak_assignment_id)
            )
            assignment = verification_target(*found) if found else None
        else:
            assignment = await session.get(PeakAssignment, peak_assignment_id)
        if assignment is None or assignment.sample_item_id != sample_item_id:
            raise NotFoundException(
                f"Assignment '{peak_assignment_id}' not found for sample "
                f"'{sample.sample_item_name}'"
            )
        if assignment.peak_assignment_run_id is not None:
            decoy = await session.scalar(
                select(PeakAssignmentRun.decoy).where(
                    PeakAssignmentRun.peak_assignment_run_id
                    == assignment.peak_assignment_run_id
                )
            )
            if decoy is not None:
                raise DecoyRunVerdictException(assignment.peak_assignment_run_id)
        # Serialize verdict writes on this identity. The supersede-then-insert below has to be
        # atomic against a concurrent verdict on the same assignment - a double-fired submit, or
        # two members of a workspace judging the same peak at once - which would otherwise both
        # stamp the same predecessor and then race to insert two live rows, one of which the
        # partial unique index rejects with an integrity error the user did nothing to deserve.
        # Transaction-scoped, so it releases on commit or rollback.
        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtext(_VERIFICATION_WRITE_LOCK_NAMESPACE),
                    func.hashtext(
                        "|".join(
                            (
                                sample_item_id,
                                assignment.sample_peak_id,
                                assignment.assigned_formula or "",
                                assignment.ionization_mechanism_id or "",
                            )
                        )
                    ),
                )
            )
        )
        now = dt.now(timezone.utc)
        # IS NOT DISTINCT FROM, not ==: both identity halves are nullable, and a plain equality
        # never matches a NULL, which would leave the old verdict live beside the new one.
        await session.execute(
            update(AssignmentVerification)
            .where(
                AssignmentVerification.sample_item_id == sample_item_id,
                AssignmentVerification.sample_peak_id == assignment.sample_peak_id,
                AssignmentVerification.assigned_formula.is_not_distinct_from(
                    assignment.assigned_formula
                ),
                AssignmentVerification.ionization_mechanism_id.is_not_distinct_from(
                    assignment.ionization_mechanism_id
                ),
                AssignmentVerification.superseded_utc.is_(None),
            )
            .values(superseded_utc=now)
        )
        provenance = assignment.provenance or {}
        verification = AssignmentVerification(
            assignment_verification_id=gen_id(32),
            sample_item_id=sample_item_id,
            peak_assignment_id=assignment.peak_assignment_id,
            peak_assignment_run_id=assignment.peak_assignment_run_id,
            sample_peak_id=assignment.sample_peak_id,
            assigned_formula=assignment.assigned_formula,
            ionization_mechanism_id=assignment.ionization_mechanism_id,
            verdict=verdict,
            evidence_level=evidence_level,
            fit_score=assignment.fit_score,
            evidence=provenance.get("evidence"),
            p_correct=provenance.get("p_correct"),
            note=note,
            context=mapping_context(),
            claimed_inchikey=claimed_inchikey,
            claimed_name=claimed_name,
            level=level,
            verified_by=user_id,
            verified_utc=now,
        )
        session.add(verification)
        await session.commit()
        await session.refresh(verification)
        record = verification.to_dict()
    return {
        "status": "success",
        "message": (
            f"Recorded '{verdict}' verification for "
            f"{record.get('assigned_formula') or 'peak'} in sample "
            f"'{sample.sample_item_name}'"
        ),
        "results": 1,
        "data": [record],
    }


@api_controller()
async def get_verifications(sample_item_id: str) -> dict:
    """All verification verdicts recorded for a sample, newest first.

    Returns every verdict, superseded ones included, so the history stays inspectable. The
    current verdict for a given assignment is the one row of its identity
    (``sample_peak_id`` + formula + adduct) with ``superseded_utc`` null - a partial unique
    index guarantees there is exactly one. The frontend derives per-assignment state from this
    list.
    """
    sample = await fetch_sample(sample_item_id)
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(AssignmentVerification)
                    .where(AssignmentVerification.sample_item_id == sample_item_id)
                    .order_by(AssignmentVerification.verified_utc.desc())
                )
            )
            .scalars()
            .all()
        )
    data = [row.to_dict() for row in rows]
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} verification"
            f"{'s' if len(data) != 1 else ''} for sample "
            f"'{sample.sample_item_name}'"
        ),
        "results": len(data),
        "data": data,
    }


async def get_scope_verifications(
    *,
    sample_batch_id: str | None = None,
    dataset_id: str | None = None,
    verdict: str | None = None,
    evidence_level: str | None = None,
    since: dt | None = None,
    include_superseded: bool = False,
) -> list[dict]:
    """The verdicts over every sample of a batch or a dataset, one row per
    judged peak, as each sample's assignment ledger shows them.

    A sample's own verdicts come first in precedence. A batch-level verdict
    reaches a sample's peak when the peak folded into the judged batch peak and
    the peak's own claim (formula, mechanism) is the one judged, so a dissenting
    sample stays out from under a verdict about another formula; it is listed
    ``borrowed``, and only where the sample has no current verdict of its own on
    that claim. The precedence is settled on the whole record before the filters
    apply, so a filter never lets a borrowed verdict through in place of the
    sample's own one.

    Each row carries the sample's filename and acquisition time and the peak's
    m/z. Superseded per-sample verdicts are listed with ``include_superseded``;
    batch-level ones are live only, since a replaced one reaches no sample.

    :param sample_batch_id: The batch whose samples to read (or ``dataset_id``).
    :param dataset_id: The dataset whose samples to read.
    :param verdict: Keep only this verdict.
    :param evidence_level: Keep only this evidence level.
    :param since: Keep only verdicts recorded at or after this moment; naive is UTC.
    :param include_superseded: Also list the per-sample verdicts a later one replaced.
    :return: The rows, by sample acquisition time, then m/z, newest verdict first.
    """
    if (sample_batch_id is None) == (dataset_id is None):
        raise ValueError("give exactly one of sample_batch_id and dataset_id")
    samples = select(Sample.sample_item_id)
    if sample_batch_id is not None:
        samples = samples.where(Sample.sample_batch_id == sample_batch_id)
    else:
        samples = samples.join(
            SampleBatch, SampleBatch.sample_batch_id == Sample.sample_batch_id
        ).where(SampleBatch.dataset_id == dataset_id)
    samples = samples.scalar_subquery()

    # The peak's m/z: off the judged ledger row, else off any row of the same
    # sample and peak - a re-run sets the judged one's id to NULL.
    other_row = aliased(PeakAssignment)
    any_row_mz = (
        select(func.max(other_row.sample_peak_mz))
        .where(
            other_row.sample_item_id == AssignmentVerification.sample_item_id,
            other_row.sample_peak_id == AssignmentVerification.sample_peak_id,
        )
        .correlate(AssignmentVerification)
        .scalar_subquery()
    )
    async with async_session() as session:
        sample_rows = {
            row.sample_item_id: row
            for row in (
                await session.execute(
                    select(
                        Sample.sample_item_id,
                        Sample.sample_item_name,
                        Sample.sample_batch_id,
                        Sample.filename,
                        Sample.datetime,
                        Sample.datetime_utc,
                    ).where(Sample.sample_item_id.in_(samples))
                )
            ).all()
        }
        own = (
            await session.execute(
                select(
                    AssignmentVerification,
                    func.coalesce(PeakAssignment.sample_peak_mz, any_row_mz),
                )
                .outerjoin(
                    PeakAssignment,
                    PeakAssignment.peak_assignment_id
                    == AssignmentVerification.peak_assignment_id,
                )
                .where(AssignmentVerification.sample_item_id.in_(samples))
            )
        ).all()
        reaching = (
            await session.execute(
                select(
                    BatchPeakOccurrence.sample_item_id,
                    BatchPeakOccurrence.sample_peak_id,
                    BatchPeakOccurrence.candidate,
                    BatchPeakOccurrence.mz_delta_ppm,
                    PeakAssignment.assigned_formula,
                    PeakAssignment.ionization_mechanism_id,
                    BatchPeakVerification,
                    BatchPeak,
                )
                .join(
                    BatchPeakVerification,
                    BatchPeakVerification.batch_peak_id
                    == BatchPeakOccurrence.batch_peak_id,
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .outerjoin(
                    PeakAssignment,
                    PeakAssignment.peak_assignment_id
                    == BatchPeakOccurrence.peak_assignment_id,
                )
                .where(
                    BatchPeakOccurrence.sample_item_id.in_(samples),
                    BatchPeakVerification.superseded_utc.is_(None),
                )
            )
        ).all()

    # The identities a sample's own current verdict holds, which a batch-level
    # verdict does not cover.
    rows: list[dict] = []
    judged = set()
    for record, mz in own:
        if record.superseded_utc is None:
            judged.add(
                (
                    record.sample_item_id,
                    record.sample_peak_id,
                    record.assigned_formula,
                    record.ionization_mechanism_id,
                )
            )
        rows.append(
            {
                **record.to_dict(),
                "sample_peak_mz": mz,
                "borrowed": False,
            }
        )
    for (
        sample_item_id,
        sample_peak_id,
        candidate,
        mz_delta_ppm,
        row_formula,
        row_mechanism,
        record,
        anchor,
    ) in reaching:
        # The member's own claim: its entry in the anchor's registry (none for
        # an unassigned member), or the ledger row it was folded from on an
        # anchor that predates the registry.
        if anchor.candidates is not None:
            entry = resolve_candidate(anchor.candidates, candidate)
            claim = (entry.get("formula"), entry.get("ionization_mechanism_id"))
        else:
            claim = (row_formula, row_mechanism)
        if claim != (record.assigned_formula, record.ionization_mechanism_id):
            continue
        if (sample_item_id, sample_peak_id, *claim) in judged:
            continue
        rows.append(
            {
                "sample_item_id": sample_item_id,
                "sample_peak_id": sample_peak_id,
                "sample_peak_mz": mz_from_delta(anchor.mz, mz_delta_ppm),
                "assigned_formula": record.assigned_formula,
                "ionization_mechanism_id": record.ionization_mechanism_id,
                "verdict": record.verdict,
                "evidence_level": record.evidence_level,
                "claimed_inchikey": record.claimed_inchikey,
                "claimed_name": record.claimed_name,
                "level": record.level,
                "note": record.note,
                "context": record.context,
                "verified_by": record.verified_by,
                "verified_utc": record.verified_utc,
                "superseded_utc": None,
                "borrowed": True,
                "batch_peak_verification_id": record.batch_peak_verification_id,
                "batch_peak_id": record.batch_peak_id,
                "stale": is_stale(record.to_dict(), claim_of(anchor)),
            }
        )

    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    kept = []
    for row in rows:
        if row["superseded_utc"] is not None and not include_superseded:
            continue
        if verdict is not None and row["verdict"] != verdict:
            continue
        if evidence_level is not None and row["evidence_level"] != evidence_level:
            continue
        if since is not None and (
            row["verified_utc"] is None or row["verified_utc"] < since
        ):
            continue
        sample = sample_rows.get(row["sample_item_id"])
        kept.append(
            {
                **row,
                "sample_item_name": sample.sample_item_name if sample else None,
                "sample_batch_id": sample.sample_batch_id if sample else None,
                "filename": sample.filename if sample else None,
                "datetime": sample.datetime if sample else None,
                "datetime_utc": sample.datetime_utc if sample else None,
            }
        )

    def order(row: dict) -> tuple:
        when = row["datetime_utc"]
        verified = row["verified_utc"]
        return (
            when is None,
            when.timestamp() if when else 0.0,
            row["sample_item_id"],
            row["sample_peak_mz"] is None,
            row["sample_peak_mz"] or 0.0,
            row["sample_peak_id"],
            -(verified.timestamp() if verified else 0.0),
        )

    kept.sort(key=order)
    return kept


@api_controller()
async def get_batch_verifications(sample_batch_id: str, **filters) -> dict:
    """The verdicts over every sample of a batch; see :func:`get_scope_verifications`.

    :param sample_batch_id: The batch.
    :param filters: ``verdict``, ``evidence_level``, ``since``, ``include_superseded``.
    :return: Status envelope with the rows.
    """
    data = await get_scope_verifications(sample_batch_id=sample_batch_id, **filters)
    return _scope_envelope(data, "batch")


@api_controller()
async def get_dataset_verifications(dataset_id: str, **filters) -> dict:
    """The verdicts over every sample of a dataset; see :func:`get_scope_verifications`.

    :param dataset_id: The dataset.
    :param filters: ``verdict``, ``evidence_level``, ``since``, ``include_superseded``.
    :return: Status envelope with the rows.
    """
    data = await get_scope_verifications(dataset_id=dataset_id, **filters)
    return _scope_envelope(data, "dataset")


def _scope_envelope(data: list[dict], scope: str) -> dict:
    borrowed = sum(1 for row in data if row["borrowed"])
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} verdict{'s' if len(data) != 1 else ''} over "
            f"the {scope}'s samples, {borrowed} of them batch-level"
        ),
        "results": len(data),
        "data": data,
    }


@api_controller()
async def get_verification_queue(
    sample_item_id: str, peak_assignment_run_id: str | None = None
) -> dict:
    """The assignments of a run worth a verdict next, in the order to ask.

    Guardrail 3 of the verification loop (``verification_queue``): the rows
    whose evidence sits near a tier band edge, where the confidence curve is
    least constrained, ranked by how close they sit and by how few labels the
    sample's instrument class and the run's reagent profile already hold near
    that evidence, with seeded random spot checks between them; and first, the
    rows a live verdict judged before this run and the run since read
    differently, marked stale. No entry carries ``p_correct`` (guardrail 5).

    The run is the one the ledger read serves (:func:`_ledger_run`). A decoy
    run's rows take no verdict, a verdict on an imported run's row never
    reaches the refit (:func:`_live_label_rows`), and a batch-derived ledger is
    no run, so all three answer an empty queue.

    :param sample_item_id: The sample.
    :param peak_assignment_run_id: The run; defaults to the latest completed one.
    :return: Status envelope: ``total`` entries in queue order under ``data``,
        ``counts`` by reason, and the ``peak_assignment_run_id`` ranked.
    """
    sample = await fetch_sample(sample_item_id)

    def envelope(entries: list, run_id: str | None, message: str) -> dict:
        counts = {
            reason: sum(1 for e in entries if e.reason == reason)
            for reason in (REASON_STALE, REASON_BAND_EDGE, REASON_SPOT_CHECK)
        }
        return {
            "status": "success",
            "message": message,
            "results": len(entries),
            "total": len(entries),
            "peak_assignment_run_id": run_id,
            "counts": counts,
            "data": [entry.to_dict() for entry in entries],
        }

    if is_fold_id(peak_assignment_run_id):
        return envelope(
            [], None, "A ledger derived from the batch is no run: nothing to rank"
        )
    async with async_session() as session:
        run = await _ledger_run(session, sample, peak_assignment_run_id)
        if run is None:
            return envelope(
                [],
                None,
                f"No completed peak assignment runs exist for sample "
                f"'{sample.sample_item_name}'",
            )
        if run.decoy is not None:
            return envelope(
                [], run.peak_assignment_run_id, "A decoy run's rows take no verdict"
            )
        if run.engine != IN_APP_ENGINE:
            return envelope(
                [],
                run.peak_assignment_run_id,
                "An imported run's verdicts do not reach the confidence curve",
            )
        run_id = run.peak_assignment_run_id
        rows = (
            await session.execute(
                select(
                    PeakAssignment.peak_assignment_id,
                    PeakAssignment.sample_peak_id,
                    PeakAssignment.assigned_formula,
                    PeakAssignment.ionization_mechanism_id,
                    PeakAssignment.role,
                    PeakAssignment.provenance["evidence"].as_float().label("evidence"),
                ).where(PeakAssignment.peak_assignment_run_id == run_id)
            )
        ).all()
        verdicts = (
            await session.execute(
                select(
                    AssignmentVerification.assignment_verification_id,
                    AssignmentVerification.sample_peak_id,
                    AssignmentVerification.assigned_formula,
                    AssignmentVerification.ionization_mechanism_id,
                    AssignmentVerification.verdict,
                    AssignmentVerification.evidence,
                    AssignmentVerification.verified_utc,
                ).where(
                    AssignmentVerification.sample_item_id == sample_item_id,
                    AssignmentVerification.superseded_utc.is_(None),
                )
            )
        ).all()
        pool = await _live_label_rows(session)
        bands = normalize_tier_bands(run.tier_bands)
        run_created = run.peak_assignment_run_utc_created
        profile = _run_profile(run.config)

    # The labels near a row are counted per instrument class and reagent
    # profile, the run's: the labels a per-profile refit of the run's curve
    # reads (recalibrate_instrument).
    inst = _instrument_class(sample.filename)
    label_evidence = [
        label.evidence
        for label in pool
        if _instrument_class(label.filename) == inst
        and _label_profile(label) == profile
    ]
    entries = rank_queue(
        (row._asdict() for row in rows),
        bands=bands,
        label_evidence=label_evidence,
        verdicts=[verdict._asdict() for verdict in verdicts],
        run_created=run_created,
        seed=run_id,
    )
    return envelope(
        entries,
        run_id,
        f"{len(entries)} assignment{'s' if len(entries) != 1 else ''} worth "
        f"verifying in sample '{sample.sample_item_name}'",
    )
