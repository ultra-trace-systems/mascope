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
from sqlalchemy import func, insert, select, update

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
    NotFoundException,
    raise_api_warning,
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
from mascope_backend.api.new.peak_assignments.calibration_store import (
    load_calibration,
    save_calibration,
)
from mascope_backend.api.new.peak_assignments.config import (
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

    Paged. The ledger is deliberately complete - one row per detected peak,
    each carrying alternatives and provenance JSON - so a dense sample runs to
    tens of thousands of rows and reading them in one response would serialize
    tens of megabytes through Pydantic on the event loop. ``total`` reports the
    match count across all pages so a client knows when it has the whole run.

    :param sample_item_id: Unique identifier of the sample item
    :param peak_assignment_run_id: Specific run to read; defaults to the
        latest completed run
    :param tier: Optional filter by confidence tier
    :param role: Optional filter by peak role
    :param source: Optional filter by assignment source (database/untargeted)
    :param limit: Maximum rows to return in this page
    :param offset: Rows to skip, for paging
    :return: Dictionary with status, message, total, and assignment rows. Run
        identity is on each row (peak_assignment_run_id); run metadata is served
        by the runs endpoint.
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
            select(PeakAssignment)
            .where(*filters)
            .order_by(PeakAssignment.sample_peak_mz, PeakAssignment.peak_assignment_id)
            .offset(offset)
            .limit(limit)
        )
        assignments = (await session.execute(query)).scalars().all()

    data = [assignment.to_dict() for assignment in assignments]
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

    Gathers every ``confirmed`` / ``rejected`` verification for samples on this instrument, uses the
    arbitration ``evidence`` snapshotted at verification time as the score and the verdict as the
    label, fits a new Platt curve, and writes it as the new active row in the calibration store
    (carrying the corroboration weights forward). The curve stays **provisional** unless enough
    positives carry strong (reference-standard / MS-MS) evidence -- a pile of visual confirmations
    can't graduate it. Reports before/after held-out ECE so the change is auditable.

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
                .where(
                    AssignmentVerification.verdict.in_(["confirmed", "rejected"]),
                    AssignmentVerification.evidence.is_not(None),
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


# The expanded reference frame is identical for every run over the same
# (reference state, mechanism set, resolution, abundance floor), so a Stage-A
# batch of N samples should pay the IsoSpec expansion once, not N times. The
# key's reference part comes from known_state_fingerprint, so a re-synced or
# (de)activated source invalidates naturally. A handful of entries covers a
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

    resolution_type = "LOW" if get_instrument_type(sample.filename) == "tof" else "HIGH"
    cache_key = (
        fingerprint,
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
            known = await iter_known_compositions(session)
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
        runtime.logger.info(
            f"Built {len(reference_isotopes_df)} reference known isotopes from "
            f"{len(known)} reference formulas for sample '{sample.sample_item_name}'"
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


async def _create_run(
    sample_item_id: str, config: PeakAssignmentConfig
) -> PeakAssignmentRun:
    """Create and commit a PeakAssignmentRun row in 'running' state."""
    run = PeakAssignmentRun(
        peak_assignment_run_id=gen_id(16),
        sample_item_id=sample_item_id,
        engine_version=PEAK_ASSIGNMENT_ENGINE_VERSION,
        status="running",
        config=config.model_dump(),
        peak_assignment_run_utc_created=dt.now(timezone.utc),
    )
    async with async_session() as session:
        session.add(run)
        await session.commit()
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
) -> dict:
    """
    Run the two-stage peak assignment engine over a sample.

    Refuses immediately when this worker is already assigning the sample, rather
    than queueing a second full run behind the first - what a double-clicked
    "Assign peaks" deserves. See :func:`_run_sample_assignment` for the engine.

    :param sample_item_id: ID of the sample item to assign
    :param config: Optional run configuration; defaults are used when omitted
    :param independent_transaction: Flag for transaction handling
    :param user_id: Current user triggered operation (for user notifications)
    :param process_id: Process identifier for progress tracking
    :param parent_id: Parent process identifier
    :return: A dictionary with run summary and status message
    """
    # Claimed before the first await: checking and adding either side of one would
    # let two concurrent requests both pass the check.
    if sample_item_id in _sample_assignments_in_flight:
        sample = await fetch_sample(sample_item_id)
        message = (
            f"Peak assignment is already running for sample "
            f"'{sample.sample_item_name}'."
        )
        runtime.logger.info(message)
        return {"status": "skipped", "message": message}
    _sample_assignments_in_flight.add(sample_item_id)
    try:
        return await _run_sample_assignment(
            sample_item_id=sample_item_id,
            config=config,
            user_id=user_id,
            process_id=process_id,
            parent_id=parent_id,
        )
    finally:
        _sample_assignments_in_flight.discard(sample_item_id)


async def _run_sample_assignment(
    sample_item_id: str,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Run the two-stage peak assignment engine over a sample.

    Every observed peak of the sample gets exactly one PeakAssignment row in
    a new PeakAssignmentRun: Stage A assigns from the known target library,
    Stage B (optional) assigns the remainder via untargeted composition
    search, and leftover peaks are persisted as unassigned.

    :param sample_item_id: ID of the sample item to assign
    :param config: Optional run configuration; defaults are used when omitted
    :param independent_transaction: Flag for transaction handling
    :param user_id: Current user triggered operation (for user notifications)
    :param process_id: Process identifier for progress tracking
    :param parent_id: Parent process identifier
    :return: A dictionary with run summary and status message
    """
    sample = await fetch_sample(sample_item_id)
    config = config or PeakAssignmentConfig()

    if sample.instrument_function_id is None:
        # Blank samples carry no peaks; nothing to assign
        warning_message = (
            f"Sample '{sample.sample_item_name}' has no peaks. "
            "Peak assignment is skipped."
        )
        raise_api_warning(warning_message, {"sample_item_id": sample_item_id})
        return {
            "status": "skipped",
            "message": warning_message,
            "_notification_data": {
                "sample_batch_id": sample.sample_batch_id,
                "sample_item_id": sample_item_id,
            },
        }

    match_params = await default_match_params(sample_item_id)
    run = await _create_run(sample_item_id, config)

    # Everything after run creation runs under the failure finalizer: an
    # exception anywhere past this point must mark the run 'failed', or it
    # stays 'running' until the startup reaper.
    try:
        runtime.logger.info(
            f"Starting peak assignment run '{run.peak_assignment_run_id}' "
            f"for sample '{sample.sample_item_name}'"
        )

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
            # tiered "identified" here while the Match tab reports no match.
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
                probable_threshold=config.identified_threshold,
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
                    probable_threshold=config.identified_threshold,
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
