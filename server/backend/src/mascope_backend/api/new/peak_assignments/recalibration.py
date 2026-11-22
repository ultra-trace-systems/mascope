"""An instrument's confidence curve fitted again from the verdicts on its rows.

The live labels of an instrument class, read per ionization profile, and the
curve fitted on them (``calibration_store``), which every later Stage A reads
its ``p_correct`` off.
"""

import re
from dataclasses import replace

from sqlalchemy import and_, or_, select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.new.ionization.modes.versions import setup_stamp
from mascope_backend.api.new.peak_assignments.calibration_store import (
    load_calibration,
    save_calibration,
)
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    STAGE_B_V2_ENGINE_VERSION,
)
from mascope_backend.api.new.peak_assignments.profiles import RESOLVED_PROFILE_KEY
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_M0,
    SOURCE_DATABASE,
    SOURCE_UNTARGETED,
)
from mascope_backend.api.new.peak_assignments.run_config import IONIZATION_SETUP_KEY
from mascope_backend.api.new.peak_assignments.tiers import TIER_ASSIGNED, TIER_CANDIDATE
from mascope_backend.db import (
    AssignmentVerification,
    IonizationMechanism,
    IonizationMode,
    PeakAssignment,
    PeakAssignmentRun,
    Sample,
    async_session,
)
from mascope_file.name import get_instrument_type
from mascope_tools.composition.calibration import (
    CalibrationRefused,
    InsufficientCalibrationData,
    recalibrate,
    refit_corroboration,
)
from mascope_tools.composition.heuristic_filter import SCORE_VERSION
from mascope_tools.composition.mechanism_notation import mechanism_key


def _version_tuple(version: str | None) -> tuple[int, ...]:
    """The leading numeric fields of an engine version, ``"0.5.0"`` -> (0, 5, 0).

    Reading stops at the first field that is not a plain number, after its
    leading digits: ``"0.9.0+assign0.6.1"`` -> (0, 9, 0).
    """
    fields: list[int] = []
    for part in str(version or "").split("."):
        digits = re.match(r"\d+", part)
        if digits is None:
            break
        fields.append(int(digits.group()))
        if digits.end() != len(part):
            break
    return tuple(fields)


def _on_v2_scale(engine_version: str | None, source: str | None) -> bool:
    """Whether a label's evidence snapshot is on the fit-score v2 scale.

    Every row of a run at :data:`STAGE_B_V2_ENGINE_VERSION` or later is; before
    it, only Stage A's (the database rows), since the untargeted stage still
    fitted with v1 while it stamped v2. A label of an older run is therefore
    admitted only where its judged row is still there to say it is Stage A's.
    A label whose run is gone, or whose run's version does not read as one,
    says no version, and is admitted as it always was: the pool of a
    deployment does not shrink on a guess.

    :param engine_version: The judged run's engine version, None where the run
        is gone.
    :param source: The judged row's source, None where the row is gone.
    """
    version = _version_tuple(engine_version)
    if not version or version >= _version_tuple(STAGE_B_V2_ENGINE_VERSION):
        return True
    return source == SOURCE_DATABASE


async def _live_label_rows(session) -> list:
    """The calibration's label pool, before it is split by instrument class.

    Every **current** ``confirmed`` / ``rejected`` verification with an evidence
    snapshot on the fit-score v2 scale (:func:`_on_v2_scale`), of an **in-app**
    run (or of a run since pruned), never of a decoy run's row. Rows of both
    stages: the untargeted stage's evidence has been on the scale Stage A's curve
    is fit on since step 2.1, so its verdicts are labels of the same curve.

    Each label carries, by name: ``verdict``, ``evidence``, ``evidence_level``,
    the sample's ``filename``, the judged run's ``run_config`` (None where the
    run is gone), the sample's ``mode_id`` and its ``version``, the run's
    ``engine_version``, the verdict's ``run_id``, ``sample_item_id`` and
    mechanism ``notation``, and the judged row's ``source``, ``role`` and
    ``target_compound_id`` (None where the row is gone), by which the
    corroboration refit finds the run's other rows of its compound. Read by
    :func:`recalibrate_instrument`, which fits on it, and by
    :func:`get_verification_queue`, which counts the labels near a row's
    evidence, so the queue ranks by the labels the fit reads.
    """
    rows = (
        await session.execute(
            select(
                AssignmentVerification.verdict,
                AssignmentVerification.evidence,
                AssignmentVerification.evidence_level,
                Sample.filename,
                PeakAssignmentRun.config.label("run_config"),
                Sample.ionization_mode_id.label("mode_id"),
                IonizationMode.version,
                PeakAssignmentRun.engine_version,
                AssignmentVerification.peak_assignment_run_id.label("run_id"),
                AssignmentVerification.sample_item_id,
                PeakAssignment.source,
                PeakAssignment.role,
                PeakAssignment.target_compound_id,
                IonizationMechanism.ionization_mechanism.label("notation"),
            )
            .join(
                Sample,
                Sample.sample_item_id == AssignmentVerification.sample_item_id,
            )
            .outerjoin(
                IonizationMode,
                IonizationMode.ionization_mode_id == Sample.ionization_mode_id,
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
            # The judged row, gone (SET NULL) once its run is pruned.
            .outerjoin(
                PeakAssignment,
                PeakAssignment.peak_assignment_id
                == AssignmentVerification.peak_assignment_id,
            )
            .outerjoin(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == AssignmentVerification.ionization_mechanism_id,
            )
            .where(
                AssignmentVerification.verdict.in_(["confirmed", "rejected"]),
                AssignmentVerification.evidence.is_not(None),
                # Live verdicts only. Without this the fit sees the whole history, so a
                # user who changed their mind contributes one label to *each* class at an
                # identical evidence value - a contradictory pair that is noise to a Platt
                # fit, drags held-out AUC toward 0.5, and counts toward the minimum-label
                # gates that are supposed to be the guardrail.
                AssignmentVerification.superseded_utc.is_(None),
                or_(
                    PeakAssignmentRun.engine.is_(None),
                    # A decoy run's rows take no verdict
                    # (`create_verification`); kept out here as well, so
                    # the pool does not rest on that refusal alone.
                    and_(
                        PeakAssignmentRun.engine == IN_APP_ENGINE,
                        PeakAssignmentRun.decoy.is_(None),
                    ),
                ),
            )
        )
    ).all()
    return [row for row in rows if _on_v2_scale(row.engine_version, row.source)]


async def _confident_channels(
    session, labels: list
) -> dict[tuple[str, str], dict[str, set[str]]]:
    """The channels each judged run committed each compound through, confidently.

    Per judged run and sample, per target compound, the channels of its
    database-stage monoisotopic rows tiered assigned or candidate: the siblings
    the engine folds a corroboration weight in for
    (``engine._fold_adduct_corroboration``), which groups Stage A's winners by
    compound. A low-confidence sibling adds nothing there, and neither does an
    untargeted row of the same neutral, which that fold never sees. Read off the
    run's rows rather than the judged row's cross-channel record, which lists a
    channel at any tier and of either stage.

    :param session: An open session.
    :param labels: The labels whose runs to read (:func:`_live_label_rows`).
    :return: Per ``(run_id, sample_item_id)``, per compound id, its channels.
    """
    runs = {label.run_id for label in labels if label.run_id}
    samples = {label.sample_item_id for label in labels if label.run_id}
    if not runs:
        return {}
    rows = (
        await session.execute(
            select(
                PeakAssignment.peak_assignment_run_id,
                PeakAssignment.sample_item_id,
                PeakAssignment.target_compound_id,
                IonizationMechanism.ionization_mechanism,
            )
            .join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == PeakAssignment.ionization_mechanism_id,
            )
            .where(
                PeakAssignment.peak_assignment_run_id.in_(runs),
                PeakAssignment.sample_item_id.in_(samples),
                PeakAssignment.role == ROLE_M0,
                PeakAssignment.source == SOURCE_DATABASE,
                PeakAssignment.tier.in_([TIER_ASSIGNED, TIER_CANDIDATE]),
            )
        )
    ).all()
    seen: dict[tuple[str, str], dict[str, set[str]]] = {}
    for run_id, sample_item_id, compound_id, notation in rows:
        seen.setdefault((run_id, sample_item_id), {}).setdefault(
            compound_id, set()
        ).add(notation)
    return seen


def _other_channels(
    label, confident: dict[tuple[str, str], dict[str, set[str]]]
) -> list[str] | None:
    """The other channels a label's compound was confidently committed through.

    The channels its run committed the judged row's target compound through at
    assigned or candidate (:func:`_confident_channels`), the row's own channel
    left out. None where the judged row is gone, or is not a database-stage
    monoisotopic row of a target compound: the engine folds corroboration into
    those rows alone, so any other label has nothing to say about the weights,
    and the refit leaves it out. It still counts toward the curve.
    """
    if (
        label.role != ROLE_M0
        or label.source != SOURCE_DATABASE
        or not label.target_compound_id
    ):
        return None
    channels = confident.get((label.run_id, label.sample_item_id), {}).get(
        label.target_compound_id, set()
    )
    own = mechanism_key(label.notation) if label.notation else None
    return sorted(
        channel for channel in channels if own is None or mechanism_key(channel) != own
    )


def _label_profile(label) -> str | None:
    """The reagent profile a label was scored under: its run's resolved one."""
    return _run_profile(label.run_config)


@api_controller()
async def recalibrate_instrument(
    instrument: str,
    score_version: int = SCORE_VERSION,
    profile: str | None = None,
) -> dict:
    """Refit an instrument's confidence calibration from the verification labels (V2 loop).

    Gathers every **current** ``confirmed`` / ``rejected`` verification of an **in-app** run for
    samples on this instrument - one per identity, superseded verdicts excluded, of either
    stage once its evidence is on the v2 scale (:func:`_live_label_rows`) - uses the
    arbitration ``evidence`` snapshotted at verification time as the score and the verdict as the
    label, fits a new Platt curve, and writes it as the new active row in the calibration store.
    Each label is weighed by the evidence level its verdict states (``EVIDENCE_LEVEL_WEIGHTS``: a
    reference standard or MS/MS above a visual look), in the fit and in both held-out ECEs, and
    the curve records the weights. The curve stays **provisional** unless enough positives carry
    strong (reference-standard / MS-MS) evidence -- a pile of visual confirmations can't graduate
    it. Reports before/after held-out ECE so the change is auditable, and keeps the curve in
    force when the refit's held-out ECE is worse than its on the same labels.

    **Per reagent profile.** Given a ``profile``, only the labels of runs that resolved to it
    are fit. The curve records the profile its labels were all scored under (given, or the one
    every label shares; none where they span several), and a run under another profile reads it
    with a warning (the profiles design's first calibration step: recorded, not keyed on). Where
    the curve has one profile, its corroboration weights are refit from that profile's labels
    (``refit_corroboration``: how much likelier a confirmed label's compound was also seen
    through an adduct than a rejected one's). Only the labels on database-stage rows of a target
    compound feed the weights, as only those rows have a weight folded in; every adduct the
    labels cannot refit, and every weight of a curve fit across profiles, is carried forward.

    **Imported runs are excluded from the label pool.** Verifying an imported assignment is fine and
    useful -- the verification endpoints are engine-agnostic and the human verdict is what matters --
    but the ``evidence`` this fits over is snapshotted from the judged row's provenance, which on an
    imported run is a value an editor supplied. This curve is what every assignment's P(correct)
    reads from, which is why the route is superuser-only, so an editor-supplied number must not
    reach it. Those verifications are still stored, listed and shown; they just do not vote here.

    :param instrument: Instrument class to recalibrate (e.g. "orbi").
    :param score_version: Fit-score version the labels were scored under (defaults to current).
    :param profile: Fit only the labels of this reagent profile; None for every profile's.
    :return: Status envelope with whether it recalibrated and the before/after ECE + label counts.
    """
    inst = str(instrument).lower()
    async with async_session() as session:
        pool = await _live_label_rows(session)

    labels_of = [
        label
        for label in pool
        if _instrument_class(label.filename) == inst
        and (profile is None or _label_profile(label) == profile)
    ]
    scores = [float(label.evidence) for label in labels_of]
    labels = [1 if label.verdict == "confirmed" else 0 for label in labels_of]
    levels = [label.evidence_level for label in labels_of]
    n_untargeted = sum(1 for label in labels_of if label.source == SOURCE_UNTARGETED)
    profiles = {_label_profile(label) for label in labels_of}
    fit_profile = profile or (
        next(iter(profiles)) if len(profiles) == 1 and None not in profiles else None
    )
    # The setups the labels were scored under, recorded on the curve: the run
    # the evidence came from says which, and a label whose run is gone is
    # taken under its sample's version.
    setups: list[dict] = []
    for label in labels_of:
        run_config = label.run_config or {}
        setup = (
            run_config.get(IONIZATION_SETUP_KEY)
            if IONIZATION_SETUP_KEY in run_config
            else setup_stamp(label.mode_id, label.version)
        )
        if setup is not None and setup not in setups:
            setups.append(setup)

    counts = {
        "instrument": inst,
        "reagent_profile": fit_profile,
        "n_pos": sum(labels),
        "n_neg": len(labels) - sum(labels),
        "n_untargeted": n_untargeted,
    }
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
    except CalibrationRefused as exc:
        reason = (
            "need both confirmed and rejected"
            if isinstance(exc, InsufficientCalibrationData)
            else "they do not make a usable curve"
        )
        return {
            "status": "success",
            "message": (
                f"Not enough verification labels to recalibrate '{inst}'"
                f"{_profile_phrase(profile)} ({reason}): {exc}"
            ),
            "recalibrated": False,
            **counts,
        }

    reported = {
        **counts,
        "before_ece": result["before_ece"],
        "after_ece": result["after_ece"],
        "n_pos": result["n_pos"],
        "n_neg": result["n_neg"],
        "n_strong_positives": result["n_strong_positives"],
        "provisional": result["provisional"],
        "label_weights": result["label_weights"],
    }
    if not result["activate"]:
        return {
            "status": "success",
            "message": (
                f"Kept the calibration of '{inst}': the refit from "
                f"{result['n_pos'] + result['n_neg']} verification labels"
                f"{_profile_phrase(profile)} has a worse held-out ECE than it "
                f"({result['before_ece']} -> {result['after_ece']})"
            ),
            "recalibrated": False,
            "refusal": result["refusal"],
            **reported,
        }

    calibration = result["calibration"]
    corroboration = None
    if fit_profile is not None:
        async with async_session() as session:
            confident = await _confident_channels(session, labels_of)
        observed, refit_labels, refit_levels = [], [], []
        for label, y, level in zip(labels_of, labels, levels, strict=True):
            others = _other_channels(label, confident)
            if others is not None:
                observed.append(others)
                refit_labels.append(y)
                refit_levels.append(level)
        corroboration = refit_corroboration(
            observed,
            refit_labels,
            refit_levels,
            current=calibration.corroboration_weights,
        )
        calibration = replace(
            calibration, corroboration_weights=corroboration["weights"] or None
        )

    await save_calibration(
        calibration,
        score_version,
        sorted(setups, key=lambda s: (s["ionization_mode_id"], s.get("version") or 0)),
        reagent_profile=fit_profile,
    )
    refit = (corroboration or {}).get("refit") or {}
    return {
        "status": "success",
        "message": (
            f"Recalibrated '{inst}'{_profile_phrase(fit_profile)} from "
            f"{result['n_pos'] + result['n_neg']} verification labels weighed by "
            f"evidence level (held-out ECE {result['before_ece']} -> "
            f"{result['after_ece']}"
            f"{'; provisional' if result['provisional'] else ''}"
            f"{f'; {len(refit)} corroboration weight(s) refit' if refit else ''})"
        ),
        "recalibrated": True,
        **reported,
        "corroboration_refit": refit or None,
        "corroboration_weights": (
            dict(calibration.corroboration_weights)
            if calibration.corroboration_weights
            else None
        ),
    }


def _profile_phrase(profile: str | None) -> str:
    """`` for profile 'x'`` where a fit names one, else nothing."""
    return f" for profile '{profile}'" if profile else ""


def _instrument_class(filename: str | None) -> str | None:
    """A sample's instrument class, or None where its file names none."""
    try:
        return get_instrument_type(filename) if filename else None
    except ValueError:
        return None


def _run_profile(run_config: dict | None) -> str | None:
    """The reagent profile a run resolved to, or None where it records none."""
    resolved = (run_config or {}).get(RESOLVED_PROFILE_KEY)
    return resolved.get("profile") if isinstance(resolved, dict) else None
