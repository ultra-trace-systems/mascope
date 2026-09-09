"""The run's own mass calibration, and what a row's distance from it costs.

A sample's committed rows do not scatter around zero ppm; they scatter around
whatever offset that acquisition actually sat at, with whatever spread that
instrument achieved that day. Both are measurable from the run's own output -
the rows it has more than a mass fit for - and once measured, every committed
row has a position in that distribution. ``mass_z`` is that position, and this
is where it is put on the row.

The gate on top of it is deliberately narrow. A row the run corroborated is
never demoted: an isotope envelope that was confirmed, or a curated identity
that was matched, is evidence the mass error does not overrule. An
UNCORROBORATED row - one that rests on the mass fit alone - beyond
:data:`OFF_CALIBRATION_Z` is capped at ``candidate``, and beyond
:data:`BELOW_ASSIGNABILITY_Z` at ``below_assignability``. The formula stays on
the row either way: what the run is withdrawing is its confidence, not its
reading.

What this is worth, measured rather than assumed: on the 43-sample gate it caps
34 rows of about 14,600 at the top tier and moves G1 by at most 1.7 points on
one set. That is not the lever it was designed to be, because the failure it was
designed to catch has already been closed upstream - the finder ranks candidates
on the v2 fit at the sample's own width (step 2.1), so a formula three sigma out
does not win its peak in the first place. The widest mass error on any committed
top-tier Orbitrap row is now 0.92 ppm. The gate stays because a guard that
nothing trips is what a guard looks like when the thing it guards against is
absent, and because the ``mass_z`` it records is what step 2.4's mechanical
tiers read; it is not what will move the agreement metrics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
    SampleMassAccuracy,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_RANK,
)
from mascope_tools.composition.mass_accuracy import fit_mass_accuracy


#: Beyond this many fitted sigma from the run's own centre, an uncorroborated
#: commit is capped at ``candidate``. Three because that is the distance at
#: which a correct assignment stops being a plausible member of the
#: distribution the run's confirmed rows describe.
OFF_CALIBRATION_Z = 3.0

#: And beyond this, ``below_assignability``: at six sigma the row is not a tail
#: of the run's own accuracy under any reading, so what is left is a formula
#: nobody should act on rather than one to look at again.
BELOW_ASSIGNABILITY_Z = 6.0

#: The reason a capped row carries, which is the vocabulary step 2.4's
#: ``tier_reasons`` will collect.
REASON_OFF_CALIBRATION = "off_calibration"

#: A curated identity: the row won its peak against the known composition set,
#: so a library entry - not this run's own search - proposed the formula.
CORROBORATED_CURATED = "curated"

#: A confirmed envelope: the ion committed a monoisotopic peak AND at least one
#: isotopologue of the same reading, so the spectrum agrees with the formula in
#: more than one place.
CORROBORATED_ISOTOPOLOGUE = "isotopologue"


@dataclass(frozen=True)
class MassCalibration:
    """What a run measured of its own mass accuracy, from its own commits.

    Distinct from :class:`engine.SampleMassAccuracy`, which is what STAGE A
    measured before the untargeted stage ran and is what that stage was scored
    at. This is measured afterwards, over everything the run went on to commit
    and corroborate, and it is what a committed row's distance is expressed in.
    The two answer different questions and a run records both: on a sample whose
    curated library holds two targets the first has nothing to say and the
    second has thousands of rows.
    """

    mu_ppm: float | None = None
    sigma_ppm: float | None = None
    anchors: int = 0

    @property
    def measured(self) -> bool:
        """Whether a row's distance from this can be stated at all.

        Both numbers are needed and neither substitutes: without a width there
        is no scale to divide by, and without an offset the distance would be
        measured from a centre the run never established - which on a sample
        sitting 1.2 ppm out condemns every row it has.
        """
        return self.mu_ppm is not None and self.sigma_ppm is not None

    def z_of(self, mz_error_ppm: float | None) -> float | None:
        """Where a mass error sits in this run's own distribution, in sigma."""
        if not self.measured or mz_error_ppm is None:
            return None
        error = float(mz_error_ppm)
        if not np.isfinite(error):
            return None
        return (error - float(self.mu_ppm)) / float(self.sigma_ppm)

    def snapshot(self) -> dict:
        """What the run records about the calibration it judged its rows at."""
        return {
            "mu_ppm": None if self.mu_ppm is None else round(float(self.mu_ppm), 4),
            "sigma_ppm": (
                None if self.sigma_ppm is None else round(float(self.sigma_ppm), 4)
            ),
            "anchors": int(self.anchors),
            # As on the scoring snapshot: "none" means the run measured nothing,
            # which is a different statement from measuring zero.
            "mu_source": "fitted" if self.mu_ppm is not None else "none",
            "sigma_source": "fitted" if self.sigma_ppm is not None else "none",
        }


def is_committed(row: dict) -> bool:
    """Whether a ledger row commits a formula to a peak.

    The pre-passes are not commits and are excluded here rather than filtered by
    their source: a reagent hit and a ringing artifact both name no formula, so
    neither has a mass error that means "this composition is this many ppm out"
    - the reagent row's error is the distance to a cluster ion the run
    deliberately did not assign, and the artifact row has none at all.
    """
    return bool(row.get("assigned_formula")) and row.get("role") in (
        ROLE_M0,
        ROLE_ISO_CHILD,
    )


def corroboration_of(assignments: list[dict]) -> dict[str, str | None]:
    """Per committed row: what this run has for it beyond the mass fit.

    Three answers, and the difference between the first two and the third is
    what the gate acts on:

    - :data:`CORROBORATED_CURATED` - Stage A matched it to the known
      composition set, so the formula was proposed by a library rather than by
      this run's own search over the mass.
    - :data:`CORROBORATED_ISOTOPOLOGUE` - the reading committed a monoisotopic
      peak and at least one isotopologue of the same ion, so the spectrum
      agrees in a second place. Both rows are corroborated by the pair: the
      child by having an owner, the M0 by being one.
    - ``None`` - the row rests on the mass fit alone.

    :param assignments: Every row built for this sample, in any order.
    :return: Corroboration keyed by ``peak_assignment_id``, committed rows only.
    """
    owners = {
        str(row["owner_peak_assignment_id"])
        for row in assignments
        if row.get("owner_peak_assignment_id")
    }
    corroboration: dict[str, str | None] = {}
    for row in assignments:
        if not is_committed(row):
            continue
        row_id = str(row["peak_assignment_id"])
        if row.get("source") == SOURCE_DATABASE:
            corroboration[row_id] = CORROBORATED_CURATED
        elif row.get("owner_peak_assignment_id") or row_id in owners:
            corroboration[row_id] = CORROBORATED_ISOTOPOLOGUE
        else:
            corroboration[row_id] = None
    return corroboration


def fit_run_mass_accuracy(
    assignments: list[dict],
    corroboration: dict[str, str | None],
) -> MassCalibration:
    """Fit the run's mass accuracy over the rows it corroborated.

    The same robust fit the rest of the engine measures a sample with
    (``fit_mass_accuracy``: median and scaled MAD), over a different and much
    larger set of anchors. Stage A's fit is over the curated library's matched
    isotopologues, which on a sample whose library holds two targets is nothing;
    this is over everything the run committed and had a second reason for, which
    on the same sample is hundreds of rows. That is what makes the calibration
    the run's own rather than the library's.

    Only corroborated rows anchor it, and that is the point rather than a
    limitation: fitting over every commit would measure the spread of the rows
    being judged, so the distribution would widen to accommodate whatever sits
    in its tail and the gate would be unable to find anything by construction.

    :param assignments: Every row built for this sample.
    :param corroboration: What :func:`corroboration_of` answered for them.
    :return: The offset and width, each None where too few anchors were found.
    """
    errors = [
        row["mz_error_ppm"]
        for row in assignments
        if corroboration.get(str(row.get("peak_assignment_id"))) is not None
        and row.get("mz_error_ppm") is not None
    ]
    mu, sigma = fit_mass_accuracy(errors)
    return MassCalibration(mu_ppm=mu, sigma_ppm=sigma, anchors=len(errors))


def apply_mass_gate(
    assignments: list[dict],
    *,
    stage_a_accuracy: SampleMassAccuracy | None = None,
) -> dict:
    """Record every commit's ``mass_z`` and cap the uncorroborated outliers.

    Modifies the rows in place, and runs after both stages and both pre-passes
    have built them: the corroboration it reads is a property of the whole
    ledger (which M0 kept an isotopologue, which peak Stage A claimed), so no
    stage can answer it alone.

    Nothing is capped when the run measured no calibration - a sample with too
    few corroborated commits to fit an offset and a width has not earned the
    right to demote anything, and standing down is recorded rather than being
    indistinguishable from a run that found nothing to demote.

    :param assignments: Every row built for this sample, modified in place.
    :param stage_a_accuracy: What Stage A measured, for the run's record. It is
        reported beside this fit rather than folded into it: the two are
        measured over different rows and a reader comparing a run's scoring to
        its gating needs to see both.
    :return: A JSON-serializable summary for the run's config.
    """
    corroboration = corroboration_of(assignments)
    calibration = fit_run_mass_accuracy(assignments, corroboration)
    summary = {
        **calibration.snapshot(),
        "cap_z": OFF_CALIBRATION_Z,
        "floor_z": BELOW_ASSIGNABILITY_Z,
        "applied": calibration.measured,
        "committed": len(corroboration),
        "corroborated": sum(1 for value in corroboration.values() if value),
        "capped": 0,
        "below_assignability": 0,
    }
    if stage_a_accuracy is not None:
        # What Stage A had to score the untargeted search with, beside what the
        # run went on to be able to measure for itself. On the gate sets these
        # differ by an order of magnitude in anchor count.
        summary["stage_a_anchors"] = int(stage_a_accuracy.anchors)

    for row in assignments:
        row_id = str(row.get("peak_assignment_id"))
        if row_id not in corroboration:
            continue
        corroborated = corroboration[row_id]
        gate: dict = {"corroborated_by": corroborated}
        z = calibration.z_of(row.get("mz_error_ppm"))
        provenance = row.setdefault("provenance", {})
        if z is not None:
            provenance["mass_z"] = round(z, 2)
            capped = _cap_for(abs(z)) if corroborated is None else None
            # Only ever downwards. A row the bands already put below the cap is
            # not lifted onto it, and the gate's word for such a row is silence:
            # it did not decide that tier and must not appear to have.
            if capped is not None and TIER_RANK[row["tier"]] > TIER_RANK[capped]:
                row["tier"] = capped
                gate["capped"] = capped
                gate["reason"] = REASON_OFF_CALIBRATION
                summary["capped"] += 1
                if capped == TIER_BELOW_ASSIGNABILITY:
                    summary["below_assignability"] += 1
        provenance["mass_gate"] = gate
    return summary


def _cap_for(abs_z: float) -> str | None:
    """The strongest tier a row this far off the run's centre may hold."""
    if abs_z > BELOW_ASSIGNABILITY_Z:
        return TIER_BELOW_ASSIGNABILITY
    if abs_z > OFF_CALIBRATION_Z:
        return TIER_CANDIDATE
    return None
