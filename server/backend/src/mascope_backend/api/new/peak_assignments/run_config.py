"""The configuration a run records on its row.

What a run was asked for and what it resolved to - the profile, the
reference licences it was allowed to match, the ionization setup - written as
the run's ``config``.
"""

from sqlalchemy import update

from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.decoy import DECOY_ARM_KEY
from mascope_backend.api.new.peak_assignments.engine import (
    BATCH_DECISIONS_KEY,
    CROSS_CHANNEL_KEY,
    ENVELOPE_TOLERANCE_KEY,
    FRAGMENTS_KEY,
    MEASUREMENT_MODEL_KEY,
    PARTNER_GATE_KEY,
    SEARCH_SCOPE_KEY,
    TIERING_KEY,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    RESOLVED_PROFILE_KEY,
    ResolvedProfile,
)
from mascope_backend.api.new.reference.licenses import reference_license_gate
from mascope_backend.db import PeakAssignmentRun, async_session


#: Key under which a run's stored config records the reference-licence gate
#: that was in force when it ran. Deliberately not a field on
#: :class:`PeakAssignmentConfig`: that model is the request body, so a field
#: there would let an API caller widen the gate its own run is matched under.
#: It is server state that a run *reports*, not configuration a client supplies.
REFERENCE_LICENSES_KEY = "reference_licenses"

#: Key under which a run's stored config records the ionization setup it
#: searched under: ``{"ionization_mode_id": ..., "version": ...}``, the
#: version's own id and its number, or None for a sample bound under no mode.
#: Server state like the licence gate, for the same reason: the mode is the
#: sample's, not something a request chooses. With it, two runs whose recorded
#: config is the same searched the same adduct panel; without it, an edit of
#: the mode in between could make them differ and nothing would say so.
IONIZATION_SETUP_KEY = "ionization_setup"


def _stored_run_config(
    config: PeakAssignmentConfig,
    resolved_profile: ResolvedProfile | None = None,
    search_scope: dict | None = None,
    measurement_model: dict | None = None,
    cross_channel: dict | None = None,
    tiering: dict | None = None,
    partner_gate: dict | None = None,
    fragments: dict | None = None,
    ionization_setup: dict | None = None,
    envelope_tolerance: dict | None = None,
    decoy_arm: dict | None = None,
    batch_decisions: dict | None = None,
) -> dict:
    """The blob persisted on a run: the requested config plus server-side state.

    A result should record what it was allowed to match, not only what it was
    asked to do - a run whose reference gate excluded half the mirror is not
    comparable with one that matched everything, and nothing else on the row
    would ever say so. The key is always present, ``None`` meaning ungated, so
    "everything was allowed" is distinguishable from a run written before this
    was recorded at all.

    The resolved profile is the same argument one step further: a run whose
    config says ``profile: "auto"`` records nothing about what auto meant unless
    the resolution is snapshotted beside it. It is absent until the run reaches
    the engine, because it is the sample's mechanisms that resolve it.

    The search scope is the third such fact, and the newest: with the untargeted
    stage's peak cap unset by default, how much of the spectrum it was offered is
    a property of the sample rather than of the request, and a blank ledger row
    means something different depending on whether that peak was searched.

    The scoring is the fourth, and the one a disagreement about a committed
    formula turns on: the width the untargeted stage judged a mass error
    against, and whether that width was fitted on this sample or fell back to
    the instrument class. A ppm is not a ppm without it.

    The mass calibration is the fifth, and the only one measured from the run's
    own answers rather than from its inputs: the offset and width its
    corroborated commits and the lines its reagent pre-pass claimed turned out
    to have, which is what every committed row's ``mass_z`` is stated in and
    what its gate demoted on. A z without the calibration behind it is a
    number with no units.

    The tiering is the sixth, and it is on the run for the same reason the tier
    BANDS are: a tier is only comparable across two runs together with the rules
    that produced it. It records the rule set's version, the thresholds it
    demoted on, and what each rule took.

    The partner gate is the seventh, beside the cross-channel pass that reads
    what it leaves: which opportunistic readings stood on a partner, which it
    turned to another reading, and what its contest took and at what margin.

    The fragment claim is the eighth, where the profile names a ladder: which
    parents it found committed, which peaks it read as their fragments, and
    which it left and why. A peak that reads as a reagent row on one run and a
    molecule on another is otherwise unexplained.

    The ionization setup is the ninth, and the first the run does not compute:
    the version of the sample's ionization mode it searched under. Its key is
    always present, None for a sample bound under no mode.

    The envelope tolerance is the tenth, beside the scoring it is the other
    half of: how well the run measured an isotope line's share, fitted on its
    own committed envelopes, in the form its instrument class takes and at the
    level it reached. A fit score is only comparable across two runs together
    with the tolerance it was read at, and a file beside this one pools the
    lines it records.
    The decoy arm is the eleventh, on a decoy run alone: the run it is a decoy of,
    the calibration it inherited from it and what it made wrong. The request
    names the arm; which run it bounds is the server's to say.

    The batch's decisions are the twelfth, on a run of a sample in a batch:
    which versions of the batch stages' records it read, and what they did to
    its rows. A run that read an older decision than its batch now holds is
    otherwise indistinguishable from one that read the current one.

    :param config: The validated client-supplied run configuration.
    :param resolved_profile: The chemistry the run resolved to, when known.
    :param search_scope: What the untargeted stage was offered, once it is known.
    :param measurement_model: The measurement model it scored and capped at,
        with how it matched a line and what its mass gate took, once the
        ledger is judged (``engine.measurement_model_record``).
    :param cross_channel: What the sample's own channels corroborated.
    :param tiering: The rule set that judged the commits, and what it took.
    :param partner_gate: What the partner gate decided, and at what margin.
    :param fragments: What the fragment claim took, where the profile names a
        ladder.
    :param ionization_setup: The setup the sample is bound under
        (:func:`~mascope_backend.api.new.ionization.modes.versions.sample_setup`).
    :param envelope_tolerance: What the run fitted of a line's share
        (:meth:`envelope_fit.RunEnvelopeFit.record`), once Stage A has run.
    :param decoy_arm: A decoy run's record of its control (``DecoyPlan.record``).
    :param batch_decisions: What it read of its sample's batch decisions
        (``batch_decisions.BatchDecisions.record``).
    :return: A JSON-serializable dict for ``PeakAssignmentRun.config``.
    """
    stored = config.model_dump()
    stored[REFERENCE_LICENSES_KEY] = reference_license_gate()
    stored[IONIZATION_SETUP_KEY] = ionization_setup
    if resolved_profile is not None:
        stored[RESOLVED_PROFILE_KEY] = resolved_profile.snapshot()
    if search_scope is not None:
        stored[SEARCH_SCOPE_KEY] = search_scope
    if measurement_model is not None:
        stored[MEASUREMENT_MODEL_KEY] = measurement_model
    if cross_channel is not None:
        stored[CROSS_CHANNEL_KEY] = cross_channel
    if tiering is not None:
        stored[TIERING_KEY] = tiering
    if partner_gate is not None:
        stored[PARTNER_GATE_KEY] = partner_gate
    if fragments is not None:
        stored[FRAGMENTS_KEY] = fragments
    if envelope_tolerance is not None:
        stored[ENVELOPE_TOLERANCE_KEY] = envelope_tolerance
    if decoy_arm is not None:
        stored[DECOY_ARM_KEY] = decoy_arm
    if batch_decisions is not None:
        stored[BATCH_DECISIONS_KEY] = batch_decisions
    return stored


async def _record_resolved_profile(
    peak_assignment_run_id: str,
    config: PeakAssignmentConfig,
    resolved_profile: ResolvedProfile,
    search_scope: dict | None = None,
    measurement_model: dict | None = None,
    cross_channel: dict | None = None,
    tiering: dict | None = None,
    partner_gate: dict | None = None,
    fragments: dict | None = None,
    ionization_setup: dict | None = None,
    envelope_tolerance: dict | None = None,
    decoy_arm: dict | None = None,
    batch_decisions: dict | None = None,
) -> None:
    """Write the resolved chemistry onto a run that is about to use it.

    A separate write because the run row exists before its sample's mechanisms
    have been read - a request answers with a run id, and an adopted run was
    created by that request - so the snapshot cannot be part of the insert.

    Called twice: once with the chemistry alone, before the stages, so a run that
    fails still says what it would have searched; and once more once the ledger
    is built, with the search scope that only the remainder after Stage A can
    decide and the mass calibration that only the finished commits can measure.

    :param peak_assignment_run_id: The run to stamp.
    :param config: The run's configuration, re-serialized with the snapshot.
    :param resolved_profile: The chemistry the run resolved to.
    :param search_scope: What the untargeted stage was offered, once known.
    :param measurement_model: The model it scored and capped at, once known.
    :param cross_channel: What the sample's own channels corroborated.
    :param tiering: The rule set that judged the commits, and what it took.
    :param partner_gate: What the partner gate decided, and at what margin.
    :param fragments: What the fragment claim took, where the profile names a
        ladder.
    :param ionization_setup: The setup the sample is bound under, kept as the
        run was created with it.
    :param envelope_tolerance: What the run fitted of a line's share.
    :param decoy_arm: A decoy run's record of its control.
    :param batch_decisions: What it read of its sample's batch decisions.
    """
    async with async_session() as session:
        await session.execute(
            update(PeakAssignmentRun)
            .where(PeakAssignmentRun.peak_assignment_run_id == peak_assignment_run_id)
            .values(
                config=_stored_run_config(
                    config,
                    resolved_profile,
                    search_scope,
                    measurement_model,
                    cross_channel,
                    tiering,
                    partner_gate,
                    fragments,
                    ionization_setup,
                    envelope_tolerance,
                    decoy_arm,
                    batch_decisions,
                )
            )
        )
        await session.commit()
