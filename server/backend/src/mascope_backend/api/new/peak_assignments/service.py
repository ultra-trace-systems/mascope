"""
Peak-centric assignment service.

Orchestrates the two-stage assignment engine over a sample and provides the
read model ("every peak in sample X with its formula and confidence"):

- Stage A (database-first): every peak is matched against the known target
  isotopologue library by reusing the targeted matcher, then the result is
  inverted from target-anchored to peak-anchored rows (source='database').
- Stage B (untargeted): peaks Stage A left unexplained are run through the
  mascope_tools composition finder (source='untargeted').
- Remaining peaks are persisted as 'unassigned' so each run is a complete,
  queryable ledger with a single owner per peak.
"""

import asyncio
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime as dt
from datetime import timezone
from types import SimpleNamespace

import pandas as pd
from fastapi import status
from sqlalchemy import func, insert, or_, select, update

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    CodedHTTPException,
    NotFoundException,
)
from mascope_backend.api.new.cheminfo.utils import (
    to_custom_element_format,
    to_explicit_isotope_format,
)
from mascope_backend.api.new.ionization.modes.util import (
    fetch_sample_ionization_mechanism_ids,
)
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.match.params.lib import (
    apply_match_params,
    isotope_abundance_threshold_expr,
)
from mascope_backend.api.new.peak_assignments.admission import (
    assignment_claim,
    in_flight_run_id,
)
from mascope_backend.api.new.peak_assignments.calibration_store import (
    load_calibration,
    save_calibration,
)
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    PEAK_ASSIGNMENT_ENGINE_VERSION,
    PeakAssignmentConfig,
    peak_assignment_enabled,
)
from mascope_backend.api.new.peak_assignments.engine import (
    REFERENCE_IDENTITIES_COL,
    build_unassigned_assignments,
    invert_matches_to_peak_assignments,
    score_ions_by_fit,
    untargeted_matches_to_peak_assignments,
)
from mascope_backend.api.new.peak_assignments.schemas import DEFAULT_PAGE_LIMIT
from mascope_backend.db import (
    AssignmentVerification,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    Sample,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_file.name import get_instrument_type
from mascope_match import compute_match_isotopes
from mascope_reference import iter_known_compositions, known_state_fingerprint
from mascope_tools.composition import CompositionSearchConfig, HeuristicFilterConfig
from mascope_tools.composition.calibration import (
    InsufficientCalibrationData,
    recalibrate,
)
from mascope_tools.composition.finder import assign_compositions
from mascope_tools.composition.heuristic_filter import SCORE_VERSION


# -------------------------------------------------------------------
# Read model
# -------------------------------------------------------------------


@api_controller()
async def get_peak_assignment_runs(sample_item_id: str) -> dict:
    """
    Retrieve all peak assignment runs for a sample, newest first.

    :param sample_item_id: Unique identifier of the sample item
    :return: Dictionary with status, message, results count, and run records
    """
    sample = await fetch_sample(sample_item_id)

    async with async_session() as session:
        runs = (
            (
                await session.execute(
                    select(PeakAssignmentRun)
                    .where(PeakAssignmentRun.sample_item_id == sample_item_id)
                    .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
                )
            )
            .scalars()
            .all()
        )

    data = [run.to_dict() for run in runs]
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} peak assignment run"
            f"{'s' if len(data) != 1 else ''} "
            f"for sample '{sample.sample_item_name}'"
        ),
        "results": len(data),
        "data": data,
    }


# Ledger list projection: every scalar column of a row, but not `alternatives`
# - inspector-only JSON that never has to leave the database for a list read.
# `provenance` (also inspector detail) is still selected, only to be collapsed
# into the few scalars the ledger columns render; see _provenance_scalars.
_LEDGER_COLUMNS = tuple(
    column
    for column in PeakAssignment.__table__.columns
    if column.key != "alternatives"
)


#: The status a run holds while an external client is still uploading its rows.
#: Named here rather than imported from the import module so the read path does
#: not depend on the write path.
IMPORTING_STATUS = "importing"

#: Code carried in the error payload when a read addresses a run that is still
#: assembling. Part of the API contract: a client polling an import it launched
#: has to tell "not finished yet, ask again" apart from the other things a 409
#: on this surface can mean, and the run listing is what tells it when to stop.
RUN_STILL_ASSEMBLING_CODE = "run_still_assembling"


class RunStillAssemblingException(CodedHTTPException):
    """A read named a run whose ledger is still arriving (409).

    Only the *default* read resolves to the latest completed run; a read with an
    explicit ``peak_assignment_run_id`` serves that run whatever its status, and
    ``GET /sample/{id}/runs`` lists runs of every status - so an assembling
    import is reachable by id, and the import's own first response hands the
    client that id. Serving it would present a partial ledger as a ledger: the
    rows are real but the set is not, and nothing in the payload says so.

    Distinguished from the 'running' case on purpose. An in-app run writes its
    whole ledger in one insert at the end, so reading it mid-flight yields an
    empty result that is honest about itself; an import accumulates, so reading
    it mid-flight yields a subset that is not.

    :param peak_assignment_run_id: The run that was addressed.
    :param sample_item_name: The sample it belongs to, for the message.
    """

    error_code = RUN_STILL_ASSEMBLING_CODE

    def __init__(self, peak_assignment_run_id: str, sample_item_name: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Peak assignment run '{peak_assignment_run_id}' for sample "
                f"'{sample_item_name}' is still being imported, so its ledger "
                "is incomplete. Wait for the import to finish - the runs "
                "endpoint reports its status - or read the sample's latest "
                "completed run by omitting the run id."
            ),
        )


def _provenance_scalars(provenance: dict | None) -> dict:
    """Collapse a provenance blob into the scalars the ledger renders.

    The ledger table shows a calibrated P(correct) column (with its provisional
    marker) and an adduct-corroboration count on every row; everything else in
    provenance is per-peak inspector detail served by
    :func:`get_peak_assignment_detail`.
    """
    provenance = provenance or {}
    calibration = provenance.get("calibration") or {}
    corroboration = provenance.get("corroboration") or {}
    return {
        "p_correct": provenance.get("p_correct"),
        "p_correct_provisional": calibration.get("provisional"),
        "corroboration_adducts": corroboration.get("n_adducts"),
    }


@api_controller()
async def get_peak_assignments(
    sample_item_id: str,
    peak_assignment_run_id: str | None = None,
    tier: str | None = None,
    role: str | None = None,
    source: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> dict:
    """
    Retrieve peaks-with-assignments for a sample.

    Returns the assignments of the requested run, or of the latest completed
    run when no run id is given. Optional filters narrow by confidence tier,
    peak role, or assignment source.

    A requested run that is still being imported is refused with 409 rather than
    served: its rows are real but its set is not, and the payload has no way to
    say so. See :class:`RunStillAssemblingException`.

    Paged, and slim: the ledger is deliberately complete - one row per detected
    peak - so a dense sample runs to tens of thousands of rows, and the
    `alternatives`/`provenance` JSON would be ~74% of the bytes while only the
    peak inspector reads it. Rows here carry the scalar fields (plus a few
    provenance-derived scalars); the full JSON detail of one assignment is
    served by :func:`get_peak_assignment_detail`. ``total`` reports the match
    count across all pages so a client knows when it has the whole run.

    :param sample_item_id: Unique identifier of the sample item
    :param peak_assignment_run_id: Specific run to read; defaults to the
        latest completed run
    :param tier: Optional filter by confidence tier
    :param role: Optional filter by peak role
    :param source: Optional filter by assignment source (database/untargeted)
    :param limit: Maximum rows to return in this page
    :param offset: Rows to skip, for paging
    :return: Dictionary with status, message, total, and slim assignment rows.
        Run identity is on each row (peak_assignment_run_id); run metadata is
        served by the runs endpoint.
    """
    sample = await fetch_sample(sample_item_id)

    async with async_session() as session:
        if peak_assignment_run_id is not None:
            run = await session.get(PeakAssignmentRun, peak_assignment_run_id)
            if run is None or run.sample_item_id != sample_item_id:
                raise NotFoundException(
                    f"Peak assignment run '{peak_assignment_run_id}' not found "
                    f"for sample '{sample.sample_item_name}'"
                )
            if run.status == IMPORTING_STATUS:
                raise RunStillAssemblingException(
                    peak_assignment_run_id, sample.sample_item_name
                )
        else:
            run = (
                await session.execute(
                    select(PeakAssignmentRun)
                    .where(
                        PeakAssignmentRun.sample_item_id == sample_item_id,
                        PeakAssignmentRun.status == "completed",
                    )
                    .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

        if run is None:
            return {
                "status": "success",
                "message": (
                    f"No completed peak assignment runs exist for sample "
                    f"'{sample.sample_item_name}'"
                ),
                "results": 0,
                "total": 0,
                "data": [],
            }

        filters = [PeakAssignment.peak_assignment_run_id == run.peak_assignment_run_id]
        if tier:
            filters.append(PeakAssignment.tier == tier)
        if role:
            filters.append(PeakAssignment.role == role)
        if source:
            filters.append(PeakAssignment.source == source)

        total = (
            await session.execute(
                select(func.count()).select_from(PeakAssignment).where(*filters)
            )
        ).scalar_one()

        # Ordered by m/z with the primary key as a tiebreak: two peaks can share
        # an m/z, and paging over an ordering that does not break such ties can
        # return one of them on both pages and the other on neither.
        query = (
            select(*_LEDGER_COLUMNS)
            .where(*filters)
            .order_by(PeakAssignment.sample_peak_mz, PeakAssignment.peak_assignment_id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await session.execute(query)).all()

    data = []
    for row in rows:
        record = row._asdict()
        record.update(_provenance_scalars(record.pop("provenance", None)))
        data.append(record)
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} of {total} peak assignment"
            f"{'s' if total != 1 else ''} "
            f"for sample '{sample.sample_item_name}'"
        ),
        "results": len(data),
        "total": total,
        "data": data,
    }


@api_controller()
async def get_peak_assignment_detail(
    sample_item_id: str,
    peak_assignment_id: str,
) -> dict:
    """
    Retrieve one assignment in full, including the inspector-only JSON detail.

    The complement of :func:`get_peak_assignments`: the list serves a slim
    projection of the whole run, and this serves the `alternatives` and
    `provenance` of a single assignment when a user selects its peak.

    :param sample_item_id: Unique identifier of the sample item
    :param peak_assignment_id: Unique identifier of the assignment
    :return: Dictionary with status, message, and the one full assignment row
    """
    sample = await fetch_sample(sample_item_id)

    async with async_session() as session:
        assignment = await session.get(PeakAssignment, peak_assignment_id)
        if assignment is None or assignment.sample_item_id != sample_item_id:
            raise NotFoundException(
                f"Assignment '{peak_assignment_id}' not found for sample "
                f"'{sample.sample_item_name}'"
            )
        record = assignment.to_dict()

    # The detail row is a superset of the list row, so the flattened scalars
    # are carried here too and a client can treat it as a drop-in replacement.
    record.update(_provenance_scalars(record.get("provenance")))
    return {
        "status": "success",
        "message": (
            f"Retrieved assignment '{peak_assignment_id}' for sample "
            f"'{sample.sample_item_name}'"
        ),
        "results": 1,
        "data": [record],
    }


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
) -> dict:
    """Record a user's verdict on an assignment (append-only), snapshotting its score.

    Looks up the judged assignment (must belong to the sample), captures its stable identity
    (sample_peak_id + formula + adduct) and the score at this moment (fit_score / evidence /
    p_correct), and inserts an :class:`AssignmentVerification`. The snapshot is what makes the
    later calibration pair `(score, label)` stable even after a re-run changes the assignment.

    :param sample_item_id: Sample the assignment belongs to.
    :param peak_assignment_id: The assignment being verified.
    :param verdict: confirmed | rejected | unsure.
    :param evidence_level: Basis for the verdict (see schemas.EvidenceLevel); the schema
        requires it for 'confirmed'.
    :param note: Optional free-text note.
    :param user_id: The verifying user (attribution).
    :return: Status envelope with the created verification record.
    """
    sample = await fetch_sample(sample_item_id)
    async with async_session() as session:
        assignment = await session.get(PeakAssignment, peak_assignment_id)
        if assignment is None or assignment.sample_item_id != sample_item_id:
            raise NotFoundException(
                f"Assignment '{peak_assignment_id}' not found for sample "
                f"'{sample.sample_item_name}'"
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
            verified_by=user_id,
            verified_utc=dt.now(timezone.utc),
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

    Returns every verdict (append-only history); the current verdict for a given assignment is
    the latest by ``verified_utc`` for its ``sample_peak_id`` + formula + adduct. The frontend
    derives per-assignment state from this list.
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


@api_controller()
async def recalibrate_instrument(
    instrument: str,
    score_version: int = SCORE_VERSION,
) -> dict:
    """Refit an instrument's confidence calibration from the verification labels (V2 loop).

    Gathers every ``confirmed`` / ``rejected`` verification of an **in-app** run for samples on this
    instrument, uses the arbitration ``evidence`` snapshotted at verification time as the score and
    the verdict as the label, fits a new Platt curve, and writes it as the new active row in the
    calibration store (carrying the corroboration weights forward). The curve stays **provisional**
    unless enough positives carry strong (reference-standard / MS-MS) evidence -- a pile of visual
    confirmations can't graduate it. Reports before/after held-out ECE so the change is auditable.

    **Imported runs are excluded from the label pool.** Verifying an imported assignment is fine and
    useful -- the verification endpoints are engine-agnostic and the human verdict is what matters --
    but the ``evidence`` this fits over is snapshotted from the judged row's provenance, which on an
    imported run is a value an editor supplied. This curve is what every assignment's P(correct)
    reads from, which is why the route is superuser-only, so an editor-supplied number must not
    reach it. Those verifications are still stored, listed and shown; they just do not vote here.

    :param instrument: Instrument class to recalibrate (e.g. "orbi").
    :param score_version: Fit-score version the labels were scored under (defaults to current).
    :return: Status envelope with whether it recalibrated and the before/after ECE + label counts.
    """
    inst = str(instrument).lower()
    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    AssignmentVerification.verdict,
                    AssignmentVerification.evidence,
                    AssignmentVerification.evidence_level,
                    Sample.filename,
                )
                .join(
                    Sample,
                    Sample.sample_item_id == AssignmentVerification.sample_item_id,
                )
                # Outer, and NULL passes: peak_assignment_run_id is nullable and
                # carries no foreign key, so a label whose run has since been
                # pruned no longer joins. Every run that existed before imports
                # did is the in-app engine's, so admitting the unjoinable ones
                # keeps an existing deployment's pool exactly as it was; only a
                # verification that positively belongs to another engine's run
                # is dropped.
                .outerjoin(
                    PeakAssignmentRun,
                    PeakAssignmentRun.peak_assignment_run_id
                    == AssignmentVerification.peak_assignment_run_id,
                )
                .where(
                    AssignmentVerification.verdict.in_(["confirmed", "rejected"]),
                    AssignmentVerification.evidence.is_not(None),
                    or_(
                        PeakAssignmentRun.engine.is_(None),
                        PeakAssignmentRun.engine == IN_APP_ENGINE,
                    ),
                )
            )
        ).all()

    scores, labels, levels = [], [], []
    for verdict, evidence, evidence_level, filename in rows:
        if get_instrument_type(filename) != inst:
            continue
        scores.append(float(evidence))
        labels.append(1 if verdict == "confirmed" else 0)
        levels.append(evidence_level)

    current = await load_calibration(inst, score_version)
    try:
        result = recalibrate(
            scores,
            labels,
            levels,
            instrument=inst,
            source=f"user verifications ({len(scores)} labels)",
            current=current,
        )
    except InsufficientCalibrationData as exc:
        return {
            "status": "success",
            "message": (
                f"Not enough verification labels to recalibrate '{inst}' "
                f"(need both confirmed and rejected): {exc}"
            ),
            "recalibrated": False,
            "instrument": inst,
            "n_pos": sum(labels),
            "n_neg": len(labels) - sum(labels),
        }

    await save_calibration(result["calibration"], score_version)
    return {
        "status": "success",
        "message": (
            f"Recalibrated '{inst}' from {result['n_pos'] + result['n_neg']} "
            f"verification labels (held-out ECE "
            f"{result['before_ece']} -> {result['after_ece']}"
            f"{'; provisional' if result['provisional'] else ''})"
        ),
        "recalibrated": True,
        "instrument": inst,
        "before_ece": result["before_ece"],
        "after_ece": result["after_ece"],
        "n_pos": result["n_pos"],
        "n_neg": result["n_neg"],
        "n_strong_positives": result["n_strong_positives"],
        "provisional": result["provisional"],
    }


# -------------------------------------------------------------------
# Assignment engine orchestration
# -------------------------------------------------------------------


def ineligible_reason(sample: Sample) -> str | None:
    """Why this sample cannot usefully be assigned, or None if it can.

    Mirrors the eligibility checks the targeted controllers apply
    (``match_compute_batch`` and the rematch verified-calibration gate): a
    blank sample carries no peaks, and a sample whose m/z calibration exists
    but is unverified would produce mass errors - and therefore fit scores and
    tiers - that mean nothing. Shared by the batch partition and the
    per-sample guard so one sample assigned from the sample menu is refused on
    exactly the condition a batch would have skipped it under.

    :param sample: Sample to test.
    :return: A short reason string, or None when the sample is eligible.
    """
    if sample.instrument_function_id is None:
        return "blank sample (no peaks)"
    if sample.mz_calibration and not sample.mz_calibration.get("verified", False):
        return "m/z calibration not verified"
    return None


async def _fetch_sample_mechanisms(
    sample: Sample,
) -> tuple[list[str], list[SimpleNamespace]]:
    """Resolve the sample's ionization mechanisms once per run.

    Every stage of a run needs the same mechanism set, so it is fetched here
    once and threaded through rather than re-queried per stage (which also
    used to open a second pooled connection inside an already-open session).
    The mechanism rows are detached into plain namespaces so downstream
    CPU-bound work can use them off the event loop.

    :param sample: Sample model object
    :return: (all mechanism ids of the sample's ionization mode,
        polarity-matching mechanism rows as detached namespaces)
    """
    mechanism_ids = await fetch_sample_ionization_mechanism_ids(sample.sample_item_id)
    async with async_session() as session:
        mechanisms = (
            (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism_id.in_(mechanism_ids),
                        IonizationMechanism.ionization_mechanism_polarity
                        == sample.polarity,
                    )
                )
            )
            .scalars()
            .all()
        )
    mechanism_specs = [
        SimpleNamespace(
            ionization_mechanism_id=m.ionization_mechanism_id,
            ionization_mechanism=m.ionization_mechanism,
            ionization_mechanism_polarity=m.ionization_mechanism_polarity,
        )
        for m in mechanisms
    ]
    return mechanism_ids, mechanism_specs


async def _fetch_known_target_isotopes(
    sample: Sample,
    isotope_abundance_threshold: float,
    ionization_mechanism_ids: list[str],
) -> pd.DataFrame:
    """
    Fetch the full known-isotopologue set for a sample (Stage A input).

    Unlike fetch_sample_unmatched_target_isotopes this does not exclude
    already-matched isotopes - a peak assignment run always evaluates the
    whole library - and it carries the compound/ion metadata that ends up
    denormalized on PeakAssignment rows.

    :param sample: Sample model object
    :param isotope_abundance_threshold: Minimum relative abundance for a
        target isotope to participate
    :param ionization_mechanism_ids: The sample's mechanism ids, resolved
        once per run by :func:`_fetch_sample_mechanisms`
    :return: DataFrame of target isotopes with compound/ion metadata
    """
    async with async_session() as session:
        resolution_type = (
            "LOW" if get_instrument_type(sample.filename) == "tof" else "HIGH"
        )
        abundance_threshold = isotope_abundance_threshold_expr(
            TargetIon.filter_params,
            sample.instrument,
            isotope_abundance_threshold,
        )

        stmt = (
            select(
                TargetIsotope.target_isotope_id,
                TargetIsotope.target_ion_id,
                TargetIsotope.target_isotope_formula,
                TargetIsotope.mz,
                TargetIsotope.relative_abundance,
                TargetIsotope.resolution,
                TargetIon.target_ion_formula,
                TargetIon.ionization_mechanism_id,
                TargetCompound.target_compound_id,
                TargetCompound.target_compound_formula,
                IonizationMechanism.ionization_mechanism,
                IonizationMechanism.ionization_mechanism_polarity,
            )
            .distinct(TargetIsotope.target_isotope_id)
            .select_from(TargetIsotope)
            .join(TargetIon, TargetIon.target_ion_id == TargetIsotope.target_ion_id)
            .join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
            )
            .join(
                TargetCompound,
                TargetCompound.target_compound_id == TargetIon.target_compound_id,
            )
            .join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_compound_id
                == TargetCompound.target_compound_id,
            )
            .join(
                TargetCollectionInSampleBatch,
                TargetCollectionInSampleBatch.target_collection_id
                == TargetCompoundInTargetCollection.target_collection_id,
            )
            .where(
                TargetCollectionInSampleBatch.sample_batch_id == sample.sample_batch_id,
                TargetIon.ionization_mechanism_id.in_(ionization_mechanism_ids),
                IonizationMechanism.ionization_mechanism_polarity == sample.polarity,
                TargetIsotope.resolution == resolution_type,
                TargetIsotope.relative_abundance >= abundance_threshold,
            )
        )
        if not (rows := (await session.execute(stmt)).all()):
            return pd.DataFrame()

    target_isotopes_df = pd.DataFrame([row._asdict() for row in rows])
    runtime.logger.info(
        f"Found {len(target_isotopes_df)} known target isotopes for sample "
        f"'{sample.sample_item_name}' (polarity: {sample.polarity})"
    )
    return target_isotopes_df


def _build_reference_isotopes_df(
    known_compositions: list,
    mechanisms: list,
    resolution_type: str,
    abundance_threshold: float,
) -> pd.DataFrame:
    """Expand reference formulas into Stage A isotope rows (CPU-bound).

    For every unique reference formula, reuses the same target-ion/IsoSpec path
    the curated library uses to produce ions -> isotopologues, keeps the rows for
    the sample's resolution above the abundance floor, and shapes them like
    :func:`_fetch_known_target_isotopes` output. Reference rows carry synthetic
    isotope/ion ids (for in-run grouping only), no ``target_compound_id``, and a
    ``reference_identities`` list the inversion drops into provenance.
    """
    mech_by_id = {m.ionization_mechanism_id: m for m in mechanisms}
    rows: list[dict] = []
    for known in known_compositions:
        compound = SimpleNamespace(
            target_compound_id="reference",
            target_compound_formula=known.formula,
        )
        try:
            ions, isotopes = generate_target_ions_from_composition(compound, mechanisms)
        except Exception as error:  # noqa: BLE001 - a bad formula skips, never fails the run
            runtime.logger.debug(
                f"Skipping reference formula '{known.formula}': {error}"
            )
            continue
        ion_by_id = {ion.target_ion_id: ion for ion in ions}
        identities = [asdict(identity) for identity in known.identities]
        for iso in isotopes:
            if iso.resolution != resolution_type:
                continue
            if iso.relative_abundance < abundance_threshold:
                continue
            ion = ion_by_id.get(iso.target_ion_id)
            if ion is None:
                continue
            mechanism = mech_by_id.get(ion.ionization_mechanism_id)
            rows.append(
                {
                    "target_isotope_id": iso.target_isotope_id,
                    "target_ion_id": iso.target_ion_id,
                    "target_isotope_formula": iso.target_isotope_formula,
                    "mz": iso.mz,
                    "relative_abundance": iso.relative_abundance,
                    "resolution": iso.resolution,
                    "target_ion_formula": ion.target_ion_formula,
                    "ionization_mechanism_id": ion.ionization_mechanism_id,
                    # No curated target for a reference-derived formula.
                    "target_compound_id": None,
                    "target_compound_formula": known.formula,
                    "ionization_mechanism": (
                        mechanism.ionization_mechanism if mechanism else None
                    ),
                    "ionization_mechanism_polarity": (
                        mechanism.ionization_mechanism_polarity if mechanism else None
                    ),
                    REFERENCE_IDENTITIES_COL: identities,
                }
            )
    return pd.DataFrame(rows)


def reference_license_gate() -> list[str] | None:
    """The reference licences Stage A is allowed to match against, or None.

    The reference mirror carries a per-record licence from ingest through to
    results precisely so a deployment can decline to match against sources
    whose terms it has not accepted (HMDB's adapter, for instance, asks that
    commercial terms be verified first). Nothing wired that to a setting until
    now: the call site passed nothing, so every active source was matched.

    ``None`` - the default, and what an env toml that says nothing yields -
    keeps exactly that behaviour. Gating is opt-in because narrowing it shrinks
    what Stage A can find with no trace in the UI: an operator must choose it,
    not inherit it.

    Read per call rather than captured at import so a restart is enough to pick
    up a change, and so tests can set it.

    :return: The allowlist (sorted, non-empty) or None for no gating.
    """
    return getattr(runtime.config, "reference_licenses", None)


# The expanded reference frame is identical for every run over the same
# (reference state, licence gate, mechanism set, resolution, abundance floor),
# so a Stage-A batch of N samples should pay the IsoSpec expansion once, not N
# times. The key's reference part comes from known_state_fingerprint, so a
# re-synced or (de)activated source invalidates naturally; the licence gate is
# in the key too, because narrowing it must not be served the wider frame a run
# from before the change left behind. A handful of entries covers a
# deployment's realistic (instrument, polarity, threshold) combinations.
_REFERENCE_ISOTOPE_CACHE_MAX = 8
_reference_isotope_cache: OrderedDict[tuple, pd.DataFrame] = OrderedDict()
_reference_isotope_cache_lock = asyncio.Lock()


async def _fetch_reference_known_isotopes(
    sample: Sample,
    isotope_abundance_threshold: float,
    mechanisms: list[SimpleNamespace],
) -> pd.DataFrame:
    """Reference-database contribution to the Stage A known set.

    Pulls the active reference compounds (bounded to the atmospheric window by
    :func:`iter_known_compositions`), then expands them into matchable isotope
    rows off the event loop. Returns an empty frame when there is no reference
    data or the sample has no matching ionization mechanisms - so the seam is a
    no-op until a reference database is loaded.

    The expansion is cached across runs (see the cache note above); the lock
    makes concurrent runs with the same key wait for one build instead of
    duplicating it.

    :param sample: Sample model object.
    :param isotope_abundance_threshold: Minimum relative abundance for a
        reference isotope to participate.
    :param mechanisms: The sample's polarity-matching mechanisms, resolved
        once per run by :func:`_fetch_sample_mechanisms`.
    :return: DataFrame in the known-isotope shape, or empty.
    """
    if not mechanisms:
        return pd.DataFrame()

    async with async_session() as session:
        fingerprint = await known_state_fingerprint(session)
    if not fingerprint:
        # No active reference sources: nothing to expand, and nothing worth a
        # cache slot either.
        return pd.DataFrame()

    licenses = reference_license_gate()
    resolution_type = "LOW" if get_instrument_type(sample.filename) == "tof" else "HIGH"
    cache_key = (
        fingerprint,
        None if licenses is None else tuple(licenses),
        tuple(sorted(m.ionization_mechanism_id for m in mechanisms)),
        resolution_type,
        isotope_abundance_threshold,
    )

    async with _reference_isotope_cache_lock:
        cached = _reference_isotope_cache.get(cache_key)
        if cached is not None:
            _reference_isotope_cache.move_to_end(cache_key)
            runtime.logger.debug(
                f"Reference isotope cache hit for sample '{sample.sample_item_name}'"
            )
            return cached.copy()

        async with async_session() as session:
            # licenses=None keeps every record, which is what an unconfigured
            # deployment must get - the argument is only narrowing.
            known = await iter_known_compositions(
                session,
                licenses=None if licenses is None else set(licenses),
            )
        if not known:
            reference_isotopes_df = pd.DataFrame()
        else:
            reference_isotopes_df = await asyncio.to_thread(
                _build_reference_isotopes_df,
                known,
                mechanisms,
                resolution_type,
                isotope_abundance_threshold,
            )
        gate = "" if licenses is None else f" (licences: {', '.join(licenses)})"
        runtime.logger.info(
            f"Built {len(reference_isotopes_df)} reference known isotopes from "
            f"{len(known)} reference formulas for sample "
            f"'{sample.sample_item_name}'{gate}"
        )
        _reference_isotope_cache[cache_key] = reference_isotopes_df
        while len(_reference_isotope_cache) > _REFERENCE_ISOTOPE_CACHE_MAX:
            _reference_isotope_cache.popitem(last=False)
        return reference_isotopes_df.copy()


def _combine_known_isotopes(
    target_isotopes_df: pd.DataFrame, reference_isotopes_df: pd.DataFrame
) -> pd.DataFrame:
    """Union the target-library and reference known-isotope frames for Stage A.

    Target rows gain a null ``reference_identities`` column so the two frames
    align; the matcher and inversion then treat both uniformly, with reference
    rows distinguished only by that carried column.
    """
    frames = [
        frame
        for frame in (target_isotopes_df, reference_isotopes_df)
        if not frame.empty
    ]
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    if REFERENCE_IDENTITIES_COL not in combined.columns:
        combined[REFERENCE_IDENTITIES_COL] = None
    return combined


def _untargeted_ionization_notations(
    mechanisms: list[SimpleNamespace],
) -> tuple[list[str], dict[str, str]]:
    """
    Resolve the sample's ionization mechanisms into the explicit-isotope
    notation used by the composition finder.

    :param mechanisms: The sample's polarity-matching mechanisms, resolved
        once per run by :func:`_fetch_sample_mechanisms`
    :return: (explicit notation strings, notation -> mechanism id mapping)
    """
    notations: list[str] = []
    mechanism_id_by_notation: dict[str, str] = {}
    for mechanism in mechanisms:
        notation, _ = to_explicit_isotope_format(mechanism.ionization_mechanism)
        notations.append(notation)
        mechanism_id_by_notation[notation] = mechanism.ionization_mechanism_id
    return notations, mechanism_id_by_notation


def _load_sample_peaks(sample: Sample) -> pd.DataFrame:
    """
    Load every observed peak of the sample with an averaged intensity.

    Uses peak heights for Orbitrap files and peak areas for TOF files,
    mirroring the targeted matcher.

    :param sample: Sample model object
    :return: DataFrame with sample_peak_id, mz, and intensity columns
    """
    peak_data = extract_peaks(sample.filename, sample.polarity, sample.t0, sample.t1)
    instrument_type = get_instrument_type(sample.filename)
    intensities = peak_data.heights if instrument_type == "orbi" else peak_data.areas
    peaks_df = pd.DataFrame(
        {
            "sample_peak_id": [str(peak_id) for peak_id in peak_data.peak_ids],
            "mz": peak_data.mz_values,
            "intensity": intensities if intensities is not None else 0.0,
        }
    )
    peaks_df["intensity"] = peaks_df["intensity"].fillna(0.0)
    return peaks_df


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

#: Key under which a run's stored config records the reference-licence gate
#: that was in force when it ran. Deliberately not a field on
#: :class:`PeakAssignmentConfig`: that model is the request body, so a field
#: there would let an API caller widen the gate its own run is matched under.
#: It is server state that a run *reports*, not configuration a client supplies.
REFERENCE_LICENSES_KEY = "reference_licenses"


def _stored_run_config(config: PeakAssignmentConfig) -> dict:
    """The blob persisted on a run: the requested config plus server-side state.

    A result should record what it was allowed to match, not only what it was
    asked to do - a run whose reference gate excluded half the mirror is not
    comparable with one that matched everything, and nothing else on the row
    would ever say so. The key is always present, ``None`` meaning ungated, so
    "everything was allowed" is distinguishable from a run written before this
    was recorded at all.

    :param config: The validated client-supplied run configuration.
    :return: A JSON-serializable dict for ``PeakAssignmentRun.config``.
    """
    stored = config.model_dump()
    stored[REFERENCE_LICENSES_KEY] = reference_license_gate()
    return stored


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
        config=_stored_run_config(config),
        # The same thresholds an import has to declare, recorded here too so a
        # tier means the same thing on either engine's run without reading into
        # the config blob.
        tier_bands={
            "assigned": config.assigned_threshold,
            "candidate": config.candidate_threshold,
        },
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
    peak_assignment_run_id: str, status: str, error: str | None = None
) -> None:
    """Mark a run completed/failed with its completion timestamp."""
    async with async_session() as session:
        await session.execute(
            update(PeakAssignmentRun)
            .where(PeakAssignmentRun.peak_assignment_run_id == peak_assignment_run_id)
            .values(
                status=status,
                error=error,
                peak_assignment_run_utc_completed=dt.now(timezone.utc),
            )
        )
        await session.commit()


# Samples with an assignment in flight in this worker. A run is CPU-bound - Stage B
# enumerates compositions for up to max_untargeted_peaks peaks in a worker thread -
# and writes a full ledger, and nothing about a second concurrent run of the same
# sample is useful: it produces a duplicate run the user did not ask for while
# competing for the same pool. Mirrors the batch guard in `batch.py`.
_sample_assignments_in_flight: set[str] = set()


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


async def _run_sample_assignment(
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
    search, and leftover peaks are persisted as unassigned.

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

    if (reason := ineligible_reason(sample)) is not None:
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
        match_params = await default_match_params(sample_item_id)

        notification = UserNotification(
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
        )

        # -- Load every observed peak of the sample
        peaks_df = _load_sample_peaks(sample)
        await send_progress_user_notification(notification, 0.1)

        # -- Resolve the sample's mechanism set once; every stage below
        # consumes the same resolution.
        mechanism_ids, mechanisms = await _fetch_sample_mechanisms(sample)

        # -- Stage A: database-first assignment from the known composition set:
        # the curated target library plus (when loaded) the reference mirror.
        stage_a_assignments: list[dict] = []
        target_isotopes_df = await _fetch_known_target_isotopes(
            sample, match_params.isotope_abundance_threshold, mechanism_ids
        )
        reference_isotopes_df = await _fetch_reference_known_isotopes(
            sample, match_params.isotope_abundance_threshold, mechanisms
        )
        known_isotopes_df = _combine_known_isotopes(
            target_isotopes_df, reference_isotopes_df
        )
        if not known_isotopes_df.empty:
            match_isotope_df = await compute_match_isotopes(
                filename=sample.filename,
                target_isotopes_df=known_isotopes_df,
                polarity=sample.polarity,
            )
            # Gate raw matches by the sample's match parameters, exactly as the
            # targeted Match pipeline does: this zeroes the score of peaks whose
            # m/z error, isotope-ratio error, or intensity falls outside
            # tolerance. Without it a peak tens of ppm off a target would be
            # tiered "assigned" here while the Match tab reports no match.
            if not match_isotope_df.empty:
                match_isotope_df = apply_match_params(match_isotope_df, match_params)
                # Deliberately score Stage A with the fit score (score_pattern_v2):
                # the peak-centric engine's scoring engine is the ion-level fit
                # quality, not the targeted matcher's per-isotopologue term. Runs
                # after gating so tolerance/intensity cuts carry into the fit.
                match_isotope_df = score_ions_by_fit(match_isotope_df)
            instrument = get_instrument_type(sample.filename)
            # Load this instrument's confidence calibration from the D6 store (active DB row,
            # else the in-code provisional curve, else None -> uncalibrated). Passing it in keeps
            # the engine DB-free; its corroboration_weights drive the P3 adduct fold-in.
            calibration = await load_calibration(instrument, SCORE_VERSION)
            stage_a_assignments = invert_matches_to_peak_assignments(
                match_isotope_df,
                sample_item_id=sample_item_id,
                peak_assignment_run_id=run.peak_assignment_run_id,
                possible_threshold=config.candidate_threshold,
                probable_threshold=config.assigned_threshold,
                max_alternatives=config.max_alternatives,
                instrument=instrument,
                calibration=calibration,
            )
        runtime.logger.info(
            f"Stage A assigned {len(stage_a_assignments)} of {len(peaks_df)} "
            f"peaks from the known target library"
        )
        await send_progress_user_notification(notification, 0.4)

        # -- Stage B: untargeted composition search for the remainder
        assigned_peak_ids = {
            assignment["sample_peak_id"] for assignment in stage_a_assignments
        }
        stage_b_assignments: list[dict] = []
        if config.run_untargeted:
            remainder_df = peaks_df[
                ~peaks_df["sample_peak_id"].isin(assigned_peak_ids)
                & (peaks_df["intensity"] >= config.peak_intensity_threshold)
                & (peaks_df["intensity"] > 0)
            ]
            # Composition enumeration cost scales with peak count; bound the
            # stage to the most intense unexplained peaks.
            remainder_df = remainder_df.nlargest(
                config.max_untargeted_peaks, "intensity"
            ).sort_values("mz")

            notations, mechanism_id_by_notation = _untargeted_ionization_notations(
                mechanisms
            )
            if remainder_df.empty or not notations:
                skip_reason = (
                    "no eligible unassigned peaks"
                    if remainder_df.empty
                    else "no polarity-compatible ionization mechanisms"
                )
                runtime.logger.info(f"Skipping untargeted stage: {skip_reason}")
            else:
                formula_ranges, _ = to_explicit_isotope_format(config.formula_ranges)
                search_config = CompositionSearchConfig(
                    ionizations=",".join(notations),
                    mass_range_ppm=config.mz_precision_ppm,
                    element_count_ranges=formula_ranges,
                    use_unsaturation=True,
                    min_unsaturation=-1000.0,
                    max_unsaturation=10000.0,
                )
                # assign_compositions is synchronous and CPU-bound (recursive
                # composition enumeration over up to max_untargeted_peaks). This
                # runs as a background task on the API event loop, so offload it
                # to a worker thread to avoid blocking every other request and
                # the progress notifications for the duration of the search.
                # Stage B opts into the Senior/RDBE feasibility cut: the
                # peak-centric engine wants chemically impossible formulas gone
                # before arbitration. It stays off for the legacy composition
                # search, which predates the rule being implemented.
                heuristics_config = HeuristicFilterConfig(use_senior=True)
                # One positionally-indexed frame feeds both the search and the
                # join back. The finder returns rows in the order it was given
                # them, so position - not float m/z equality - is what maps a
                # result to the peak it came from.
                search_peaks_df = remainder_df.reset_index(drop=True)
                matches_df, _ = await asyncio.to_thread(
                    assign_compositions,
                    search_peaks_df[["mz", "intensity"]],
                    search_config,
                    heuristics_config,
                )
                stage_b_assignments = untargeted_matches_to_peak_assignments(
                    matches_df,
                    peaks_df=search_peaks_df,
                    sample_item_id=sample_item_id,
                    peak_assignment_run_id=run.peak_assignment_run_id,
                    possible_threshold=config.candidate_threshold,
                    probable_threshold=config.assigned_threshold,
                    mechanism_id_by_notation=mechanism_id_by_notation,
                    formula_formatter=to_custom_element_format,
                    max_alternatives=config.max_alternatives,
                )
                runtime.logger.info(
                    f"Stage B assigned {len(stage_b_assignments)} of "
                    f"{len(remainder_df)} remaining peaks via untargeted search"
                )
        await send_progress_user_notification(notification, 0.8)

        # -- Persist the complete ledger: one row per observed peak
        assigned_peak_ids.update(
            assignment["sample_peak_id"] for assignment in stage_b_assignments
        )
        unassigned_df = peaks_df[~peaks_df["sample_peak_id"].isin(assigned_peak_ids)]
        unassigned_assignments = build_unassigned_assignments(
            unassigned_df,
            sample_item_id=sample_item_id,
            peak_assignment_run_id=run.peak_assignment_run_id,
        )

        all_assignments = (
            stage_a_assignments + stage_b_assignments + unassigned_assignments
        )
        # Insert owners before children: owner_peak_assignment_id is a
        # self-referential FK validated per row during the bulk insert.
        all_assignments.sort(
            key=lambda row: row["owner_peak_assignment_id"] is not None
        )
        if all_assignments:
            async with async_session() as session:
                await session.execute(insert(PeakAssignment), all_assignments)
                await session.commit()

        await _finalize_run(run.peak_assignment_run_id, "completed")
        await send_progress_user_notification(notification, 1.0)

        # Fold this completed run into the batch peaks so the batch overview
        # reflects every assignment path: sample arrival, an explicit single-sample
        # re-assign, and a batch assign (which loops this function). Isolated -- a
        # fold-in failure must never fail or un-complete the assignment itself.
        try:
            from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
                fold_sample_into_batch_peaks,
            )

            await fold_sample_into_batch_peaks(sample_item_id)
        except Exception as fold_error:  # noqa: BLE001 - fold-in is best-effort
            runtime.logger.warning(
                f"Batch-peak fold-in failed for sample '{sample_item_id}' "
                f"(run '{run.peak_assignment_run_id}'): {fold_error}"
            )

        message = (
            f"Assigned peaks for sample '{sample.sample_item_name}': "
            f"{len(stage_a_assignments)} from the target library, "
            f"{len(stage_b_assignments)} untargeted, "
            f"{len(unassigned_assignments)} unassigned "
            f"({len(all_assignments)} peaks total)."
        )
        runtime.logger.info(message)
        return {
            "status": "success",
            "message": message,
            "data": {
                "peak_assignment_run_id": run.peak_assignment_run_id,
                "total_peaks": len(all_assignments),
                "database_assigned": len(stage_a_assignments),
                "untargeted_assigned": len(stage_b_assignments),
                "unassigned": len(unassigned_assignments),
            },
            "_notification_data": {
                "sample_batch_id": sample.sample_batch_id,
                "sample_item_id": sample_item_id,
                "peak_assignment_run_id": run.peak_assignment_run_id,
            },
        }
    except asyncio.CancelledError:
        # CancelledError is a BaseException, so the failure handler below never
        # sees it - without this branch a cancelled run stayed 'running' until
        # the startup reaper, invisible to the read model and (correctly)
        # refused by the retention prune. Finalize to the distinct 'cancelled'
        # state and re-raise so cancellation still propagates. Shielded: the
        # task is already cancelled, and a second cancellation arriving during
        # the finalizer's own awaits must not abort the terminal write.
        await asyncio.shield(
            _finalize_run(
                run.peak_assignment_run_id, "cancelled", error="Run was cancelled"
            )
        )
        runtime.logger.info(
            f"Peak assignment run '{run.peak_assignment_run_id}' was cancelled"
        )
        raise
    except Exception as e:
        await _finalize_run(run.peak_assignment_run_id, "failed", error=str(e))
        runtime.logger.error(
            f"Peak assignment run '{run.peak_assignment_run_id}' failed: {e}"
        )
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

    :param sample_item_id: ID of the sample item to assign
    :param user_id: Current user triggered operation (for user notifications)
    :param parent_id: Parent process identifier for progress nesting
    """
    if not peak_assignment_enabled():
        return
    try:
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
