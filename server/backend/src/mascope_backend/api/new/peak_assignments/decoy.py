"""Decoy runs: the engine re-run on a sample with its chemistry made wrong.

The gate's metrics count agreement with the reference, intrinsic plausibility
and rule outcomes; none of them estimates how many assigned rows a sample would
still get if its chemistry were wrong (``docs/dev/assignment_quality_plan.md``,
step 5.2). A decoy run does: the engine runs again on a sample it has already
assigned, its *control*, with one arm made wrong, and what still reaches
``assigned`` is a bound on what it assigns falsely. The reference engine
validates its own releases with the same two arms (its ``scripts/scorecard.py``):

- **a shifted axis.** Every peak's m/z scaled by a few ppm (9 either way on an
  Orbitrap) or moved by a fraction of a dalton (0.35 on a TOF). The spectrum is
  the same; no true formula is on it. A ppm shift inside the window the run
  searches and matches in is refused, since the true formula stays in reach
  and the arm would be no decoy.
- **swapped adducts.** Every channel of the sample's mode searched through an
  adduct of the same polarity the reagent profile does not declare, and no
  secondary channel opened. The ions are where they were; every neutral read
  off them is wrong, except where the swap re-splits an ion the control already
  reads, which the comparison counts apart (``tooling/assignment_compare``).

Every arm runs at the control's calibration, as the reference's do: Stage A's
fit, the untargeted search, the seeded re-score and the mass gate are scored at
the centre and width the control recorded, and every fit score at the envelope
tolerance it recorded, never fitted again on the decoy's own rows. Those rows are wrong formulas on a moved axis, and fitted on them a
run would measure the shift back out of the spectrum it was put into, or fail
to calibrate and judge with its mass gate off.

A shift moves the axis off every formula of the sample, not off the source
that made the spectrum. The two passes that look for the source's own ions,
the secondary channels' carriers and the reagent pre-pass's lines, read the
peaks on the axis as stored (``service._source_axis``), so a shifted arm
opens the channels its control opened and claims the lines its control
claimed, at the offset its control measured them at; the reference's arms
open the side channels their control recorded too. (The adduct arm moves no
axis, and opens no secondary channel at all.) Read on the moved axis, a dalton shift
would put both out of the passes' 20 ppm reach: the arm would search fewer
channels than its control and offer the stages the source's brightest ions.

Everything here is pure. The service reads the control and the mechanism rows
and runs the arm (``service.decoy_plan``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace

from pydantic import ValidationError

from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    PeakAssignmentDecoy,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ENVELOPE_TOLERANCE_KEY,
    MASS_CALIBRATION_KEY,
    PATTERN_SCORING_KEY,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import (
    RunEnvelopeFit,
    inherited_envelope_fit,
)
from mascope_backend.api.new.peak_assignments.mass_gate import MassCalibration
from mascope_backend.api.new.peak_assignments.profiles import RESOLVED_PROFILE_KEY
from mascope_tools.composition.mass_accuracy import (
    MU_SOURCE_FITTED,
    MU_SOURCE_REAGENT,
    PRED_SIGMA_PPM,
    MassTrend,
    ReagentOffset,
    SampleMassAccuracy,
)
from mascope_tools.composition.mechanism_notation import mechanism_key
from mascope_tools.composition.profiles import get_reagent_profile
from mascope_tools.composition.reagents import secondary_channels


#: Key under which a decoy run's stored config records what it ran against:
#: the control's id, the window a shift had to clear, the channels it swapped
#: and the calibration it inherited. Server state, beside the request's own
#: ``decoy`` block, for the reason the licence gate is: a client says which arm,
#: never which run it bounds.
DECOY_ARM_KEY = "decoy_arm"

#: The adducts a swapped channel is searched through, per polarity, in the
#: order they are taken: the reference's wrong set first (sodium and ammonium
#: on a positive source, chloride and iodide on a negative one), then one more
#: of each kind so a profile that declares one of those still has a decoy. A
#: mode's channels take them one each, in order, skipping any the profile or
#: the mode declares and any the deployment holds no mechanism row for. The
#: labelled reagents sit a dalton from their unlabelled forms, so they are no
#: wrong adduct, and electron transfer or a proton is a channel most sources
#: make; neither is here.
DECOY_ADDUCTS: dict[str, tuple[str, ...]] = {
    "+": ("[M+Na]+", "[M+NH4]+", "[M+K]+"),
    "-": ("[M+Cl]-", "[M+I]-", "[M+Br]-"),
}

#: Why a decoy cannot run, as the refusal names it.
REFUSED_NO_CONTROL = "the sample has no completed run of this engine to be a decoy of"
REFUSED_NO_SCORING = (
    "its control recorded no scoring to inherit (a run with no untargeted "
    "stage, or one from before the record)"
)


class DecoyRefused(Exception):
    """A decoy that cannot bound its control, and why."""


@dataclass(frozen=True)
class DecoyPlan:
    """What a decoy run inherits from its control, and what it makes wrong.

    :param decoy: The arm, as the request named it.
    :param control_run_id: The run it is a decoy of.
    :param accuracy: Stage A's measurement as the control made it, which every
        stage of the decoy scores at instead of fitting its own.
    :param calibration: The mass gate's calibration as the control measured
        it, which the decoy's gate judges every commit against.
    :param envelope: The envelope tolerance the control scored a line's share
        at, which every fit score of the decoy reads instead of one fitted on
        its own commits (``envelope_fit.inherited_envelope_fit``).
    :param window_ppm: The window a ppm shift had to clear, None for the
        other arms.
    :param mechanisms: For the adduct arm, the mechanism rows each channel is
        searched through instead, in the order the channels were swapped;
        empty for a shift.
    :param swapped: For the adduct arm, each of the mode's channels and the
        adduct it was swapped for, None where none was left to take.
    """

    decoy: PeakAssignmentDecoy
    control_run_id: str
    accuracy: SampleMassAccuracy
    calibration: MassCalibration
    envelope: RunEnvelopeFit
    window_ppm: float | None = None
    mechanisms: tuple[SimpleNamespace, ...] = ()
    swapped: dict[str, str | None] = field(default_factory=dict)

    def record(self) -> dict:
        """What the decoy run records about it (:data:`DECOY_ARM_KEY`)."""
        return {
            "arm": self.decoy.arm,
            "control_run_id": self.control_run_id,
            # What every stage was scored at: always the control's, so a
            # reader comparing the arm with its control compares one
            # calibration with itself.
            "calibration": "control",
            # Where the channels' carriers and the reagent lines were looked
            # for: on the axis as stored, so the arm opens and claims what its
            # control did. A decoy recorded without it looked on the moved one.
            "source_ions": "stored_axis",
            "window_ppm": self.window_ppm,
            "swapped_channels": dict(self.swapped) if self.decoy.swap_adducts else None,
        }


def settings_differ(config: PeakAssignmentConfig, control_config: dict) -> list[str]:
    """The settings a decoy changes from its control's, by field name.

    A decoy bounds its control only when it is the same run with one thing
    wrong; a different profile, grid or tier band would make its rate a number
    about another run.

    :param config: The decoy's own configuration.
    :param control_config: The control's stored config.
    :raises DecoyRefused: The control's settings no longer validate, as a
        preset it named that the library has since dropped would not.
    :return: The fields whose values differ, empty when none do.
    """
    try:
        control = PeakAssignmentConfig.model_validate(
            {key: value for key, value in control_config.items() if key != "decoy"}
        ).settings()
    except ValidationError as invalid:
        raise DecoyRefused(
            f"its control's settings no longer validate ({invalid.error_count()} "
            "errors), so no run can share them"
        ) from invalid
    mine = config.settings()
    return sorted(key for key in mine if mine[key] != control[key])


def decoy_window_ppm(control_config: dict) -> float | None:
    """How far from a line the control still reached for a formula, in ppm.

    The wider of the window the untargeted stage searched candidates in and
    the window every predicted line was matched in, which is also the one
    Stage A gated its targets at: a shift inside either leaves the true
    formula in reach of one stage, so the arm would be no decoy.

    :param control_config: The control's stored config.
    :return: The window; None where the control recorded neither.
    """
    windows = [
        (control_config.get(RESOLVED_PROFILE_KEY) or {}).get("mz_precision_ppm"),
        (control_config.get(PATTERN_SCORING_KEY) or {}).get("mz_tolerance_ppm"),
    ]
    known = [float(window) for window in windows if window is not None]
    return max(known) if known else None


def fitted_sigma_ppm(scoring_sigma_ppm: float) -> float:
    """The width a sample's lines were fitted at, from the width it scored at.

    The inverse of ``mass_accuracy.scoring_sigma_ppm``, which widens a fitted
    width by :data:`~mascope_tools.composition.mass_accuracy.PRED_SIGMA_PPM` in
    quadrature: a run records the width it scored at, and the stages that
    score at the fit itself (Stage A's ions, the seeded re-score) need the
    fit back.

    :param scoring_sigma_ppm: The recorded scoring width.
    :return: The fitted width it was widened from.
    """
    return math.sqrt(max(float(scoring_sigma_ppm) ** 2 - PRED_SIGMA_PPM**2, 0.0))


def inherited_accuracy(scoring: dict) -> SampleMassAccuracy:
    """Stage A's measurement as a control's ``pattern_scoring`` recorded it.

    Rebuilt so that every number a stage reads off it is the control's: the
    offset it scored at and where that came from (the target library's fit,
    the reagent lines', or none), the width, and the anchors it counted.

    :param scoring: The control's ``pattern_scoring`` snapshot.
    :return: The measurement, as the decoy's stages take it.
    """
    mu_source = scoring.get("mu_source")
    mu_ppm = scoring.get("mu_ppm")
    reagent = None
    if mu_source == MU_SOURCE_REAGENT:
        # The control scored at the reagent lines' offset: it stood in beyond
        # the width, which is the one case a run takes it.
        reagent = ReagentOffset(
            mu_ppm=float(mu_ppm),
            lines=int(scoring.get("reagent_lines") or 0),
            beyond_width=True,
        )
    elif "reagent_lines" in scoring:
        # Recorded, and not scored at: inside the width, or too few to read.
        reagent_mu = scoring.get("reagent_mu_ppm")
        reagent = ReagentOffset(
            mu_ppm=None if reagent_mu is None else float(reagent_mu),
            lines=int(scoring.get("reagent_lines") or 0),
        )
    sigma = scoring.get("sigma_ppm")
    return SampleMassAccuracy(
        mu_ppm=float(mu_ppm) if mu_source == MU_SOURCE_FITTED else None,
        sigma_ppm=(
            fitted_sigma_ppm(sigma)
            if scoring.get("sigma_source") == "fitted" and sigma is not None
            else None
        ),
        anchors=int(scoring.get("fitted_anchors") or 0),
        reagent=reagent,
        anchor_ions=int(scoring.get("fitted_anchor_ions") or 0),
    )


def inherited_calibration(snapshot: dict) -> MassCalibration:
    """The mass gate's calibration as a control's ``mass_calibration`` recorded it.

    The centre, the widths and the line the centre followed, so a commit of
    the decoy sits where it would have sat in the control. A control whose gate
    measured nothing gives a calibration that measures nothing, and the decoy's
    gate stands down as the control's did.

    :param snapshot: The control's ``mass_calibration`` record.
    :return: The calibration, as the decoy's gate takes it.
    """

    def number(key: str, source: dict = snapshot) -> float | None:
        value = source.get(key)
        return None if value is None else float(value)

    trend_record = snapshot.get("trend")
    trend = None
    if isinstance(trend_record, dict):
        trend = MassTrend(
            intercept_ppm=float(trend_record["intercept_ppm"]),
            offset_mda=float(trend_record["offset_mda"]),
            sigma_ppm=float(trend_record["sigma_ppm"]),
            points=int(trend_record.get("kept") or 0),
            mz_lo=float(trend_record["mz_lo"]),
            mz_hi=float(trend_record["mz_hi"]),
        )
    return MassCalibration(
        mu_ppm=number("mu_ppm"),
        sigma_ppm=number("sigma_ppm"),
        anchors=int(snapshot.get("anchors") or 0),
        reagent_anchors=int(snapshot.get("reagent_anchors") or 0),
        gate_sigma_ppm=number("gate_sigma_ppm"),
        trend=trend,
        trend_refused=snapshot.get("trend_refused"),
        trend_rows=int((trend_record or {}).get("rows") or 0),
    )


def declared_channels(profile_name: str, mode_notations: Iterable[str]) -> set[str]:
    """Every channel a run of this chemistry could read a peak through.

    The profile's fingerprint, its secondary adducts and its secondary
    channels, and the mode's own: none of them is a wrong adduct, since the
    source makes it.

    :param profile_name: The reagent profile the control resolved to.
    :param mode_notations: The notations of the sample's mode.
    :return: Their comparison keys (``mechanism_key``).
    """
    profile = get_reagent_profile(profile_name)
    notations = [
        *profile.detection,
        *profile.secondary_adducts,
        *(channel.notation for channel in secondary_channels(profile_name)),
        *mode_notations,
    ]
    return {mechanism_key(notation) for notation in notations}


def wrong_adducts(polarity: str, declared: set[str]) -> list[str]:
    """The adducts a channel of this polarity may be swapped for, in order.

    :param polarity: The sample's polarity, ``"+"`` or ``"-"``.
    :param declared: What :func:`declared_channels` says the chemistry makes.
    :return: The notations of :data:`DECOY_ADDUCTS` it does not.
    """
    return [
        notation
        for notation in DECOY_ADDUCTS.get(polarity, ())
        if mechanism_key(notation) not in declared
    ]


def swap_channels(
    channels: Sequence[SimpleNamespace],
    candidates: Sequence[str],
    rows: Sequence[SimpleNamespace],
    fingerprint: Iterable[str] = (),
) -> tuple[tuple[SimpleNamespace, ...], dict[str, str | None]]:
    """Each of the mode's channels, and the wrong adduct it is searched through.

    One each, in order: a mode of two channels searches two wrong adducts, so
    the arm offers the search as many channels as the control had. A channel
    left without one is not searched at all, since searching it as it is would
    let the true chemistry through. The profile's fingerprint channels take
    theirs first, so a channel left without one is never the one most of the
    sample's ions are read through.

    :param channels: The mode's own mechanism rows, of the sample's polarity.
    :param candidates: :func:`wrong_adducts`, in the order they are taken.
    :param rows: The deployment's mechanism rows for those notations.
    :param fingerprint: The profile's ``detection`` notations.
    :return: The rows the arm searches, and each channel's swap by notation.
    """
    by_key = {mechanism_key(row.ionization_mechanism): row for row in rows}
    available = [
        by_key[mechanism_key(notation)]
        for notation in candidates
        if mechanism_key(notation) in by_key
    ]
    first = {mechanism_key(notation) for notation in fingerprint}
    ordered = sorted(
        channels,
        key=lambda row: (
            mechanism_key(row.ionization_mechanism) not in first,
            row.ionization_mechanism,
        ),
    )
    searched = tuple(available[: len(ordered)])
    swapped = {
        channel.ionization_mechanism: (
            searched[index].ionization_mechanism if index < len(searched) else None
        )
        for index, channel in enumerate(ordered)
    }
    return searched, swapped


def plan_decoy(
    decoy: PeakAssignmentDecoy,
    config: PeakAssignmentConfig,
    control_run_id: str | None,
    control_config: dict | None,
) -> DecoyPlan:
    """Check a decoy can bound its control, and take what it inherits.

    The adduct arm's mechanism rows are the service's to read; this plans a
    shift completely and the inheritance of every arm.

    :param decoy: The arm the request names.
    :param config: The decoy run's whole configuration.
    :param control_run_id: The sample's latest completed run of this engine
        that is no decoy, or None.
    :param control_config: That run's stored config.
    :raises DecoyRefused: The decoy would not bound its control, saying why.
    :return: The plan, its mechanisms still to be swapped for the adduct arm.
    """
    if control_run_id is None or control_config is None:
        raise DecoyRefused(REFUSED_NO_CONTROL)
    differ = settings_differ(config, control_config)
    if differ:
        raise DecoyRefused(
            f"a decoy runs at its control's settings, and {', '.join(differ)} "
            f"differ from run '{control_run_id}'"
        )
    scoring = control_config.get(PATTERN_SCORING_KEY)
    calibration = control_config.get(MASS_CALIBRATION_KEY)
    if not isinstance(scoring, dict) or not isinstance(calibration, dict):
        raise DecoyRefused(REFUSED_NO_SCORING)
    window = None
    if decoy.mz_shift_ppm is not None:
        window = decoy_window_ppm(control_config)
        if window is None:
            raise DecoyRefused(REFUSED_NO_SCORING)
        if abs(decoy.mz_shift_ppm) <= window:
            raise DecoyRefused(
                f"a shift of {decoy.mz_shift_ppm:+g} ppm is inside the "
                f"{window:g} ppm window its control searched and matched in, "
                "so the true formula stays in reach"
            )
    return DecoyPlan(
        decoy=decoy,
        control_run_id=control_run_id,
        accuracy=inherited_accuracy(scoring),
        calibration=inherited_calibration(calibration),
        envelope=inherited_envelope_fit(control_config.get(ENVELOPE_TOLERANCE_KEY)),
        window_ppm=window,
    )
