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
49 rows of about 14,600 at the top tier, demotes none that the reference
confirms, and moves G1 by at most 1.7 points on one set. That is not the lever it was designed to be, because the failure it was
designed to catch has already been closed upstream - the finder ranks candidates
on the v2 fit at the sample's own width (step 2.1), so a formula three sigma out
does not win its peak in the first place. The widest mass error on any committed
top-tier Orbitrap row is now 0.92 ppm. The gate stays because a guard that
nothing trips is what a guard looks like when the thing it guards against is
absent, and because the ``mass_z`` it records is what step 2.4's mechanical
tiers read; it is not what will move the agreement metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

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
from mascope_tools.composition.mass_accuracy import (
    fit_mass_accuracy,
    scoring_sigma_ppm,
)


#: Beyond this many fitted sigma from the run's own centre, an uncorroborated
#: commit is capped at ``candidate``. Three because that is the distance at
#: which a correct assignment stops being a plausible member of the
#: distribution the run's confirmed rows describe.
OFF_CALIBRATION_Z = 3.0

#: And beyond this, ``below_assignability``: at six sigma the row is not a tail
#: of the run's own accuracy under any reading, so what is left is a formula
#: nobody should act on rather than one to look at again.
BELOW_ASSIGNABILITY_Z = 6.0

#: How many of the instrument class's precision a satellite's mass error may
#: sit from its parent's and still be read as the same ion. Three, because the
#: quantity being tested is a DIFFERENCE of two measurements and this is a three
#: sigma test on it: the difference is about as wide as the class's precision on
#: the sets that have real envelopes (0.28 to 0.45 ppm against a 0.3 ppm class),
#: so testing at the bare precision is a two-thirds-of-one-sigma test and throws
#: away a third to a half of the genuine children. Measured on the gate, the
#: share of children kept at one, two and three times the precision: A 59, 84,
#: 91%; B 66, 88, 95%; C 67, 78, 81%; C2 66, 82, 90%; D 50, 74, 84%. What the
#: anchors then fit does not move at any of those multiples (A +0.02 ppm at 0.12
#: wide throughout, D -0.17 at 0.35 to 0.40), so this number does not decide the
#: calibration - it decides which rows a run calls corroborated, which is the
#: flag step 2.4 reads.
#:
#: On a TOF no multiple separates the two populations, because a coincidental
#: pairing is spread evenly across the matching window rather than clustered:
#: the same three sets keep 37-46% at one and 77-82% at three. There the test is
#: a purity choice rather than a separation, which is why 2.4 weighs isotope
#: corroboration by instrument class rather than counting it.
TRACKING_SIGMAS = 3.0

#: The reason a capped row carries, which is the vocabulary step 2.4's
#: ``tier_reasons`` will collect.
REASON_OFF_CALIBRATION = "off_calibration"

#: A curated identity: the row won its peak against the known composition set,
#: so a library entry - not this run's own search - proposed the formula.
CORROBORATED_CURATED = "curated"

#: A confirmed envelope: the ion committed a monoisotopic peak AND at least one
#: isotopologue of the same reading whose own mass error TRACKS its parent's, so
#: the spectrum agrees with the formula in more than one place.
#:
#: The tracking test is what makes this mean anything on a crowded spectrum. A
#: satellite is paired within the instrument class's matching window - 15 ppm on
#: a TOF - so on a dense TOF spectrum a peak that is nobody's isotopologue lands
#: inside that window by coincidence, and counting it as agreement lets the
#: coincidence corroborate the reading it was matched to. Measured on the gate:
#: the child-minus-parent error is 0.28 to 0.45 ppm wide on the Orbitrap sets
#: with 98-99% of children inside 3 ppm of their parent, and 4.6 to 6.5 ppm wide
#: on the three TOF sets with 37-46% inside it. Two lines of one ion differ only
#: by what centroiding does to each, so the class's own precision is the bar.
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
    #: The width a row's distance is actually judged in, which is this fit's
    #: own width or the width the SEARCH scored at, whichever is wider. They
    #: differ because the two describe different populations: the anchors are
    #: the run's best-corroborated rows and are intrinsically its best measured
    #: ones (0.06-0.17 ppm on the sparse Orbitrap set), while the rows the gate
    #: judges rest on the mass fit alone and spread wider (0.36 ppm on the same
    #: set). Judging the second population by the first condemns its tails by
    #: construction - measured, it demoted 105 rows the reference confirms on
    #: that set alone. A row cannot be off calibration for a distance its own
    #: search was told to accept, so the search's width is the floor.
    gate_sigma_ppm: float | None = None

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
        """Where a mass error sits in this run's own distribution, in sigma.

        In :attr:`gate_sigma_ppm`, not in the fitted width, so that one number
        on the row means one thing: a row reads beyond 3 exactly when the gate
        would cap it for being there.
        """
        if not self.measured or mz_error_ppm is None:
            return None
        error = float(mz_error_ppm)
        if not np.isfinite(error):
            return None
        width = self.gate_sigma_ppm or self.sigma_ppm
        return (error - float(self.mu_ppm)) / float(width)

    def snapshot(self) -> dict:
        """What the run records about the calibration it judged its rows at."""
        return {
            "mu_ppm": None if self.mu_ppm is None else round(float(self.mu_ppm), 4),
            "sigma_ppm": (
                None if self.sigma_ppm is None else round(float(self.sigma_ppm), 4)
            ),
            "anchors": int(self.anchors),
            # What the fit measured, and what a row was judged in. Recorded
            # apart because a reader comparing two runs needs to know whether a
            # row escaped the cap on its own accuracy or on the search's.
            "gate_sigma_ppm": (
                None
                if self.gate_sigma_ppm is None
                else round(float(self.gate_sigma_ppm), 4)
            ),
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


def tracking_tolerance_ppm(precision_ppm: float) -> float:
    """How far a satellite's mass error may sit from its parent's.

    :param precision_ppm: The instrument class's precision.
    :return: The bar, :data:`TRACKING_SIGMAS` of it.
    """
    return TRACKING_SIGMAS * float(precision_ppm)


def tracks_its_parent(
    child_error_ppm: float | None,
    parent_error_ppm: float | None,
    tolerance_ppm: float,
) -> bool:
    """Whether an isotopologue's mass error is its parent's, within precision.

    Two lines of one ion are one measurement of one axis: their mass errors
    differ only by what centroiding does to each peak, so a child whose error
    sits further from its parent's than the instrument class can explain is not
    that ion's isotopologue - it is another peak the matching window happened to
    reach. A row missing either error cannot be shown to track and does not.

    :param child_error_ppm: The satellite's own mass error.
    :param parent_error_ppm: Its owner's.
    :param tolerance_ppm: The bar, from :func:`tracking_tolerance_ppm`.
    :return: Whether the pair may be read as one envelope.
    """
    if child_error_ppm is None or parent_error_ppm is None:
        return False
    child, parent = float(child_error_ppm), float(parent_error_ppm)
    if not (np.isfinite(child) and np.isfinite(parent)):
        return False
    return abs(child - parent) <= float(tolerance_ppm)


def corroboration_of(
    assignments: list[dict],
    *,
    precision_ppm: float,
) -> dict[str, str | None]:
    """Per committed row: what this run has for it beyond the mass fit.

    Three answers, and the difference between the first two and the third is
    what the gate acts on:

    - :data:`CORROBORATED_CURATED` - Stage A matched it to the known
      composition set, so the formula was proposed by a library rather than by
      this run's own search over the mass.
    - :data:`CORROBORATED_ISOTOPOLOGUE` - the reading committed a monoisotopic
      peak and at least one isotopologue of the same ion WHOSE MASS ERROR
      TRACKS ITS PARENT'S, so the spectrum agrees in a second place rather than
      in a place the matching window happened to reach. Both rows of such a
      pair are corroborated by it; a child that does not track corroborates
      nothing, including itself.
    - ``None`` - the row rests on the mass fit alone.

    :param assignments: Every row built for this sample, in any order.
    :param precision_ppm: The instrument class's precision. The bar a child's
        error must meet against its parent's is :data:`TRACKING_SIGMAS` of it,
        which is a three sigma test on their difference.
    :return: Corroboration keyed by ``peak_assignment_id``, committed rows only.
    """
    tolerance = tracking_tolerance_ppm(precision_ppm)
    error_by_id = {
        str(row["peak_assignment_id"]): row.get("mz_error_ppm")
        for row in assignments
        if row.get("peak_assignment_id")
    }
    # The children that are their parents' isotopologues, and the parents they
    # confirm. Resolved first, because a row's own corroboration depends on the
    # whole envelope rather than on the row.
    tracking_children: set[str] = set()
    confirmed_owners: set[str] = set()
    for row in assignments:
        owner_id = row.get("owner_peak_assignment_id")
        if not is_committed(row) or not owner_id:
            continue
        if tracks_its_parent(
            row.get("mz_error_ppm"), error_by_id.get(str(owner_id)), tolerance
        ):
            tracking_children.add(str(row["peak_assignment_id"]))
            confirmed_owners.add(str(owner_id))

    corroboration: dict[str, str | None] = {}
    for row in assignments:
        if not is_committed(row):
            continue
        row_id = str(row["peak_assignment_id"])
        if row.get("source") == SOURCE_DATABASE:
            corroboration[row_id] = CORROBORATED_CURATED
        elif row_id in tracking_children or row_id in confirmed_owners:
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

    Monoisotopic rows only. A satellite is the same ion measured on a weaker
    peak, so it is the wider row wherever it is real - 0.35 ppm against 0.12 for
    the M0 rows it belongs to on the sparse Orbitrap set - and where it is not
    real it is a coincidence of the matching window. Letting satellites anchor
    made the recorded calibration theirs on every TOF set: on the bromide TOF
    set they and the M0 rows they "confirmed" were two thirds of the anchors and
    put the run at +0.4 to +1.6 ppm and 4.4 to 4.6 ppm wide, while the run's own
    uncorroborated M0 rows sat at -0.1 to +0.1 and 2.7 to 2.9 wide.

    :param assignments: Every row built for this sample.
    :param corroboration: What :func:`corroboration_of` answered for them.
    :return: The offset and width, each None where too few anchors were found.
    """
    errors = [
        row["mz_error_ppm"]
        for row in assignments
        if row.get("role") == ROLE_M0
        and corroboration.get(str(row.get("peak_assignment_id"))) is not None
        and row.get("mz_error_ppm") is not None
    ]
    mu, sigma = fit_mass_accuracy(errors)
    return MassCalibration(mu_ppm=mu, sigma_ppm=sigma, anchors=len(errors))


def apply_mass_gate(
    assignments: list[dict],
    *,
    stage_a_accuracy: SampleMassAccuracy | None = None,
    fallback_sigma_ppm: float,
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
        its gating needs to see both. It is also half of the width the search
        scored at, which is the floor on the width this gate judges in.
    :param fallback_sigma_ppm: The instrument class's width, which is both the
        other half of that floor and the precision a satellite has to track its
        parent within (``profiles.resolve_fallback_sigma_ppm``).
    :return: A JSON-serializable summary for the run's config.
    """
    # The width the untargeted search actually scored a mass error at, rebuilt
    # from the same two numbers `pattern_scoring_for` builds it from, so the
    # floor below is the search's own width rather than an approximation of it.
    search_sigma = scoring_sigma_ppm(
        None if stage_a_accuracy is None else stage_a_accuracy.sigma_ppm,
        float(fallback_sigma_ppm),
    )
    corroboration = corroboration_of(assignments, precision_ppm=fallback_sigma_ppm)
    calibration = fit_run_mass_accuracy(assignments, corroboration)
    if calibration.sigma_ppm is not None:
        calibration = replace(
            calibration,
            gate_sigma_ppm=max(float(calibration.sigma_ppm), float(search_sigma)),
        )
    summary = {
        **calibration.snapshot(),
        "cap_z": OFF_CALIBRATION_Z,
        "floor_z": BELOW_ASSIGNABILITY_Z,
        # What the search judged a mass error at, recorded so the gate width
        # above can be read as the max of the two it is.
        "search_sigma_ppm": round(float(search_sigma), 4),
        "precision_ppm": float(fallback_sigma_ppm),
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
