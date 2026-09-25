"""
Pure transformation logic for peak-centric assignment.

Inverts target-anchored match results (one row per target isotope) into
peak-anchored assignments (one row per observed peak), applies the
single-owner-per-peak arbitration, and maps untargeted composition-finder
results onto the same row shape.

No database access happens here - everything is DataFrame/dict manipulation
so the arbitration logic stays unit-testable. The service layer owns
persistence.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from mascope_backend.api.controllers.match.lib.match_score_v2 import (
    ion_score_v2,
    sample_noise_floor,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_RANK,
    TIER_UNASSIGNED,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.arbitration import (
    CANDIDATE_DENSITY,
    arbitrate_candidates,
    density_of,
)
from mascope_tools.composition.calibration import (
    Calibration,
    apply_calibration,
    apply_corroboration,
    calibration_for,
    corroboration_by_mechanism,
)
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.finder import (
    HELD_BY_LIBRARY,
    HELD_BY_LINES,
    KNOWN_DISPLACED,
    KNOWN_KEPT,
    KNOWN_RIVALS,
    ListReading,
    ReadingRivals,
)
from mascope_tools.composition.heuristic_filter import (
    PATTERN_BASE_SNR,
    SAME_ION_ALTERNATIVES,
    SCORE_VERSION,
    element_counts,
    formula_plausibility,
    neutral_is_closed_shell,
    propose_same_ion_readings,
)
from mascope_tools.composition.mass_accuracy import (
    fit_sample_mass_accuracy,
    mass_accuracy_anchors,
    scoring_sigma_ppm,
)
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
    PatternScoring,
)
from mascope_tools.composition.utils import (
    calculate_mass,
    parse_composition,
    parse_formula_tokens,
    to_hill_order,
)


# Sentinel so a caller can pass calibration=None (explicitly uncalibrated) distinctly from
# "not provided" (fall back to the in-code registry via calibration_for).
_CALIBRATION_UNSET = object()


# Peak roles within an assignment run
ROLE_M0 = "M0"
ROLE_ISO_CHILD = "iso_child"
ROLE_UNASSIGNED = "unassigned"
# A peak the source made rather than the sample: a reagent cluster ion or one of
# its isotopologues, claimed by the pre-pass before either stage runs. The role
# is what takes such a peak out of the analyte ledger - it carries no
# `assigned_formula`, so it votes on nothing and is counted as explained by its
# role rather than by a formula it has no business claiming.
ROLE_REAGENT = "reagent"
# An instrument artifact rather than an ion: an FT sidelobe, the ringing a very
# intense centroid leaves around itself. Like a reagent row it carries no
# formula and votes on nothing; unlike one it names no ion either, because there
# is none - the peak is the detector's answer to a neighbour, not a species.
ROLE_ARTIFACT = "artifact"

# Which stage won the peak
SOURCE_DATABASE = "database"
SOURCE_UNTARGETED = "untargeted"
# ...or the reagent pre-pass, which is neither: it does not assign a formula to a
# peak, it declares the peak to be the source's own chemistry.
SOURCE_REAGENT = "reagent"
# ...or the artifact pre-pass, which declares it to be the instrument's.
SOURCE_ARTIFACT = "artifact"
# ...or, when no stage did, the person who decided it instead. A manually
# curated row is not the output of a stage, and saying 'database' or
# 'untargeted' on it would credit an engine with a choice a human made.
SOURCE_MANUAL = "manual"

# Carried DataFrame column: reference-database identities (a list of dicts) for
# isotope rows that came from the reference mirror rather than the curated target
# library. Present only on reference rows; the inversion drops it into provenance
# and nulls the (synthetic) target FK ids so reference winners persist without a
# dangling target_compound_id / target_ion_id.
REFERENCE_IDENTITIES_COL = "reference_identities"

# Placeholder used by the untargeted finder for unassigned peaks
UNTARGETED_NO_MATCH = "---"
# The finder emits "()" for ionization/reagent peaks (an adduct with no
# molecular core). That is not a molecular formula, so it must not be persisted
# as one. A reagent peak that reaches the finder at all has escaped the pre-pass
# (`reagent_pass`), whose library claims these before either stage runs; this
# stays as the backstop for a source whose profile has no library.
UNTARGETED_IONIZATION = "()"

#: Key under which a run records what the untargeted stage judged a mass error
#: against: the width, the offset, whether each was fitted on this sample or
#: taken from a stand-in, and how many known ions they were fitted from. On the
#: run's config beside the resolved profile, because it is the
#: difference between a candidate a whole ppm off being refused and being
#: elected, and nothing else on a row would ever say which happened.
PATTERN_SCORING_KEY = "pattern_scoring"

#: Key under which a run records the mass calibration it fitted from its own
#: corroborated commits, and what that gating cost: the offset and width every
#: committed row's ``mass_z`` is measured in, how many rows anchored them, and
#: how many uncorroborated rows the distance capped. Separate from
#: :data:`PATTERN_SCORING_KEY` because the two are measured over different rows
#: at different points - that one is what Stage A had to score the search with,
#: this one is what the finished ledger turned out to be able to measure.
MASS_CALIBRATION_KEY = "mass_calibration"

#: Key under which a run records what its own channels corroborated: the
#: mechanisms it searched, which of them could be donating a reported nitrogen,
#: how many committed neutrals a second channel confirmed, and what the reagent-N
#: rule capped. Separate from :data:`MASS_CALIBRATION_KEY` because the evidence
#: is of a different kind - that one is where a row sits on the mass axis, this
#: one is how many independent chemistries saw the same neutral.
CROSS_CHANNEL_KEY = "cross_channel"

#: Key under which a run records the rule set that judged its commits: the
#: version, the thresholds it demoted on, and what each rule took. On the run
#: for the same reason the tier BANDS are - a tier is only comparable across
#: two runs together with the rules that produced it, and these rules will
#: change while old ledgers stay readable.
TIERING_KEY = "tiering"

#: Key under which a run records how much of its spectrum the untargeted stage
#: was actually offered. On the run's config beside the resolved profile, and for
#: the same reason: "searched 300 of 2,577 peaks" and "searched all 2,577" are
#: different results, and nothing else on the row would ever say which happened.
SEARCH_SCOPE_KEY = "search_scope"


def untargeted_targets(
    eligible: pd.DataFrame,
    max_untargeted_peaks: int | None,
    ceiling: int,
) -> tuple[pd.DataFrame, dict]:
    """The peaks the untargeted stage will enumerate, and what it left behind.

    Every unexplained peak is eligible; the cap decides how many of them are
    searched, brightest first. Unset, that is all of them up to the ceiling -
    which exists so "all" cannot mean unbounded work, not because some number of
    peaks is the right number to look at.

    :param eligible: The unassigned peaks above the run's intensity threshold,
        with an ``intensity`` column.
    :param max_untargeted_peaks: The run's cap, or None for every peak.
    :param ceiling: The hard bound a cap may not exceed, and the effective cap
        when the run names none.
    :return: The peaks to search, and a scope dict for the run to record.
    """
    limit = (
        ceiling if max_untargeted_peaks is None else min(max_untargeted_peaks, ceiling)
    )
    targets = eligible.nlargest(limit, "intensity")
    scope = {
        "eligible_peaks": int(len(eligible)),
        "searched_peaks": int(len(targets)),
        "requested_limit": max_untargeted_peaks,
        "ceiling": int(ceiling),
        # True only when peaks were left unsearched, which is the question a
        # reader of the run has: not "was there a bound" but "did it bite".
        "limited": bool(len(targets) < len(eligible)),
        "at_ceiling": bool(max_untargeted_peaks is None and len(eligible) > ceiling),
    }
    return targets, scope


#: Where the offset a sample is scored at came from, as its run records it:
#: the target library's matched lines, the lines the reagent pre-pass claimed,
#: or nothing, which is scored as no offset.
MU_SOURCE_FITTED = "fitted"
MU_SOURCE_REAGENT = "reagent"
MU_SOURCE_NONE = "none"

#: Claimed reagent lines below which their median is not read as an offset.
#: Below three it is not a median: one line is its own error and two are their
#: mean, so a single line off the axis sets the answer. The lines most likely
#: to be off it are a source's brightest, which an Orbitrap moves: on the gate's
#: labelled-nitrate set the core ion and its first rung sit at +1.26 and -0.05
#: ppm, while their five isotopologue lines sit at -0.7 to -1.9 and the run's
#: own commits at -1.1.
REAGENT_OFFSET_MIN_LINES = 3


@dataclass(frozen=True)
class ReagentOffset:
    """Where the lines the reagent pre-pass claimed put a sample's mass axis.

    The median mass error of every line the pass claimed, isotopologues
    included. It is not the correction the pass claims its own rungs against,
    which is the median of two or three anchors, the source's brightest ions:
    on the gate's labelled-nitrate set those put the axis at +0.60 ppm, the
    seven lines together at -1.26, and the run's own commits at -1.10.

    A stand-in, like the instrument class's width, and never pooled with the
    target library's lines: it is taken only where those were too few to fit
    an offset (:attr:`SampleMassAccuracy.scoring_mu_ppm`). The lines are the
    source's own, at the low end of the mass range and far brighter than an
    analyte, and they need not sit where an analyte's lines do: on the gate's
    uronium set they sit at -0.9 ppm while the library's lines and the run's
    commits sit within 0.15 ppm of zero. Pooled, they would pull a sample that
    can measure its own offset away from it.

    :param mu_ppm: The lines' median mass error, or None below
        :data:`REAGENT_OFFSET_MIN_LINES` lines.
    :param lines: How many lines the pass claimed.
    :param beyond_width: Whether the offset is beyond the width a sample is
        scored at when its library fits none. Only then is a sample scored at
        it: an offset inside that width is one the score already allows for, so
        a sample whose lines put the axis where it should be is scored as if
        they had said nothing.
    """

    mu_ppm: float | None
    lines: int
    beyond_width: bool = False


def reagent_line_offset(
    errors_ppm: Iterable[float | None],
    fallback_sigma_ppm: float,
) -> ReagentOffset:
    """What the reagent pre-pass's claimed lines say about a sample's offset.

    :param errors_ppm: The mass error of every line the pass claimed, in ppm.
        Non-finite values are ignored.
    :param fallback_sigma_ppm: The instrument class's width. The offset is
        taken only beyond the width a sample is scored at when its target
        library fits none, which is this one widened
        (:func:`mass_accuracy.scoring_sigma_ppm`): the offset and the width
        need the same number of library lines, so a sample that reaches this
        offset is always scored at the class's width.
    :return: The lines' offset, and whether it is beyond the width.
    """
    errors = [
        float(error)
        for error in errors_ppm
        if error is not None and np.isfinite(float(error))
    ]
    if len(errors) < REAGENT_OFFSET_MIN_LINES:
        return ReagentOffset(mu_ppm=None, lines=len(errors))
    mu = float(np.median(errors))
    return ReagentOffset(
        mu_ppm=mu,
        lines=len(errors),
        beyond_width=abs(mu) > scoring_sigma_ppm(None, float(fallback_sigma_ppm)),
    )


@dataclass(frozen=True)
class SampleMassAccuracy:
    """What Stage A measured of a sample's own mass error, and from how much.

    Either number is None when it was not measured: ``sigma_ppm`` below
    :data:`mass_accuracy.MASS_ACCURACY_MIN_ANCHORS` matched lines of the target
    library and ``mu_ppm`` below :data:`mass_accuracy.MASS_OFFSET_MIN_ANCHORS`.
    Neither is a small measurement - it is no measurement, and ``anchors`` says
    how close it came, so a run that fell back records why.

    ``anchors`` counts the target library's matched lines and nothing else. A
    loaded reference mirror's lines are in the same Stage A frame and are never
    among them (:func:`target_library_rows`), so the count is the same whether
    or not a mirror is loaded.

    The offset is reported separately from the width because each has its own
    stand-in: the width falls back to the instrument class's, and the offset to
    what the reagent pre-pass's lines said (``reagent``) where they show one
    beyond that width, else to none. Scoring a sample a ppm to one side as
    centred is not a smaller correction than scoring it at its offset; it is the
    opposite claim, so :attr:`mu_source` records which one a run made.
    """

    mu_ppm: float | None = None
    sigma_ppm: float | None = None
    anchors: int = 0
    #: What the reagent pre-pass's lines said, recorded whether or not the
    #: sample is scored at it; None where the run had no pre-pass to ask.
    reagent: ReagentOffset | None = None

    @property
    def scoring_mu_ppm(self) -> float | None:
        """The offset both stages score this sample at.

        The target library's where it fitted one, else the reagent lines' where
        they are beyond the width (:attr:`ReagentOffset.beyond_width`), else
        None, which a scorer reads as no correction.
        """
        if self.mu_ppm is not None:
            return self.mu_ppm
        if self.reagent is not None and self.reagent.beyond_width:
            return self.reagent.mu_ppm
        return None

    @property
    def mu_source(self) -> str:
        """Which measurement :attr:`scoring_mu_ppm` is, as a run records it."""
        if self.mu_ppm is not None:
            return MU_SOURCE_FITTED
        if self.reagent is not None and self.reagent.beyond_width:
            return MU_SOURCE_REAGENT
        return MU_SOURCE_NONE


def reference_mirror_mask(match_isotope_df: pd.DataFrame) -> pd.Series:
    """Which rows of a Stage A match frame the reference mirror contributed.

    Told apart by what only a mirror row carries, a non-empty
    ``reference_identities`` list; the target library's rows carry none.

    :param match_isotope_df: A Stage A match frame.
    :return: A boolean mask on the frame's index, all False for a frame with no
        reference column.
    """
    if REFERENCE_IDENTITIES_COL not in match_isotope_df.columns:
        return pd.Series(False, index=match_isotope_df.index, dtype=bool)
    return (
        match_isotope_df[REFERENCE_IDENTITIES_COL]
        .apply(lambda value: isinstance(value, list) and bool(value))
        .astype(bool)
    )


def target_library_rows(match_isotope_df: pd.DataFrame) -> pd.DataFrame:
    """The rows of a Stage A match frame that the target library contributed.

    These are what a sample's mass accuracy is fitted from. A loaded reference
    mirror sits in the same frame, and its lines do not measure the instrument.
    A mirror is matched against every sample, so most of its pairings are lines
    the match window happened to reach rather than compounds the sample holds.
    An Orbitrap's window is too narrow for that to show. A TOF's is wide enough
    for such pairings to scatter across all of it, and then they set the width.
    Measured on the gate's three TOF sets with the default seed loaded, the
    mirror's lines were over 90% of the anchors, and the fitted width went from
    3.0, 5.1 and 2.6 ppm to 8.2, 8.2 and 6.5. Fitting over only the lines that
    won their peak still gave 7.9, 7.5 and 6.9, because a chance line seldom
    has a rival for its peak.

    The target library's own lines do not move when a mirror is loaded beside
    them, because the matcher pairs each ion on its own. A sample whose library
    matches too few of them to fit a width falls back to the instrument class's,
    as it does with no mirror loaded.

    :param match_isotope_df: A Stage A match frame.
    :return: The frame without the reference mirror's rows.
    """
    return match_isotope_df[~reference_mirror_mask(match_isotope_df)]


def is_target_library_row(assignment: dict) -> bool:
    """Whether a ledger row was committed for a compound of the target library.

    The inversion keeps a target winner's ``target_compound_id`` and writes a
    reference mirror's winner without one, with its identities in provenance
    instead. On a ledger row the compound id is therefore what tells the two
    Stage A sources apart.

    :param assignment: A ledger row as the engine builds it.
    :return: True for a Stage A row of the target library, False for a
        reference mirror's row and for every row another pass wrote.
    """
    return assignment.get("source") == SOURCE_DATABASE and bool(
        assignment.get("target_compound_id")
    )


def is_reference_mirror_row(assignment: dict) -> bool:
    """Whether a ledger row was committed for a reference mirror's formula.

    The other Stage A source, told apart from the target library by the compound
    id only a target winner keeps (:func:`is_target_library_row`).

    :param assignment: A ledger row as the engine builds it.
    :return: True for a Stage A row with no target compound, False for a target
        library row and for every row another pass wrote.
    """
    return assignment.get("source") == SOURCE_DATABASE and not is_target_library_row(
        assignment
    )


def sample_mass_accuracy(
    match_isotope_df, reagent: ReagentOffset | None = None
) -> SampleMassAccuracy:
    """Fit a sample's mass accuracy off the frame its Stage A fit was scored on.

    The fit runs over the target library's rows alone
    (:func:`target_library_rows`). Stage A scores its own ions at this width
    (:func:`score_ions_by_fit`) and the untargeted stage is scored at it, so
    both stages leave a reference mirror's lines out of it.

    :param match_isotope_df: The gated, fit-scored Stage A match frame.
    :param reagent: What the reagent pre-pass's lines said
        (:func:`reagent_line_offset`), carried beside the fit and never into
        it: the offset falls back to it only where the fit measured none.
    :return: The fitted offset and width, how many of the target library's
        matched lines they were fitted from, and the reagent lines' reading.
    """
    anchors = target_library_rows(match_isotope_df)
    mu, sigma = fit_sample_mass_accuracy(anchors)
    return SampleMassAccuracy(
        mu_ppm=mu,
        sigma_ppm=sigma,
        anchors=len(mass_accuracy_anchors(anchors)),
        reagent=reagent,
    )


def pattern_scoring_for(
    match_params,
    mass_accuracy: SampleMassAccuracy,
    instrument_accuracy_ppm: float,
) -> PatternScoring:
    """How the untargeted stage scores this sample's isotope envelopes.

    Everything the composition finder needs to judge a candidate as a
    measurement of THIS sample rather than of a generic Orbitrap: the width its
    mass errors actually have, the offset they sit at, the window a line may be
    matched in, and how deep an envelope may be predicted.

    The width comes from Stage A: the fitted spread of the target library's own
    matched isotopologues, which is the instrument's measured accuracy on this
    sample. A reference mirror's lines are not part of that fit
    (:func:`target_library_rows`), because on a TOF they measure the match
    window. The width is widened by ``PRED_SIGMA_PPM`` exactly as Stage A's own
    fit widens it, so a Stage B row and a Stage A row are judged at one width. Both
    go through :func:`mass_accuracy.scoring_sigma_ppm`, the library's one
    statement of what a fit score judges a mass error against, so an outside
    engine scoring the same sample judges it at the same width.

    Where Stage A matched too few known ions to fit anything, the instrument
    class's own accuracy stands in (``profiles.resolve_fallback_sigma_ppm``).
    That is a weak substitute for a measurement and the only honest one
    available: judging a bromide set whose curated library holds two targets at
    the 5 ppm match tolerance instead measures nothing, because on a spectrum
    accurate to 0.3 ppm every candidate the search enumerated is then equally
    good and the election falls to the envelope alone. The offset has a
    stand-in of its own there, the reagent pre-pass's lines
    (:attr:`SampleMassAccuracy.scoring_mu_ppm`).

    :param match_params: The sample's resolved match parameters.
    :param mass_accuracy: What Stage A measured of this sample's mass error.
    :param instrument_accuracy_ppm: The class width to use when it measured none.
    :return: The scoring parameters for this sample's search.
    """
    return PatternScoring(
        sigma_ppm=scoring_sigma_ppm(
            mass_accuracy.sigma_ppm, float(instrument_accuracy_ppm)
        ),
        # The offset Stage A scored its own ions at, and no offset where nothing
        # measured one - the same rule `ion_score_v2` applies to Stage A, so both
        # stages of one sample are corrected by the same amount or by neither.
        mu_ppm=float(mass_accuracy.scoring_mu_ppm or 0.0),
        mz_tolerance_ppm=float(match_params.mz_tolerance),
        abundance_floor=float(match_params.isotope_abundance_threshold),
    )


def pattern_scoring_snapshot(
    scoring: PatternScoring,
    mass_accuracy: "SampleMassAccuracy",
) -> dict:
    """What a run records about the width it judged a mass error against.

    The first gate round of step 2.1 turned on exactly this and the run could
    not answer it: whether the width was the sample's own or the instrument
    class's is the difference between a candidate a whole ppm off being refused
    and being elected, and the row it decided says nothing about it.

    :param scoring: The scoring parameters the search actually used.
    :param mass_accuracy: What Stage A measured, and from how many anchors.
    :return: A JSON-serializable dict for the run's config.
    """
    snapshot = {
        "sigma_ppm": round(float(scoring.sigma_ppm), 4),
        "mu_ppm": round(float(scoring.mu_ppm), 4),
        # "fitted" means this sample measured its own width; "instrument_class"
        # means too few known ions matched to fit one and the class stood in.
        "sigma_source": (
            "fitted" if mass_accuracy.sigma_ppm is not None else "instrument_class"
        ),
        # And the same question about the offset, which has a stand-in of its
        # own: "reagent" means the library fitted none and the reagent
        # pre-pass's lines showed one beyond the width; "none" means the run
        # corrected by zero because it measured nothing, not because it
        # measured zero.
        "mu_source": mass_accuracy.mu_source,
        # The target library's matched lines the width was fitted over; a
        # reference mirror's are never counted, whether or not one is loaded.
        "fitted_anchors": int(mass_accuracy.anchors),
        "mz_tolerance_ppm": float(scoring.mz_tolerance_ppm),
        "abundance_floor": float(scoring.abundance_floor),
    }
    reagent = mass_accuracy.reagent
    if reagent is not None:
        # What the reagent lines said whether or not the run scored at it, so a
        # sample left uncorrected shows whether its lines were inside the width
        # or too few to read.
        snapshot["reagent_lines"] = int(reagent.lines)
        snapshot["reagent_mu_ppm"] = (
            None if reagent.mu_ppm is None else round(float(reagent.mu_ppm), 4)
        )
    return snapshot


def tier_for_evidence(
    evidence: float | None,
    *,
    candidate_threshold: float,
    assigned_threshold: float,
) -> str:
    """Map a peak's evidence onto a confidence tier.

    Evidence is ``fit x plausibility`` -- the measurement of how well the formula
    explains the observed isotope envelope, weighted by how chemically plausible
    that formula is at all. Tiering on the product rather than on the fit alone is
    what stops a chemically implausible formula from holding the ledger's
    strongest word on mass accuracy: it is already the currency both stages
    arbitrate a contested peak in, so the tier now agrees with the quantity that
    picked the winner in the first place.

    ``fit_score`` remains stored and displayed as the pure measurement; it is just
    no longer what buckets the row.

    The bands are keyword-only. They were positional, in the opposite order to
    their names, and every caller needed a comment saying so - a positional call
    written in band order silently inverted them and tiered a whole run wrong.

    :param evidence: The row's evidence, or None when it has none.
    :param candidate_threshold: Evidence at or above which a row is 'candidate'.
    :param assigned_threshold: Evidence at or above which a row is 'assigned'.
    :return: The tier this evidence earns under these bands.
    """
    if evidence is None or not np.isfinite(evidence) or evidence <= 0:
        return TIER_BELOW_ASSIGNABILITY
    if evidence >= assigned_threshold:
        return TIER_ASSIGNED
    if evidence >= candidate_threshold:
        return TIER_CANDIDATE
    return TIER_BELOW_ASSIGNABILITY


def evidence_for(fit_score: float | None, formula: str | None) -> float | None:
    """Evidence for a committed formula: ``fit x plausibility``.

    The one place the product is spelled out for callers that hold a stored row
    rather than a live scoring frame - manual curation, the batch propagation, the
    import check. Both engine stages compute it inline instead, because they
    already carry the plausibility they arbitrated with and re-deriving it from
    the formula string would be a second, divergeable source of the same number.

    Plausibility is recomputed from the formula rather than read off whatever the
    row carries: it is a pure function of the formula (Seven Golden Rules), so
    there is nothing to gain from trusting a number a caller could have made up,
    and an imported row can then be checked without asking its author to declare
    one. It never decides whether a write happens, so a formula that cannot be
    parsed fails open at plausibility 1.0 and the evidence is the fit alone.

    :param fit_score: The row's fit score, or None.
    :param formula: The committed neutral formula, or None.
    :return: The evidence, or None when there is no fit score to weigh.
    """
    fit = _score_or_none(fit_score)
    if fit is None:
        return None
    if not formula:
        return fit
    try:
        return round(fit * float(formula_plausibility(formula)), 4)
    except Exception:  # plausibility must never decide whether a write happens
        return fit


def plausibility_for(formula: str | None) -> float | None:
    """This server's chemical plausibility for a committed formula, for storing.

    The other half of the product :func:`evidence_for` returns, spelled out for
    the same callers and for the same reason: manual curation and the import
    path both write it into ``provenance.plausibility``, which the peak
    inspector renders as this server's reading of the chemistry.

    Answers ``None`` where there is nothing this server can honestly claim - no
    formula, or one it could not parse. That second case needs stating, because
    ``formula_plausibility`` does not raise on it: it fails open to 1.0, which
    is the right answer for *weighing* a fit (an unreadable formula must not
    demote a row) and the wrong one for *displaying*, where it would assert
    perfect chemistry for a string nothing could read. Evidence therefore still
    comes out as the bare fit while the plausibility beside it reads as a dash,
    which is the honest rendering of "could not read it".

    :param formula: The row's committed neutral formula, or None.
    :return: The plausibility, or None when there is none to state.
    """
    if not formula:
        return None
    try:
        if element_counts(formula) is None:
            return None
        return round(float(formula_plausibility(formula)), 4)
    except Exception:  # chemistry must never decide whether a row is stored
        return None


def _float_or_none(value) -> float | None:
    """Coerce to float, mapping NaN/None/missing to None."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _score_or_none(value) -> float | None:
    """Coerce a match score to float within [0, 1], or None."""
    score = _float_or_none(value)
    if score is None:
        return None
    return min(1.0, max(0.0, score))


def _str_or_none(value) -> str | None:
    """Coerce to str, mapping NaN/None to None."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return str(value)


#: Width of ``peak_assignment.isotope_formula``.
ISOTOPE_FORMULA_LENGTH = 256


def fit_isotope_formula(value) -> str | None:
    """An isotopologue formula that fits its column, or None.

    At a low resolution one peak merges many isotopologues, and the isotope
    generator names every one of them, separated by ``/``. For a large ion that
    carries bromine or nitrogen the names run past the column, and a single row
    too long fails the insert of the whole run. The column is a label the
    inspector renders, not a record of every contributor, so whole names are
    kept from the front and the rest are dropped.
    """
    text = _str_or_none(value)
    if text is None or len(text) <= ISOTOPE_FORMULA_LENGTH:
        return text
    kept = ""
    for name in text.split("/"):
        joined = f"{kept}/{name}" if kept else name
        if len(joined) > ISOTOPE_FORMULA_LENGTH:
            break
        kept = joined
    return kept or text[:ISOTOPE_FORMULA_LENGTH]


def _isotope_offset_label(iso_mz: float, main_mz: float | None) -> str | None:
    """Label an isotopologue by its nominal mass offset from the ion's M0, the
    monoisotopic isotopologue: ``M+1``, ``M+2`` ... and, for an element whose
    most abundant isotope is not its lightest (iron), ``M-2``."""
    if main_mz is None or not np.isfinite(main_mz):
        return None
    offset = int(round(iso_mz - main_mz))
    if offset == 0:
        return "M0"
    return f"M+{offset}" if offset > 0 else f"M{offset}"


def labelled_isotopes(ion_formula) -> dict[str, int]:
    """The isotopes an ion carries by design, spelled the way its isotopologue
    formulas spell them: a labelled reagent's ``^N`` is ``{"[15N]": 1}``,
    ``^N2`` is ``{"[15N]": 2}``, and an ion without a label carries none.

    :param ion_formula: The ion's formula (``C9H16O7^N-``).
    """
    labels: dict[str, int] = {}
    if not isinstance(ion_formula, str):
        return labels
    for symbol, count in parse_formula_tokens(ion_formula).items():
        element = CUSTOM_ELEMENTS.get(symbol)
        if element is not None:
            token = f"[{element.labelled_massnumber}{element.base_element}]"
            labels[token] = labels.get(token, 0) + count
    return labels


def _substituted_isotopes(isotopologue_formula: str) -> dict[str, int]:
    """The isotopes an isotopologue formula names in brackets, with counts."""
    return {
        symbol: count
        for symbol, count in parse_formula_tokens(isotopologue_formula).items()
        if symbol.startswith("[")
    }


def is_monoisotopic_formula(formula, labels: dict[str, int] | None = None) -> bool:
    """Whether an isotopologue formula names the ion's monoisotopic isotopologue.

    The generator writes a substituted isotope in brackets (``C5[13C]H13O6+``,
    ``[81Br]Br2-``) and the monoisotopic isotopologue - every element at its
    most abundant isotope - without (``C6H13O6+``, ``Br3-``). A labelled
    reagent's atom is written in brackets too, because its isotope is the one
    the label put there, so the monoisotopic isotopologue of a labelled ion
    names exactly its labels and nothing else: ``[15N]C9H16O7-`` for
    ``C9H16O7^N-``. The formula without a bracket is then the reagent's
    unlabelled remainder, one mass unit below the line the ion is measured by.

    At a low resolution one line holds several isotopologues, their names
    joined by "/"; it is the monoisotopic line when any of them is.

    :param formula: An isotopologue formula.
    :param labels: The ion's labelled isotopes (:func:`labelled_isotopes`);
        none for an ion without a label.
    """
    if not isinstance(formula, str) or not formula:
        return False
    expected = labels or {}
    return any(_substituted_isotopes(name) == expected for name in formula.split("/"))


def monoisotopic_row(ion_rows: pd.DataFrame) -> pd.Series:
    """The row of an ion's monoisotopic isotopologue: the M0 every role and
    offset label counts from, the way an isotope table counts - which for a
    bromine- or chlorine-rich ion is the lightest peak of the cluster, not the
    tallest, and for a labelled ion is the labelled line, not the unlabelled
    remainder below it. The lightest row stands in when no formula carries the
    isotope marker that tells them apart, and is the same row wherever an
    element's most abundant isotope is also its lightest.

    Positionally off a sort rather than ``.loc[idxmin()]``: a frame that has
    been through a gate and a scorer can carry a duplicated index, and that
    lookup would then hand back a frame where every caller expects one row.

    :param ion_rows: One ion's rows of an isotope frame (``mz``, and
        ``target_isotope_formula`` and ``target_ion_formula`` where the frame
        has them).
    """
    ordered = ion_rows.sort_values("mz")
    if "target_isotope_formula" in ordered.columns:
        labels = (
            labelled_isotopes(ordered["target_ion_formula"].iloc[0])
            if "target_ion_formula" in ordered.columns
            else {}
        )
        mono = ordered[
            ordered["target_isotope_formula"]
            .map(lambda formula: is_monoisotopic_formula(formula, labels))
            .astype(bool)
        ]
        if not mono.empty:
            return mono.iloc[0]
    return ordered.iloc[0]


# Columns the ion-level fit score needs on the isotope frame. Absent (e.g. a lighter
# match path) -> the fit scoring is skipped and the per-isotopologue score stands.
_FIT_SCORE_COLS = frozenset(
    {"target_ion_id", "relative_abundance", "match_mz_error", "sample_peak_intensity"}
)


def drop_ions_claimed_elsewhere(
    match_isotope_df: pd.DataFrame, claimed_peak_ids: set[str]
) -> pd.DataFrame:
    """Remove target ions whose peaks another pass has already claimed.

    Dropping the matched ROWS is not enough, and the difference is what this
    function exists for. An ion is inverted as a family - one M0 and its
    isotopologue children, the children naming the M0 as their owner - so
    removing only the row that landed on the claimed peak leaves the children
    behind with nothing to belong to: the lightest of them is read as the
    ion's M0, and the ledger carries an isotopologue family whose ion is not
    in it.

    So the whole ion goes when its monoisotopic peak is claimed: an ion whose M0
    is the reagent has the reagent's isotopologues, not its own. Any straggler
    row that landed on a claimed peak goes too, which keeps the ledger's one row
    per peak whichever part of the family the claim caught.

    :param match_isotope_df: The gated, scored isotope frame.
    :param claimed_peak_ids: Peaks another pass owns.
    :return: The frame without those ions.
    """
    if match_isotope_df.empty or not claimed_peak_ids:
        return match_isotope_df
    if not {"target_ion_id", "sample_peak_id"} <= set(match_isotope_df.columns):
        return match_isotope_df
    claimed_ions = {
        ion_id
        for ion_id, group in match_isotope_df.groupby("target_ion_id", sort=False)
        if str(monoisotopic_row(group).get("sample_peak_id") or "") in claimed_peak_ids
    }
    keep = ~match_isotope_df["sample_peak_id"].isin(claimed_peak_ids)
    if claimed_ions:
        keep &= ~match_isotope_df["target_ion_id"].isin(claimed_ions)
    return match_isotope_df[keep]


def score_ions_by_fit(
    match_isotope_df: pd.DataFrame,
    fallback_sigma_ppm: float | None = None,
    reagent_offset: ReagentOffset | None = None,
) -> pd.DataFrame:
    """Set each isotopologue's ``match_score`` to its ion's fit score (Stage A).

    The peak-centric engine adopts the fit score (`score_pattern_v2`) *deliberately*
    as its scoring engine -- unconditionally, not gated on the legacy
    ``MASCOPE_MATCH_SCORE_VERSION`` switch, per the epic's "coexist, don't replace"
    principle. Where the targeted matcher emits a per-isotopologue
    ``abundance_term * mz_term``, this replaces it with the consolidated ion-level
    fit quality: the whole predicted isotope envelope scored against the spectrum
    (mass, intensity, SNR-detectability), computed exactly as the aggregate match
    path does (`ion_score_v2` per ``target_ion_id`` with the sample's fitted mass
    accuracy). That accuracy is fitted over the target library's lines only
    (:func:`sample_mass_accuracy`), so every ion in the frame, a reference
    mirror's included, is scored at a width the mirror's own chance lines did
    not widen. Every isotopologue of an ion carries that ion's fit, so the
    single-owner arbitration in `invert_matches_to_peak_assignments` awards a
    contested peak to the better-corroborated assignment, not the one with the
    best single-peak mass hit.

    Call AFTER `apply_match_params`: it zeroes ``sample_peak_intensity`` for
    out-of-tolerance isotopologues, and this treats any isotopologue the gating
    rejected (``match_score == 0``) as absent, so the fit score honours the same
    tolerance / intensity-floor gating as the targeted Match tab.

    The returned frame carries that verdict: an isotopologue the gating rejected
    comes back with ``sample_peak_intensity`` zeroed, not just excluded from the
    arithmetic. `apply_match_params` only zeroes the intensity of *out-of-tolerance*
    pairings - a within-tolerance pairing it rejected on the intensity floor keeps
    its intensity - so without this the ownership guard in
    `invert_matches_to_peak_assignments` would let a pairing this function counted
    as ABSENT claim its peak anyway, carrying the ion's (possibly "assigned") fit
    score and blocking Stage B from explaining that peak. One gating decision, one
    frame.

    Real per-peak ``signal_to_noise`` (carried from the filestore by
    `compute_match_isotopes`) makes this the full v2 fit; rows without it are
    scored in `ion_score_v2`'s no-SNR mode. No-op (returns the frame
    unchanged) when empty or missing the required columns.

    NOTE: what this computes is the fit, which is NOT what the tier is read off.
    The confidence-tier bands sit on the EVIDENCE scale (fit x plausibility) --
    assigned/candidate = 0.75/0.45 on `PeakAssignmentConfig` (config.py). The fit
    is half of that product and stays the pure measurement; see
    `tier_for_evidence`. Per-instrument recalibration of the bands is a follow-up
    once verification labels accumulate.

    :param match_isotope_df: The gated match frame.
    :param fallback_sigma_ppm: The instrument class's width
        (``profiles.resolve_fallback_sigma_ppm``). A sample whose target library
        matched too few lines to fit a width is scored at it, the stand-in the
        untargeted stage takes (:func:`pattern_scoring_for`), so the two stages
        still score at one width. Without it such a sample is scored at
        ``score_pattern_v2``'s generic 2 ppm. Measured on the gate's bromide
        Orbitrap set, whose library matches two lines a sample, that generic
        width put 155 more of a loaded seed's rows at assigned tier once the
        seed's lines were out of the fit, and G1 rose from 8.0 to 15.3%. None
        keeps the generic width, for a caller with no instrument class to name.
    :param reagent_offset: What the reagent pre-pass's lines said about the
        offset (:func:`reagent_line_offset`). A sample whose target library
        matched too few lines to fit an offset is scored at it where it is
        beyond the width (:attr:`ReagentOffset.beyond_width`), which is the
        offset the untargeted stage is scored at too
        (:attr:`SampleMassAccuracy.scoring_mu_ppm`).
        Measured on the gate's labelled-nitrate set, whose curated lines are
        too few once its workaround entries are gone, the lines put the axis
        at -1.26 ppm and the run's own commits at -1.10, where the sample was
        otherwise scored at zero. None leaves such a sample uncorrected.
    :return: The gated frame with every ion's fit as its rows' ``match_score``.
    """
    if match_isotope_df.empty or not _FIT_SCORE_COLS.issubset(match_isotope_df.columns):
        return match_isotope_df

    df = match_isotope_df.copy()
    # Honour apply_match_params gating: an isotopologue it rejected (score 0) is
    # absent to the fit score (its intensity may still be set, e.g. below the
    # intensity floor but within tolerance).
    if "match_score" in df.columns:
        gated_out = pd.to_numeric(df["match_score"], errors="coerce").fillna(0.0) == 0
        df.loc[gated_out, "sample_peak_intensity"] = 0.0

    # The one measurement the untargeted stage is scored at too, read off this
    # same gated frame, with the same stand-ins where it measured nothing: the
    # class width for the width, the reagent lines' offset where it is beyond
    # the width.
    # The offset is None where neither measured one; the scorer reads that as
    # an uncorrected sample rather than a centred one.
    accuracy = sample_mass_accuracy(df, reagent=reagent_offset)
    sigma = accuracy.sigma_ppm if accuracy.sigma_ppm is not None else fallback_sigma_ppm
    mu = accuracy.scoring_mu_ppm
    noise = sample_noise_floor(df)
    fit_by_ion = df.groupby("target_ion_id", sort=False, dropna=False).apply(
        lambda g: ion_score_v2(g, sigma_ppm=sigma, mu=mu, noise=noise),
        include_groups=False,
    )
    # Return the GATED frame (not a fresh copy of the input): the zeroed intensities
    # are the record of which pairings this scoring counted as absent, and the
    # ownership guard downstream reads exactly that.
    df["match_score"] = df["target_ion_id"].map(fit_by_ion).astype(float)
    return df


def _row_reference_identities(row) -> list | None:
    """Reference identities carried on a match row, else None.

    Only reference-sourced isotope rows carry a non-empty list here; target and
    untargeted rows have NaN. Used to null the synthetic target FKs and to attach
    the one-to-many identities to the assignment's provenance.
    """
    value = row.get(REFERENCE_IDENTITIES_COL)
    return value if isinstance(value, list) and value else None


def calibration_meta(calibration) -> dict | None:
    """What a run records about the confidence calibration it applied.

    The instrument class, whether the curve is provisional, and what it was fit
    from - the three fields every database-sourced row used to repeat in its
    provenance, now recorded once per run (``PeakAssignmentRun.confidence_calibration``)
    and folded back into each row by the detail read. ``None`` when the run was
    uncalibrated, which is what a reader takes to mean "no curve".

    :param calibration: The calibration the run applied, or None.
    :return: The record for the run row, or None.
    """
    if calibration is None:
        return None
    return {
        "instrument": calibration.instrument,
        "provisional": calibration.provisional,
        "source": calibration.source,
    }


def _alternative_dict(
    row,
    reference_identities_by_formula: dict,
    main_isotope_ids: set | frozenset = frozenset(),
    main_mz_by_ion: dict | None = None,
) -> dict:
    """Build one runner-up candidate dict for a peak's ``alternatives`` list.

    Null the target FKs when the runner-up is itself a reference row, and attach
    the formula's reference identities (if any) so a runner-up known compound is
    still named.

    The isotopologue label is recorded the same way the winner's is. A runner-up
    is a target *isotope* that also landed on this peak, and it is just as free
    as the winner to be one of its ion's isotopologues rather than the main one -
    so without the label, promoting such a candidate by hand would enter a
    compound's M+1 into the ledger as the compound's main peak.
    """
    is_reference_row = _row_reference_identities(row) is not None
    formula = _str_or_none(row.get("target_compound_formula"))
    ion_id = _str_or_none(row.get("target_ion_id"))
    isotope_label = (
        "M0"
        if row.get("target_isotope_id") in main_isotope_ids
        else _isotope_offset_label(row["mz"], (main_mz_by_ion or {}).get(ion_id))
    )
    alternative = {
        "assigned_formula": formula,
        "ion_formula": _str_or_none(row.get("target_ion_formula")),
        "isotope_label": isotope_label,
        # The adduct the runner-up was scored under. A formula is only half an
        # assignment - without the mechanism a promoted runner-up would land on
        # the ledger as an adductless claim, and a verification's identity
        # (peak + formula + mechanism) would be incomplete. Older rows predate
        # this key; curation falls back to the target ion's mechanism there.
        "ionization_mechanism_id": _str_or_none(row.get("ionization_mechanism_id")),
        "target_compound_id": (
            None if is_reference_row else _str_or_none(row.get("target_compound_id"))
        ),
        "target_ion_id": (
            None if is_reference_row else _str_or_none(row.get("target_ion_id"))
        ),
        "fit_score": _score_or_none(row.get("match_score")),
        "mz_error_ppm": _float_or_none(row.get("match_mz_error")),
        "plausibility": _float_or_none(row.get("_plaus")),
        "source": SOURCE_DATABASE,
    }
    formula_identities = reference_identities_by_formula.get(formula_identity(formula))
    if formula_identities:
        alternative["reference_identities"] = formula_identities
    return alternative


@lru_cache(maxsize=4096)
def formula_identity(formula: str | None) -> str:
    """A neutral formula's identity, however it was written.

    Stage A's frame holds formulas from two authors. A target library holds what
    a person typed - ``CH3COOH``, ``NH3``, ``H2SO4`` - and a reference list holds
    the same neutrals in Hill order, so one reading of a peak can arrive spelled
    twice. Compared as text the two spellings were two hypotheses: the tie
    between them fell to alphabetical order, the library's entry lost its own
    line to the list's copy, and the candidate-density rule counted the copy as
    a rival. Read as a composition they are one.

    :param formula: A neutral formula as a library or a reference list spells it.
    :return: The Hill-order formula of its composition, ``"()"`` for the empty
        neutral, and the text as given where it does not parse, so an unreadable
        formula is never merged with another.
    """
    text = str(formula or "").strip()
    try:
        counts = {symbol: n for symbol, n in parse_composition(text).items() if n}
    except Exception:  # noqa: BLE001 - an unreadable formula keeps its own text
        return text
    if not counts and text not in ("", "()"):
        return text
    return to_hill_order(counts)


def _restates_winner(contenders: "pd.DataFrame", winner) -> "pd.Series":
    """Mask of contender rows that restate the winner's own hypothesis.

    Same formula through the same ionization mechanism is one explanation of the
    peak, however many rows carried it into the frame and however each spelled
    it (:func:`formula_identity`) - the reference mirror sits in the same frame
    as the curated targets, so a compound that is both a target and a known
    reference contributes two rows, and two targets can share a formula
    outright. Excluding the winner by position alone left those twins in
    `alternatives`, where the inspector rendered them exactly like the committed
    assignment: the peak's own answer offered back as a close alternative.

    The isotope label adds nothing to that identity. Within one ion the matcher
    already forbids two isotopes from holding the same peak - `_match_assign`
    keeps a per-ion set of claimed peaks and awards a contested one to the higher
    relative abundance - so the winner's own ion is represented here exactly once.
    Two ions that share a formula and a mechanism can still claim one peak through
    different isotopologues, and that row is dropped as well: an alternative
    carries no isotope label, so it would render as the bare committed formula,
    which is the duplicate this screen exists to remove.

    :param contenders: The peak's rows other than the winning one, carrying
        ``_formula_key``.
    :param winner: The row that won the peak.
    :return: Boolean mask, True where the row is the winner's hypothesis again.
    """
    same = contenders["_formula_key"] == str(winner["_formula_key"])
    if "ionization_mechanism_id" in contenders.columns:
        same &= contenders["ionization_mechanism_id"].astype(str) == str(
            winner.get("ionization_mechanism_id")
        )
    return same


def _reference_copies(matched: "pd.DataFrame") -> "pd.Series":
    """Mask of reference mirror rows that restate a target library row on their peak.

    A copy is the same neutral (:func:`formula_identity`) through the same
    mechanism on the same peak: the same ion, so the same line of it.

    :param matched: The gated Stage A frame, carrying ``_formula_key``.
    :return: Boolean mask on the frame's index, True for a mirror row whose
        reading the target library also makes on that peak.
    """
    mirror = reference_mirror_mask(matched)
    if not mirror.any():
        return mirror
    mechanism = (
        matched["ionization_mechanism_id"].astype(str)
        if "ionization_mechanism_id" in matched.columns
        else pd.Series("", index=matched.index)
    )
    readings = list(
        zip(
            matched["sample_peak_id"].astype(str),
            matched["_formula_key"],
            mechanism,
            strict=True,
        )
    )
    library = {
        reading
        for reading, is_mirror in zip(readings, mirror, strict=True)
        if not is_mirror
    }
    return pd.Series(
        [
            bool(is_mirror) and reading in library
            for reading, is_mirror in zip(readings, mirror, strict=True)
        ],
        index=matched.index,
        dtype=bool,
    )


def invert_matches_to_peak_assignments(
    match_isotope_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
    candidate_threshold: float,
    assigned_threshold: float,
    max_alternatives: int = 5,
    instrument: str | None = None,
    calibration: "Calibration | None | object" = _CALIBRATION_UNSET,
) -> list[dict]:
    """Invert target-first match results into per-peak assignments (Stage A).

    The targeted matcher produces one row per target isotope with the
    sample_peak_id it hit (or a placeholder when unmatched). This groups those
    rows by peak, picks the best-scoring owner per peak (ties broken by
    smaller m/z error), and keeps the runners-up as alternatives - the
    single-owner-per-peak invariant.

    Roles: the winner is 'M0' when it is its ion's monoisotopic isotopologue -
    the M0 an isotope table counts from, the lightest peak of a bromine-rich
    cluster rather than the tallest, the labelled line of a labelled ion rather
    than the unlabelled remainder below it - otherwise 'iso_child' pointing at
    the assignment that holds the ion's M0 peak. An isotopologue is written
    only beside that assignment. One whose ion did not win its M0 peak here
    would say its peak is part of an envelope whose ion the ledger never
    commits, so it is left out and the peak goes to the untargeted stage - the
    rule that stage keeps for its own rows.

    :param match_isotope_df: Output of compute_match_isotopes enriched with
        target metadata columns (target_compound_id, target_compound_formula,
        target_ion_formula, ionization_mechanism_id).
    :param sample_item_id: Sample the peaks belong to.
    :param peak_assignment_run_id: Run the assignments belong to.
    :param candidate_threshold: Evidence threshold for the 'candidate' tier.
    :param assigned_threshold: Evidence threshold for the 'assigned' tier.
    :param max_alternatives: Cap on stored runner-up candidates per peak.
    :param instrument: Instrument class; selects the in-code calibration when ``calibration``
        is not passed. ``None`` -> uncalibrated.
    :param calibration: The confidence calibration to apply (from the D6 store). If omitted,
        falls back to the in-code registry via ``calibration_for(instrument)``; pass ``None``
        explicitly to force uncalibrated. Its ``corroboration_weights`` drive the P3 adduct
        co-occurrence fold-in on ``p_correct``.
    :return: One assignment dict per matched peak, ready for bulk insert.
    """
    if match_isotope_df.empty:
        return []

    # A peak may only be OWNED by a target isotopologue the gating accepted. The targeted
    # matcher pairs each target isotope to the nearest peak in a wide 0.5 Da search window
    # (for the legacy Match tab's ppm-error display), then `apply_match_params` zeroes
    # `sample_peak_intensity` for pairings outside the m/z / abundance tolerance and
    # `score_ions_by_fit` zeroes it for the ones the gating rejected on the intensity floor
    # (both legs of one gating decision, see its docstring). Without this guard a trace
    # isotopologue whose real peak is absent claims whatever peak sits in that window - tens
    # to hundreds of ppm off, or below the floor the scorer treated as no peak at all, and
    # actually belonging to another compound - inheriting its ion's tier and blocking that
    # peak's correct assignment (Stage B or another target). Requiring a positive gated
    # intensity releases those peaks to the untargeted stage instead.
    matched = match_isotope_df[
        match_isotope_df["sample_peak_id"].notna()
        & (match_isotope_df["sample_peak_id"] != "")
        & (match_isotope_df["sample_peak_intensity"].fillna(0) > 0)
    ].copy()
    if matched.empty:
        return []

    # Reference identities keyed by canonical formula, drawn from the reference
    # rows in the frame. A peak's winner - target OR reference - inherits its
    # formula's identities in provenance, so a curated target keeps its
    # target_compound_id while still surfacing the known-compound name(s) that
    # share its formula (the convergence precedence: target owns the FK, reference
    # identity rides alongside).
    reference_identities_by_formula: dict[str, list] = {}
    if REFERENCE_IDENTITIES_COL in match_isotope_df.columns:
        reference_rows = match_isotope_df[reference_mirror_mask(match_isotope_df)]
        for formula, group in reference_rows.groupby(
            reference_rows["target_compound_formula"].map(formula_identity)
        ):
            reference_identities_by_formula[str(formula)] = group.iloc[0][
                REFERENCE_IDENTITIES_COL
            ]

    # Reference isotope per ion - the monoisotopic isotopologue, the M0 an isotope
    # table counts from - used for role attribution and isotope labelling. For a
    # bromine- or chlorine-rich ion that is the lightest peak of the cluster, not
    # the most intense one. Computed over the full target set so an ion whose M0
    # went unmatched still labels its isotopologues correctly where they are
    # listed as a peak's alternatives.
    references = [
        monoisotopic_row(group)
        for _, group in match_isotope_df.groupby("target_ion_id", sort=False)
    ]
    main_isotope_ids = {reference["target_isotope_id"] for reference in references}
    main_mz_by_ion = {
        str(reference["target_ion_id"]): float(reference["mz"])
        for reference in references
    }

    # Arbitration (P2): rank a peak's competing candidates by evidence =
    # fit x chemical plausibility, not fit alone, so a chemically implausible formula
    # cannot win a peak on mass fit. The stored fit_score stays the pure measurement,
    # but evidence now decides the winner, the reported confidence AND the tier - the
    # three used to disagree, and a formula that won a peak on evidence could then be
    # banded as though it had fit cleanly.
    #
    # Every comparison of two candidates' formulas below is of their identities, so
    # one neutral spelled two ways is one hypothesis (:func:`formula_identity`).
    matched["_formula_key"] = (
        matched["target_compound_formula"].astype(str).map(formula_identity)
    )
    plaus_by_formula = {
        f: formula_plausibility(f) for f in matched["_formula_key"].unique()
    }
    matched["_plaus"] = matched["_formula_key"].map(plaus_by_formula)
    # A reference list's copy of a reading the target library makes on the same
    # peak is that reading again, and the library's row owns it. The list's names
    # still ride along on it (above), but the copy never takes the line, whichever
    # of the two the matcher happened to fit a shade better.
    matched = matched[~_reference_copies(matched)].copy()
    # Calibration maps the winner's evidence to P(correct) for this instrument. None
    # when the instrument has no curated calibration (e.g. TOF) -> the assignment is
    # reported uncalibrated rather than borrowing another instrument's curve. The service
    # passes the calibration from the D6 store; falling back to the in-code registry keeps
    # direct callers (and unit tests) working unchanged.
    if calibration is _CALIBRATION_UNSET:
        calibration = calibration_for(instrument)
    matched["_fit"] = matched["match_score"].map(lambda v: _score_or_none(v) or 0.0)
    matched["_evidence"] = matched["_fit"] * matched["_plaus"]
    matched["_abs_mz_error"] = matched["match_mz_error"].abs()
    # This row sort selects the winning ROW only; confidence and ties are delegated
    # to `arbitration.arbitrate_candidates` per peak below. The selection cannot be
    # delegated because the winner has to keep the whole match row - target FKs,
    # reference identities, isotope role, mass and abundance errors - which the
    # library's (formula, fit) view cannot carry, and because mass error is the
    # domain-meaningful middle key: between two equally plausible formulas that fit
    # equally well, the closer mass is the better assignment. The formula key is last
    # so two candidates equal on all three still resolve by the data rather than by
    # whatever order the matcher happened to emit them in.
    matched = matched.sort_values(
        ["sample_peak_id", "_evidence", "_abs_mz_error", "_formula_key"],
        ascending=[True, False, True, True],
    )

    assignments: list[dict] = []
    m0_assignment_by_ion: dict[str, str] = {}
    child_assignments: list[tuple[dict, str]] = []
    # (assignment, compound_id, adduct notation) for M0 winners, for the P3 corroboration
    # post-pass (a compound seen via several adducts lifts each one's p_correct).
    m0_corroboration: list[tuple[dict, str, str]] = []

    for sample_peak_id, group in matched.groupby("sample_peak_id", sort=False):
        winner = group.iloc[0]
        # The winner is never its own alternative. Dropping the rows that merely
        # restate it BEFORE the cap matters twice over: the duplicate never
        # reaches the inspector, and it does not burn one of the `max_alternatives`
        # slots that a genuinely different formula could have had.
        contenders = group.iloc[1:]
        runners = (
            contenders[~_restates_winner(contenders, winner)].iloc[:max_alternatives]
            if max_alternatives
            else contenders.iloc[:0]
        )

        # Arbitration confidence and the tie flag come from the shared
        # `arbitrate_candidates`, not an inline copy - one implementation, so a fix
        # in the library reaches the engine (issue #1731). Delegating buys the two
        # behaviours the inline copy had lost: duplicate formulas are COLLAPSED
        # before normalisation (the same formula arriving via two adducts is one
        # hypothesis, not two competitors splitting their own confidence into a
        # self-tie), and the tie gap is RELATIVE to the best evidence with an
        # absolute floor, instead of one absolute gap that called nearly
        # everything below 0.1 apart a tie.
        #
        # Confidence answers "which of this peak's candidates", NOT "how good is
        # this assignment" - an uncontested peak scores 1.0 however poorly its
        # single candidate fits, because there was nothing to lose to.
        # `n_candidates` is recorded alongside it so a 1.0 earned against
        # competitors is distinguishable from a 1.0 won by default; "how good" is
        # `p_correct` (the calibrated evidence), which does not divide by the
        # field and does fall for a poor lone candidate.
        arbitrated = arbitrate_candidates(
            zip(group["_formula_key"], group["_fit"], strict=True)
        )
        winner_arbitrated = next(
            c for c in arbitrated if c.formula == str(winner["_formula_key"])
        )
        confidence = winner_arbitrated.confidence
        is_tie = winner_arbitrated.is_tie

        # The winner's evidence, rounded once here and used for both the tier and
        # the provenance the ledger displays. Two roundings of one quantity is how
        # a row ends up in the band below the percentage its own chip shows.
        evidence = round(float(winner["_evidence"]), 4)

        # Calibrated P(correct) for the winner's evidence - only when this instrument
        # has a calibration; otherwise the assignment is honestly left uncalibrated.
        # Which curve it was is the run's to record (`calibration_meta`), not each
        # row's: one curve serves a whole run.
        p_correct = (
            round(float(apply_calibration(float(winner["_evidence"]), calibration)), 4)
            if calibration is not None
            else None
        )
        alternatives = [
            _alternative_dict(
                row, reference_identities_by_formula, main_isotope_ids, main_mz_by_ion
            )
            for _, row in runners.iterrows()
        ]

        ion_id = _str_or_none(winner.get("target_ion_id"))
        # A reference-*row* winner has synthetic target ids that must not persist as
        # FKs. Separately, any winner (target or reference) whose *formula* is in the
        # reference mirror inherits those identities in provenance.
        winner_is_reference_row = _row_reference_identities(winner) is not None
        formula_identities = reference_identities_by_formula.get(
            str(winner["_formula_key"])
        )
        is_main = winner["target_isotope_id"] in main_isotope_ids
        isotope_label = (
            "M0"
            if is_main
            else _isotope_offset_label(winner["mz"], main_mz_by_ion.get(ion_id))
        )

        assignment = {
            "peak_assignment_id": gen_id(32),
            "peak_assignment_run_id": peak_assignment_run_id,
            "sample_item_id": sample_item_id,
            "sample_peak_id": str(sample_peak_id),
            "sample_peak_mz": float(winner["sample_peak_mz"]),
            "sample_peak_intensity": float(winner["sample_peak_intensity"]),
            "sample_peak_tof": _float_or_none(winner.get("sample_peak_tof")),
            "role": ROLE_M0 if is_main else ROLE_ISO_CHILD,
            "assigned_formula": _str_or_none(winner.get("target_compound_formula")),
            "ion_formula": _str_or_none(winner.get("target_ion_formula")),
            "ionization_mechanism_id": _str_or_none(
                winner.get("ionization_mechanism_id")
            ),
            "isotope_label": isotope_label,
            "isotope_formula": fit_isotope_formula(
                winner.get("target_isotope_formula")
            ),
            "source": SOURCE_DATABASE,
            "fit_score": _score_or_none(winner["match_score"]),
            "mz_error_ppm": _float_or_none(winner["match_mz_error"]),
            "abundance_error": _float_or_none(winner["match_abundance_error"]),
            # Tiered on the evidence this peak was WON with, not on the fit alone:
            # the same product that beat the runners-up above decides which band
            # the winner lands in, so a formula that only won because nothing more
            # plausible competed cannot also claim the top tier on mass fit.
            #
            # The ROUNDED value, which is also what provenance records and the
            # ledger shows beside the tier. Tiering the full-precision product
            # instead would put a row reading 0.7499996 into the band below the
            # 75% its own chip displays.
            "tier": tier_for_evidence(
                evidence,
                candidate_threshold=candidate_threshold,
                assigned_threshold=assigned_threshold,
            ),
            "target_compound_id": _str_or_none(winner.get("target_compound_id")),
            # A reference-row winner carries only a synthetic ion id (for in-run
            # grouping), never persisted as a dangling FK; a target winner keeps its.
            "target_ion_id": None if winner_is_reference_row else ion_id,
            "owner_peak_assignment_id": None,
            "alternatives": alternatives or None,
            "provenance": {
                "confidence": round(confidence, 4),
                # How many DISTINCT formulas the confidence was normalised across
                # (duplicate arrivals of one formula collapse); 1 means the peak
                # was uncontested and the 1.0 above was won by default.
                "n_candidates": int(len(arbitrated)),
                # ...and how many of them the evidence could not SEPARATE, which
                # is the different question: a peak with nine candidates whose
                # winner clears them all is not the same peak as one with three
                # the evidence ranks equally. Read off the arbitration above
                # rather than competed again.
                #
                # On the MAIN row only. This loop writes one provenance blob for
                # every row of a winner's envelope, and an isotopologue was never
                # searched - it is a line predicted from the winner and matched,
                # so the peak's candidate count is not a measurement of it. The
                # untargeted stage drops it from a child for the same reason
                # (`finder.process_isotopes`), and the schema says an isotopologue
                # carries none.
                #
                # `around` cannot change the number HERE and is passed anyway:
                # the row sort above orders by `_evidence`, which is the same
                # product the arbitration ranks on, so this stage's winner is
                # always the arbitration's top and the anchored count is the top
                # tie set by construction. It matters in the untargeted stage,
                # where the finder ranks on the fit alone. Passing it keeps the
                # count anchored on what the row commits if this selection ever
                # stops agreeing - it is not pinned by a test, because no input
                # can make the two disagree while that sort key stands.
                **(
                    {
                        CANDIDATE_DENSITY: density_of(
                            arbitrated, around=str(winner["_formula_key"])
                        )
                    }
                    if is_main
                    else {}
                ),
                "plausibility": round(float(winner["_plaus"]), 4),
                "evidence": evidence,
                "is_tie": is_tie,
                # The fit-score generation this evidence is on. Snapshotted here because
                # a verification label is only interpretable against the scoring that
                # produced it (see AssignmentVerification.score_version).
                "score_version": SCORE_VERSION,
                # P(correct) is the calibrated probability; null when uncalibrated,
                # so the UI can show "uncalibrated" instead of a fabricated number.
                # The curve it was read off is on the run; the detail read folds
                # it back in here as `calibrated` / `calibration`.
                "p_correct": p_correct,
                # One-to-many known-compound identities for a database-sourced peak
                # whose formula is in the reference mirror (name/source/license),
                # attached whether the target library or the reference set won it.
                **(
                    {"reference_identities": formula_identities}
                    if formula_identities
                    else {}
                ),
            },
        }
        assignments.append(assignment)

        if not is_main:
            child_assignments.append((assignment, ion_id))
        elif ion_id is not None:
            m0_assignment_by_ion[ion_id] = assignment["peak_assignment_id"]
            compound_id = _str_or_none(winner.get("target_compound_id"))
            notation = _str_or_none(winner.get("ionization_mechanism"))
            if compound_id and notation:
                m0_corroboration.append((assignment, compound_id, notation))

    # Attribute each isotopologue to its ion's M0 assignment, and leave out the
    # ones whose ion did not win its M0 peak in this run: their peaks go to the
    # untargeted stage. Settled after the loop, because an ion's M0 peak may be
    # decided after its isotopologue's.
    orphaned: set[str] = set()
    for assignment, ion_id in child_assignments:
        owner = m0_assignment_by_ion.get(ion_id)
        if owner is None:
            orphaned.add(assignment["peak_assignment_id"])
        else:
            assignment["owner_peak_assignment_id"] = owner
    if orphaned:
        assignments = [
            assignment
            for assignment in assignments
            if assignment["peak_assignment_id"] not in orphaned
        ]
        runtime.logger.info(
            f"Stage A: {len(orphaned)} isotopologue rows whose ion did not win its "
            f"monoisotopic peak were not written; their peaks go to the untargeted "
            f"stage."
        )

    # P3 corroboration: fold co-occurring adducts of the same compound into p_correct.
    if calibration is not None:
        _fold_adduct_corroboration(m0_corroboration, calibration.corroboration_weights)

    return assignments


def _fold_adduct_corroboration(
    m0_items: list[tuple[dict, str, str]],
    weights: "dict | None",
) -> None:
    """Fold adduct co-occurrence into each M0 winner's ``p_correct`` in place (P3).

    A compound assigned via several confident adducts corroborates each of them: for each winner
    we add the measured log-odds of the OTHER adducts its compound was seen via (see
    ``apply_corroboration``). Only confident (assigned/candidate) winners count toward the
    co-occurrence set, so a low-confidence sibling can't manufacture corroboration. No-op when the
    calibration carries no weights. Records the co-occurrence + boost in provenance for the UI."""
    if not weights or not m0_items:
        return
    # Keyed by mechanism once for the fold rather than once per winner.
    weights = corroboration_by_mechanism(weights)
    adducts_by_compound: dict[str, set[str]] = {}
    for assignment, compound_id, notation in m0_items:
        if assignment["tier"] in (TIER_ASSIGNED, TIER_CANDIDATE):
            adducts_by_compound.setdefault(compound_id, set()).add(notation)
    for assignment, compound_id, notation in m0_items:
        all_adducts = adducts_by_compound.get(compound_id, set())
        others = all_adducts - {notation}
        if not others:
            continue
        prov = assignment["provenance"]
        p0 = prov.get("p_correct")
        p1 = apply_corroboration(p0, sorted(others), weights)
        prov["p_correct"] = round(p1, 4) if p1 is not None else None
        prov["corroboration"] = {
            "adducts": sorted(all_adducts),
            "n_adducts": len(all_adducts),
            "boost": round(p1 - p0, 4) if (p0 is not None and p1 is not None) else None,
        }


def record_mirror_same_ion_readings(
    assignments: list[dict],
    *,
    mechanism_id_by_notation: dict[str, str],
    search_config: CompositionSearchConfig,
    heuristics_config: HeuristicFilterConfig,
    formula_formatter=None,
    max_alternatives: int = 5,
) -> int:
    """Give each reference mirror row the other readings of its ion.

    An election leaves the readings it displaced on its winner, flagged
    ``same_ion``. A Stage A row was matched rather than elected, so it arrives
    with none, and the question they answer is as open on it: the ion a list's
    formula makes through one mechanism is, to the same mass and envelope,
    another neutral's through another. This writes the family the untargeted
    search would have given that ion
    (``heuristic_filter.propose_same_ion_readings``) the way an election's
    displaced readings are written: ahead of the row's scored rivals, and
    carrying the row's own fit and mass error, since the ion and so the
    measurement is the same.

    A rival already on the row that restates a reading - the same neutral
    through the same mechanism, which the known set can hold as a compound of
    its own - is that reading, so it is flagged and moved ahead rather than
    repeated.

    The target library's rows get none. Their formulas are the workspace's own
    curation (:func:`is_target_library_row`), where a list's is a prior matched
    against every sample. Only a monoisotopic row gets them, as in an election:
    an isotopologue is its owner's ion on another line.

    :param assignments: Every row built for this sample; mirror rows are
        modified in place.
    :param mechanism_id_by_notation: The mechanisms the run searched, in the
        finder's notation, mapped to their ids.
    :param search_config: The untargeted search's configuration, whose element
        box a reading has to sit in.
    :param heuristics_config: The untargeted search's heuristic filter.
    :param formula_formatter: How the untargeted stage writes a formula.
    :param max_alternatives: Cap on stored alternatives per row.
    :return: How many mirror rows carry at least one reading.
    """
    notation_by_id = {
        str(mechanism_id): notation
        for notation, mechanism_id in mechanism_id_by_notation.items()
    }
    format_formula = formula_formatter or (lambda formula: formula)
    rows = [
        row
        for row in assignments
        if is_reference_mirror_row(row)
        and row.get("role") == ROLE_M0
        and row.get("assigned_formula")
        and str(row.get("ionization_mechanism_id")) in notation_by_id
    ]
    families = propose_same_ion_readings(
        [
            (
                str(row["assigned_formula"]),
                notation_by_id[str(row["ionization_mechanism_id"])],
            )
            for row in rows
        ],
        list(mechanism_id_by_notation),
        search_config,
        heuristics_config,
    )
    carrying = 0
    for row, family in zip(rows, families, strict=True):
        if not family:
            continue
        rivals = list(row.get("alternatives") or [])
        readings = []
        for member in family:
            mechanism_id = mechanism_id_by_notation[member["ionization_mechanism"]]
            counts = element_counts(member["formula"])
            restated = next(
                (
                    index
                    for index, rival in enumerate(rivals)
                    if str(rival.get("ionization_mechanism_id")) == str(mechanism_id)
                    and element_counts(str(rival.get("assigned_formula") or ""))
                    == counts
                ),
                None,
            )
            if restated is not None:
                readings.append({**rivals.pop(restated), "same_ion": True})
                continue
            readings.append(
                {
                    "assigned_formula": format_formula(member["formula"]),
                    "ion_formula": row.get("ion_formula"),
                    "ionization_mechanism_id": mechanism_id,
                    "isotope_label": row.get("isotope_label"),
                    "fit_score": row.get("fit_score"),
                    "mz_error_ppm": row.get("mz_error_ppm"),
                    "plausibility": round(
                        float(formula_plausibility(member["formula"])), 4
                    ),
                    "same_ion": True,
                    "source": SOURCE_DATABASE,
                }
            )
        row["alternatives"] = (readings + rivals)[: max_alternatives or 0] or None
        carrying += 1
    return carrying


#: The provenance block a list hit carries once the formula search has been
#: asked about its peak: the grid's rivals its density counts.
GRID_RIVALS = "grid_rivals"

#: The provenance block a search row carries where it took a list hit's peak:
#: the list reading it displaced and the evidence the two were weighed on.
LIST_READING = "list_reading"

#: How much a list reading's evidence counts for against the formula search's.
#: A rival takes a list hit's peak only where its evidence (fit times
#: plausibility) exceeds twice the reading's, and then by more than a tie (the
#: plan owner's answer on step 2.5f). Measured before it was taken, it lets the
#: grid take the peaks of readings the run already held below assignability,
#: and none on which the reference commits the list's formula.
LIST_PRIOR_WEIGHT = 2.0

#: How many of those rivals a row keeps by name. The count is on the row in
#: full; the names are for a reader, as a row's alternatives are.
GRID_RIVALS_KEPT = 5


def record_grid_rivals(rows: list[dict], measured: list) -> dict:
    """Count the formula search's rivals into each list hit's density.

    A list hit's density counts the known set's formulas its evidence could not
    separate (:func:`invert_matches_to_peak_assignments`). The grid holds
    formulas for the same mass that the known set never proposed, and an
    election's density counts those. This adds the ones the list hit's
    evidence cannot separate either, less any the known set already counted
    (its rows' formulas are the row's alternatives), so the density rule reaches
    a list hit as it reaches an election.

    :param rows: The Stage A monoisotopic rows that were measured, modified in
        place.
    :param measured: What :func:`finder.rivals_of_readings` found for each, in
        the same order; None where it could not measure a row.
    :return: What the run records: how many rows were measured, and how many
        the grid added a rival to.
    """
    with_rivals = 0
    for row, found in zip(rows, measured, strict=True):
        if found is None:
            continue
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
            row["provenance"] = provenance
        known = {
            formula_identity(alternative.get("assigned_formula"))
            for alternative in row.get("alternatives") or []
        }
        added = [
            rival
            for rival in found.rivals
            if formula_identity(rival["formula"]) not in known
        ]
        known_density = provenance.get(CANDIDATE_DENSITY)
        known_density = known_density if isinstance(known_density, int) else 1
        provenance[GRID_RIVALS] = {
            "known_density": known_density,
            "added": len(added),
            "rivals": [
                {
                    "formula": rival["formula"],
                    "ion_formula": rival["ion"],
                    "ionization_mechanism": rival["ionization_mechanism"],
                    "fit_score": round(float(rival["fit_score"]), 4),
                    "mz_error_ppm": (
                        None
                        if rival["mz_error_ppm"] is None
                        else round(float(rival["mz_error_ppm"]), 4)
                    ),
                }
                for rival in added[:GRID_RIVALS_KEPT]
            ],
            # The row's fit on the search's own scale, beside which the rivals
            # were counted.
            "fit_score": round(float(found.fit_score), 4),
            "grid_candidates": found.candidates,
            # Uniqueness is relative to the searched box: a formula outside it
            # only meets the rivals the box builds.
            "in_grid": found.in_grid,
            # The rival that cleared the list's prior and still did not take the
            # peak, and why.
            **(
                {"held_against": _held_against_record(found.held_against)}
                if found.held_against
                else {}
            ),
        }
        provenance[CANDIDATE_DENSITY] = known_density + len(added)
        if added:
            with_rivals += 1
    return {
        "measured": sum(1 for found in measured if found is not None),
        "with_rivals": with_rivals,
    }


def _held_against_record(held: dict) -> dict:
    """The rival a list hit kept its peak against, as the row records it."""
    return {
        "formula": held["formula"],
        "ion_formula": held["ion"],
        "ionization_mechanism": held["ionization_mechanism"],
        "fit_score": round(float(held["fit_score"]), 4),
        "prior": held["prior"],
        "why": held["why"],
        "unexplained_lines": [round(float(mz), 5) for mz in held["unexplained_lines"]],
    }


def list_readings(
    stage_a_assignments: list[dict],
    peaks_df: pd.DataFrame,
    notation_by_id: dict[str, str],
) -> dict[str, ListReading]:
    """The list readings the formula search is asked about, by row id.

    Every monoisotopic Stage A row whose channel the search runs, at its peak's
    m/z as the search's own frame holds it, so the search can key the reading
    on the value it walks. A compound of the sample's own target library keeps
    its peak whatever the grid holds, and its rivals are still counted.

    :param stage_a_assignments: Stage A's rows.
    :param peaks_df: The frame the search is handed, with ``sample_peak_id``.
    :param notation_by_id: The searched mechanisms, by id.
    :return: Row id -> the reading.
    """
    mz_by_peak = dict(
        zip(
            peaks_df["sample_peak_id"].astype(str),
            peaks_df["mz"].astype(float),
            strict=True,
        )
    )
    readings: dict[str, ListReading] = {}
    for row in stage_a_assignments:
        notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        mz = mz_by_peak.get(str(row.get("sample_peak_id")))
        if (
            row.get("role") != ROLE_M0
            or not row.get("assigned_formula")
            or notation is None
            or mz is None
        ):
            continue
        readings[str(row["peak_assignment_id"])] = ListReading(
            mz=mz,
            formula=str(row["assigned_formula"]),
            ionization_mechanism=notation,
            mz_error_ppm=_float_or_none(row.get("mz_error_ppm")),
            keeps_peak=is_target_library_row(row),
        )
    return readings


@dataclass
class ListElection:
    """How the formula search settled the peaks the lists had read.

    :param stage_a: Stage A's rows less the list hits a rival took and their
        isotopologues; the kept hits carry the grid's rivals.
    :param matches: The search's rows less the markers of kept readings.
    :param released: The peaks a rival took, with the lines of the list hits it
        took them from, which the search's rows may now hold.
    :param displaced: Peak id -> the list hit a rival took it from, and the
        weighing.
    :param summary: What the run records under its search scope.
    """

    stage_a: list[dict]
    matches: pd.DataFrame
    released: set[str]
    displaced: dict[str, tuple[dict, dict]]
    summary: dict


def settle_list_election(
    stage_a_assignments: list[dict],
    matches_df: pd.DataFrame,
    readings: dict[str, ListReading],
) -> ListElection:
    """Apply what the search decided on the peaks the lists had read.

    A kept reading leaves its row as Stage A built it, with the grid's rivals
    counted into its density (:func:`record_grid_rivals`), and the run counts
    the readings a rival cleared the prior against and still did not displace:
    for the lines it left unexplained, or because the reading is the target
    library's. A reading a rival beat leaves the ledger with its isotopologues,
    and their peaks go to the search's rows.

    :param stage_a_assignments: Stage A's rows.
    :param matches_df: The search's result, with the known-peak markers.
    :param readings: The readings the search was asked about, by row id.
    :return: The settled rows and what the run records.
    """
    row_by_id = {str(row["peak_assignment_id"]): row for row in stage_a_assignments}
    row_id_by_mz = {reading.mz: row_id for row_id, reading in readings.items()}
    kept_rows: list[dict] = []
    kept_measured: list = []
    displaced: dict[str, tuple[dict, dict]] = {}
    commits = matches_df
    if not matches_df.empty and KNOWN_KEPT in matches_df.columns:
        # A frame holds a missing value on the rows that are not markers, and
        # numpy's bool where every row is one.
        is_kept = matches_df[KNOWN_KEPT].eq(True)
        for _, result in matches_df[is_kept].iterrows():
            row = row_by_id.get(row_id_by_mz.get(float(result["mz"]), ""))
            if row is not None:
                found = result.get(KNOWN_RIVALS)
                kept_rows.append(row)
                # A reading the search could not read was not measured.
                kept_measured.append(
                    found if isinstance(found, ReadingRivals) else None
                )
        commits = matches_df[~is_kept]
    if not commits.empty and KNOWN_DISPLACED in commits.columns:
        for _, result in commits.iterrows():
            weighing = result.get(KNOWN_DISPLACED)
            if not isinstance(weighing, dict):
                continue
            row = row_by_id.get(row_id_by_mz.get(float(weighing["peak_mz"]), ""))
            if row is not None:
                displaced[str(row["sample_peak_id"])] = (row, dict(weighing))
    recorded = record_grid_rivals(kept_rows, kept_measured)
    held = Counter(
        found.held_against["why"]
        for found in kept_measured
        if found is not None and found.held_against
    )
    taken_ids = {str(row["peak_assignment_id"]) for row, _ in displaced.values()}
    released = set(displaced)
    stage_a = []
    for row in stage_a_assignments:
        owner = str(row.get("owner_peak_assignment_id") or "")
        if str(row["peak_assignment_id"]) in taken_ids or owner in taken_ids:
            released.add(str(row["sample_peak_id"]))
            continue
        stage_a.append(row)
    return ListElection(
        stage_a=stage_a,
        matches=commits,
        released=released,
        displaced=displaced,
        summary={
            "measured": recorded["measured"] + len(displaced),
            "with_rivals": recorded["with_rivals"],
            "taken_by_rivals": len(displaced),
            "kept_by_lines": held[HELD_BY_LINES],
            "kept_by_library": held[HELD_BY_LIBRARY],
            "prior": LIST_PRIOR_WEIGHT,
        },
    )


#: On an alternative, what says it is the reading an isotopologue claim took
#: the peak from (:mod:`envelope_claims`).
DISPLACED_BY_CLAIM = "displaced_by_claim"

#: On an alternative, what says it is the list reading a rival took the peak
#: from (:func:`settle_list_election`).
DISPLACED_BY_RIVAL = "displaced_by_rival"


def reading_as_alternative(row: dict, displaced_by: str) -> dict:
    """What a row said, shaped as an alternative of the row that took its peak.

    The inspector lists it with the row's other readings, and promoting it by
    hand commits it again, with its compound.

    :param row: The row whose reading was displaced.
    :param displaced_by: What tells a reader this is the reading something took
        the peak from, rather than a rival the peak's own election considered:
        :data:`DISPLACED_BY_CLAIM` or :data:`DISPLACED_BY_RIVAL`.
    """
    provenance = row.get("provenance") or {}
    return {
        "assigned_formula": row.get("assigned_formula"),
        "ion_formula": row.get("ion_formula"),
        "ionization_mechanism_id": row.get("ionization_mechanism_id"),
        "isotope_label": row.get("isotope_label"),
        "target_compound_id": row.get("target_compound_id"),
        "target_ion_id": row.get("target_ion_id"),
        "fit_score": row.get("fit_score"),
        "mz_error_ppm": row.get("mz_error_ppm"),
        "plausibility": provenance.get("plausibility"),
        "source": row.get("source"),
        displaced_by: True,
        **(
            {REFERENCE_IDENTITIES_COL: provenance[REFERENCE_IDENTITIES_COL]}
            if provenance.get(REFERENCE_IDENTITIES_COL)
            else {}
        ),
    }


def record_displaced_list_readings(
    stage_b_assignments: list[dict],
    displaced: dict[str, tuple[dict, dict]],
    max_alternatives: int,
) -> int:
    """Give each search row that took a list hit's peak the reading it displaced.

    The reading goes first among the row's alternatives, and the weighing into
    :data:`LIST_READING`: the list's formula, its identities, and the evidence
    the two readings were weighed on with the prior.

    :return: How many of the taken peaks a search row holds.
    """
    holding = 0
    for assignment in stage_b_assignments:
        taken = displaced.get(str(assignment.get("sample_peak_id")))
        if taken is None or assignment.get("role") != ROLE_M0:
            continue
        row, weighing = taken
        provenance = assignment.setdefault("provenance", {})
        row_provenance = row.get("provenance") or {}
        provenance[LIST_READING] = {
            "assigned_formula": row.get("assigned_formula"),
            "ion_formula": row.get("ion_formula"),
            "source": row.get("source"),
            "target_compound_id": row.get("target_compound_id"),
            # The tier the reading's evidence gave it. The election settles on
            # the rows as Stage A built them, before any rule of the run judges
            # the ledger, so a reading the run would have held at candidate can
            # read assigned here.
            "tier": row.get("tier"),
            "evidence": round(float(weighing["evidence"]), 4),
            "fit_score": round(float(weighing["fit_score"]), 4),
            "rival_evidence": round(float(weighing["rival_evidence"]), 4),
            "prior": weighing["prior"],
            **(
                {REFERENCE_IDENTITIES_COL: row_provenance[REFERENCE_IDENTITIES_COL]}
                if row_provenance.get(REFERENCE_IDENTITIES_COL)
                else {}
            ),
        }
        assignment["alternatives"] = [
            reading_as_alternative(row, DISPLACED_BY_RIVAL),
            *(assignment.get("alternatives") or []),
        ][: max_alternatives or 0] or None
        holding += 1
    return holding


# Mapping a finder result back to the observed peak it came from is an IDENTITY join,
# not a mass match: `assign_compositions` copies the m/z straight out of the frame it was
# handed. The tolerance below exists only to survive a float32/float64 round trip or a
# decimal rounding inside the finder. At 0.1 ppm it sits well under the spacing of any two
# peaks a high-resolution instrument resolves (an Orbitrap peak is several ppm wide), so it
# cannot merge two real peaks - while a rounding change that would otherwise silently
# unassign the entire stage still lands on the right peak.
_PEAK_JOIN_REL_TOL = 1e-7
_PEAK_JOIN_ABS_TOL = 1e-9


def _resolve_peak_positions(
    peaks_df: pd.DataFrame, mz_values: list[float]
) -> list[int | None]:
    """Map result m/z values onto POSITIONS in ``peaks_df``, or None when none matches.

    Positional rather than value-keyed: a dict keyed on ``float(mz)`` loses one of two
    peaks that share a value, and turns any rounding inside the finder into a silent
    "no observed peak" for every row at once. Resolving to the row's position keeps the
    two peaks distinct (each value is matched to an as-yet-unclaimed position before a
    claimed one) and makes a failure to match visible to the caller, which logs it.
    """
    peak_mz = peaks_df["mz"].to_numpy(dtype=float)
    order = np.argsort(peak_mz, kind="stable")
    sorted_mz = peak_mz[order]

    claimed: set[int] = set()
    positions: list[int | None] = []
    for value in mz_values:
        tol = max(_PEAK_JOIN_ABS_TOL, abs(value) * _PEAK_JOIN_REL_TOL)
        lo = int(np.searchsorted(sorted_mz, value - tol, side="left"))
        hi = int(np.searchsorted(sorted_mz, value + tol, side="right"))
        if lo >= hi:
            positions.append(None)
            continue
        window = list(range(lo, hi))
        free = [k for k in window if int(order[k]) not in claimed]
        best = min(free or window, key=lambda k: abs(sorted_mz[k] - value))
        position = int(order[best])
        claimed.add(position)
        positions.append(position)
    return positions


def _untargeted_row_score(row) -> tuple[float, float | None, float | None]:
    """The row's fit score plus the mass / abundance errors it was derived from.

    Prefers the isotope-pattern fit score mascope_tools already computed over the
    candidate's whole predicted envelope; falls back to the single-peak term only when no
    envelope was scored for this row.
    """
    # Fall back to composition_error_ppm when mz_error_ppm is absent OR present-but-NaN.
    # dict.get's default only covers the absent case, so a NaN in a mixed batch (some rows
    # carry an isotope-envelope mz error, some do not) would otherwise collapse the score.
    mz_error_ppm = _float_or_none(row.get("mz_error_ppm"))
    if mz_error_ppm is None:
        mz_error_ppm = _float_or_none(row.get("composition_error_ppm"))
    abundance_error = _float_or_none(row.get("intensity_error"))

    score = _score_or_none(row.get("isotopic_pattern_score"))
    if score is None:
        mz_term = (
            max(0.0, 1.0 - 1e-2 * abs(mz_error_ppm))
            if mz_error_ppm is not None
            else 0.0
        )
        abundance_term = (
            1.0 - min(1.0, abs(abundance_error)) if abundance_error is not None else 1.0
        )
        score = abundance_term * mz_term
    return score, mz_error_ppm, abundance_error


def _seed_of(
    row, mechanism_id_by_notation: dict[str, str], format_formula
) -> tuple | None:
    """The ``(formula, mechanism id)`` a finder row commits to, or None.

    The key both halves of the seeded re-score are indexed by, written once so
    the list that is measured and the lookup that reads the result cannot drift:
    the formula in the form it is STORED in (the formatter is what turns an
    explicit-isotope string into the custom-element notation the target library
    speaks), and the mechanism as an id rather than as a notation.
    """
    formula = row.get("formula")
    if not isinstance(formula, str) or formula in (
        UNTARGETED_NO_MATCH,
        UNTARGETED_IONIZATION,
    ):
        return None
    mechanism_id = mechanism_id_by_notation.get(
        _str_or_none(row.get("ionization_mechanism"))
    )
    if not mechanism_id:
        return None
    return (format_formula(formula), mechanism_id)


def untargeted_seeds(
    matches_df: pd.DataFrame,
    mechanism_id_by_notation: dict[str, str] | None = None,
    formula_formatter=None,
) -> set[tuple[str, str]]:
    """The formula x mechanism list the finder's result commits to.

    What the seeded re-score measures against the sample's own peaks, so that
    the fit a Stage B row is tiered on is the fit a Stage A row would have
    earned for the same ion: one ``compute_match_isotopes`` pass, the sample's
    match-params gating, the ion-level v2 fit with the file's own per-peak
    signal-to-noise. The finder's ranking is a different measurement - it works
    off the peak list, scores the envelope it predicted for ranking, and its
    job is to decide which reading of a peak wins.

    Every committed row is seeded, not only the M0 rows: an isotopologue's ion
    is a hypothesis about the peak it sits on, it can lose that peak to another
    reading, and the loser is kept as an alternative whose fit a reader compares
    against the winner's. Both have to be on one scale for that comparison.

    :param matches_df: First element returned by ``assign_compositions``.
    :param mechanism_id_by_notation: Maps the search's notations to mechanism
        ids; a row whose notation is not in it cannot be seeded.
    :param formula_formatter: Applied to formulas, as in the conversion.
    :return: Distinct ``(formula, ionization_mechanism_id)`` pairs.
    """
    if matches_df.empty:
        return set()
    mechanism_id_by_notation = mechanism_id_by_notation or {}
    format_formula = formula_formatter or (lambda formula: formula)
    seeds = {
        _seed_of(row, mechanism_id_by_notation, format_formula)
        for _, row in matches_df.iterrows()
    }
    return {seed for seed in seeds if seed is not None}


def _same_ion_family(row) -> list[dict]:
    """The readings of this row's own ion that the finder's policy displaced.

    Same ion means same mass and same predicted envelope, so these are not
    runners-up that scored lower - they are the winner's evidence read as a
    different split between the analyte and the mechanism, and the spectrum
    cannot say which split is right. The finder writes them on the M0 row only;
    an isotopologue is owned by that row.

    :param row: One row of the finder's result frame.
    :return: The displaced readings, empty when the row carries none (the
        column is absent entirely on a run where no peak had a family, and
        null on the rows that did not).
    """
    family = row.get(SAME_ION_ALTERNATIVES)
    if not isinstance(family, list):
        return []
    return [member for member in family if isinstance(member, dict)]


def untargeted_matches_to_peak_assignments(
    matches_df: pd.DataFrame,
    peaks_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
    candidate_threshold: float,
    assigned_threshold: float,
    mechanism_id_by_notation: dict[str, str] | None = None,
    formula_formatter=None,
    max_alternatives: int = 5,
    minor_channels: frozenset[str] | None = None,
    excluded_peak_ids: set[str] | None = None,
    fit_by_seed: dict[tuple[str, str], float | None] | None = None,
) -> list[dict]:
    """Map untargeted composition results onto peak assignments (Stage B).

    `assign_compositions` already yields one row per input peak with the best
    composition, isotope labelling, and runner-up formulas; this converts
    those rows into the persisted PeakAssignment shape. Rows with the '---'
    placeholder are skipped (their peaks stay unassigned).

    One fit, computed twice on two frames, and the row carries the second. The
    finder's ``isotopic_pattern_score`` decides which READING of a peak wins:
    the v2 fit of a candidate's predicted envelope against the peak list, on
    every candidate of every searched peak. What a committed row is TIERED on is
    ``fit_by_seed`` - the same ion, the same fit, measured again through the
    full match path, one ``compute_match_isotopes`` pass over the sample with
    the run's match-params gating, which is how a Stage A row is measured.
    Reading the tier off that is what puts the two stages' evidence on one
    scale. Only one number reaches the row (decision 12): a second score on it
    would name a version that no longer differs.

    Without a seeded fit for a row's ion the finder's own number stands. When no
    envelope was scored either (the column is absent/NaN) it falls back to the
    legacy single-peak maths
    ``score = (1 - min(1, |intensity_error|)) * max(0, 1 - |mz_error_ppm|/100)``.

    Two results can land on the same observed peak - typically one composition's isotope
    child on another composition's M0 - and only one may own it. The contest is settled by
    ``evidence = fit x plausibility``, the same currency Stage A arbitrates in, and the
    loser is kept as an alternative on the winner rather than dropped: a peak that two
    compositions explain is exactly the peak an analyst needs to see both explanations for.

    A third kind of alternative arrives already decided. Compositions that make the
    SAME ion are ranked in the finder, by the policy that the mechanism carrying the
    mass is the reading (``heuristic_filter.elect_same_ion_families``); this stores
    the readings it displaced, flagged ``same_ion``, so the ledger records that the
    split between analyte and adduct was a choice and says what the alternative was.
    That policy is not re-run here, and this function's own contest does not re-rank
    it.

    An isotopologue row is written by the monoisotopic row that claims it and names
    that row as its owner from the start. It is never linked to a parent afterwards,
    and when the ion's M0 wins no peak of its own it is not written at all: an
    isotopologue row that belongs to nothing states that a peak is part of an envelope
    whose ion the ledger never commits, which is not a verdict a reader can act on. Its
    peak stays unassigned instead, which is what it is.

    :param matches_df: First element returned by assign_compositions.
    :param peaks_df: The observed peaks the search results are joined back to, by
        position (see :func:`_resolve_peak_positions`), with ``sample_peak_id`` / ``mz``
        / ``intensity`` columns. This is the sample's whole peak list, not only the
        peaks that were enumerated: the finder scores an envelope against every peak it
        is given, so an isotopologue lands wherever it sits rather than only inside
        the searched set.
    :param fit_by_seed: The seeded re-score's fit per ``(formula, mechanism id)``,
        from :func:`untargeted_seeds` measured through ``score_seeds``. Optional:
        a caller that cannot run a match pass (a unit test, a path with no
        sample file) gets the finder's own score on every row instead, which is
        the behaviour this had before the re-score existed.
    :param excluded_peak_ids: Peaks another pass already owns - the reagent and artifact
        pre-passes, and Stage A. Rows landing on them are dropped rather than written:
        the ledger holds one row per peak, and a stage that arrives second does not get
        to restate a peak somebody else has already accounted for. Dropping the M0 this
        way takes its isotopologues with it, by the rule above.
    :param mechanism_id_by_notation: Maps the ionization notation used in the
        search back to IonizationMechanism ids.
    :param formula_formatter: Optional callable applied to formulas (e.g.
        explicit-isotope to custom element notation conversion).
    :param minor_channels: Notations of the opportunistic secondary channels
        this run switched on. They are searched beside the mode's own
        mechanisms but are not equal to them: they only take peaks no primary
        channel explains, and a winner on one is capped at ``candidate`` unless
        something corroborates it (see :func:`_apply_minor_channel_policy`).
    :return: One assignment dict per assigned peak, ready for bulk insert.
    """
    if matches_df.empty or peaks_df.empty:
        return []

    mechanism_id_by_notation = mechanism_id_by_notation or {}
    minor_channels = minor_channels or frozenset()
    excluded_peak_ids = excluded_peak_ids or set()
    format_formula = formula_formatter or (lambda formula: formula)

    assigned_rows = [
        row
        for _, row in matches_df.iterrows()
        if isinstance(row.get("formula"), str)
        and row.get("formula") not in (UNTARGETED_NO_MATCH, UNTARGETED_IONIZATION)
    ]
    if not assigned_rows:
        return []
    positions = _resolve_peak_positions(
        peaks_df, [float(row["mz"]) for row in assigned_rows]
    )
    peak_ids = peaks_df["sample_peak_id"].to_numpy()
    peak_intensities = peaks_df["intensity"].to_numpy(dtype=float)

    # Contenders per observed peak, in the order the finder emitted them. Insertion order
    # is preserved so the assignments come back in the finder's (m/z-sorted) order even
    # though the winner within a peak is chosen by evidence.
    contenders_by_position: dict[int, list[dict]] = {}
    unmatched_m0 = 0
    unmatched_children = 0
    claimed_elsewhere = 0
    for row, position in zip(assigned_rows, positions):
        isotope_label = _str_or_none(row.get("isotope_label")) or "M0"
        if position is not None and str(peak_ids[position]) in excluded_peak_ids:
            # The peak belongs to a pass that ran before this one. It is still
            # pattern context - the finder scored envelopes against it, which is
            # the point of feeding the whole peak list in - but no row here may
            # restate it.
            claimed_elsewhere += 1
            continue
        if position is None:
            # An isotope child can legitimately land on an m/z outside the peaks fed to the
            # search (e.g. below the intensity threshold), so a child miss is expected. An
            # M0 came straight out of that peak list, so a missed M0 means the join itself
            # broke - which would otherwise unassign the whole stage in silence.
            if isotope_label == "M0":
                unmatched_m0 += 1
            else:
                unmatched_children += 1
            continue
        formula = str(row["formula"])
        score, mz_error_ppm, abundance_error = _untargeted_row_score(row)
        plausibility = round(float(formula_plausibility(formula)), 4)
        seed = _seed_of(row, mechanism_id_by_notation, format_formula)
        seeded_fit = (fit_by_seed or {}).get(seed) if seed is not None else None
        contenders_by_position.setdefault(position, []).append(
            {
                "row": row,
                "formula": formula,
                "isotope_label": isotope_label,
                "fit": _score_or_none(seeded_fit if seeded_fit is not None else score)
                or 0.0,
                "score": score,
                "plausibility": plausibility,
                "mz_error_ppm": mz_error_ppm,
                "abundance_error": abundance_error,
                "minor": _str_or_none(row.get("ionization_mechanism"))
                in minor_channels,
            }
        )
    if unmatched_m0:
        runtime.logger.warning(
            f"Untargeted stage: {unmatched_m0} of {len(assigned_rows)} composition rows "
            f"could not be joined back to an observed peak (M0 rows, which come from the "
            f"peak list itself). Those peaks stay unassigned - check whether the "
            f"composition finder is rounding or re-typing the m/z it was given."
        )
    if unmatched_children:
        runtime.logger.debug(
            f"Untargeted stage: {unmatched_children} isotope-child rows fell outside the "
            f"peaks fed to the search and were skipped."
        )
    if claimed_elsewhere:
        runtime.logger.debug(
            f"Untargeted stage: {claimed_elsewhere} composition rows landed on peaks an "
            f"earlier pass had already claimed and were dropped."
        )

    # Every row in finder order, each child carrying the group whose M0 has to
    # own it. Two passes rather than one: an ion's M0 is not necessarily the
    # first of its rows to appear - for a bromine- or chlorine-rich envelope the
    # finder reports the most abundant isotopologue first, and the monoisotopic
    # line can sit at a lower m/z than an isotopologue already seen - so which
    # children have an owner is only known once every peak has been settled.
    ordered: list[tuple[dict, tuple | None]] = []
    m0_assignment_by_group: dict[tuple, str] = {}

    for position, contenders in contenders_by_position.items():
        # Same ranking as Stage A: evidence first, closest mass next, formula last so a
        # dead heat resolves by the data rather than by the finder's row order.
        #
        # An opportunistic secondary channel loses this contest on equal evidence: a
        # primary-channel isotope child beats a secondary-channel M0 for the same
        # observed peak. Which READING of a peak wins - X.[M+NH4]+ against
        # (X+NH3).[M+H]+, the same ion split two ways - is not decided here and must
        # not be: the finder ranks that hypothesis family under the same-ion policy,
        # where the mechanism carrying the mass is what settles it. This rule and that
        # one do not re-rank each other.
        contenders.sort(
            key=lambda c: (
                -round(c["fit"] * c["plausibility"], 4),
                c["minor"],
                abs(c["mz_error_ppm"])
                if c["mz_error_ppm"] is not None
                else float("inf"),
                c["formula"],
            )
        )
        winner, losers = contenders[0], contenders[1:]
        row = winner["row"]
        formula = winner["formula"]
        isotope_label = winner["isotope_label"]
        is_m0 = isotope_label == "M0"
        notation = _str_or_none(row.get("ionization_mechanism"))
        group_key = (formula, notation)

        # Losing contenders first: they are scored competitors for this actual peak. The
        # finder's other_candidates are formula names only (no per-candidate fit or mass
        # error), but chemical plausibility is computable from the formula itself, so the
        # inspector can still rank them.
        #
        # Both sources are screened against the winner, because neither can be trusted to
        # have left it out. A loser reaching the same formula through the same mechanism is
        # the winner's own hypothesis arriving twice; and the finder's shortlist is frozen
        # before the heuristic filter and the isotope-pattern ranking pick the winner, so
        # older results still name the winning formula among the "other" candidates. Both
        # would render as the committed assignment listed among its own close alternatives.
        # Screening happens before the cap so a duplicate cannot displace a real rival.
        alternatives = [
            {
                "assigned_formula": format_formula(loser["formula"]),
                "ion_formula": _str_or_none(loser["row"].get("ion")),
                # As in Stage A: the adduct the runner-up was scored under, so
                # promoting it by hand yields a complete assignment rather than
                # a formula with no mechanism.
                "ionization_mechanism_id": mechanism_id_by_notation.get(
                    _str_or_none(loser["row"].get("ionization_mechanism"))
                ),
                "isotope_label": loser["isotope_label"],
                # The same measurement the winner's is, so a reader comparing
                # the two is comparing like with like.
                "fit_score": _score_or_none(loser["fit"]),
                "mz_error_ppm": loser["mz_error_ppm"],
                "plausibility": loser["plausibility"],
                "source": SOURCE_UNTARGETED,
            }
            for loser in losers
            if (
                loser["formula"],
                _str_or_none(loser["row"].get("ionization_mechanism")),
            )
            != (formula, notation)
        ]
        # The other readings of this same ion, ahead of the scored rivals: a
        # candidate that TIED on the evidence explains the peak at least as well
        # as one that lost on it, so it is the first alternative worth seeing
        # when the cap bites. Its fit and mass error are the winner's own -
        # identical ion, identical envelope - and the flag is what tells a reader
        # this is the same measurement split differently rather than a weaker
        # hypothesis.
        family = _same_ion_family(row)
        alternatives = [
            {
                "assigned_formula": format_formula(str(member.get("formula") or "")),
                "ion_formula": _str_or_none(member.get("ion")),
                "ionization_mechanism_id": mechanism_id_by_notation.get(
                    _str_or_none(member.get("ionization_mechanism"))
                ),
                "isotope_label": isotope_label,
                "fit_score": _score_or_none(winner["fit"]),
                "mz_error_ppm": winner["mz_error_ppm"],
                "plausibility": round(
                    float(formula_plausibility(str(member.get("formula") or ""))), 4
                ),
                "same_ion": True,
                "source": SOURCE_UNTARGETED,
            }
            for member in family
        ] + alternatives
        other_candidates = _str_or_none(row.get("other_candidates"))
        if other_candidates:
            # Formula-only entries, all drawn from this peak's own composition search:
            # one naming the winning formula IS the winner, not a rival mechanism, and
            # one naming a same-ion reading is that reading stripped of everything the
            # entry above already says about it.
            named = {formula} | {str(member.get("formula") or "") for member in family}
            alternatives.extend(
                {
                    "assigned_formula": format_formula(alt.strip()),
                    "plausibility": round(float(formula_plausibility(alt.strip())), 4),
                    "source": SOURCE_UNTARGETED,
                }
                for alt in other_candidates.split(",")
                if alt.strip() and alt.strip() not in named
            )
        alternatives = alternatives[: max_alternatives or 0] or None

        # Chemical plausibility (Seven Golden Rules) is stage-agnostic and the headline
        # provenance metric, so the untargeted winner reports it on the same footing as a
        # database winner (Stage A above). Evidence -- the same fit x plausibility product
        # -- rides along for a different reason: it is the quantity `create_verification`
        # snapshots as a verdict's calibration label, and a Stage B label whose evidence is
        # null can never be fit. It is indistinguishable from a verdict nobody recorded, in
        # the one table no re-run can rebuild, so the number is captured now even though
        # nothing fits it yet.
        #
        # Confidence and P(correct) stay database-arbitration concepts, but for
        # one reason now rather than two. Confidence needs the peak's full scored
        # candidate set, which the untargeted search does not expose here
        # (other_candidates carries formulas only, no per-candidate fit). The
        # scale objection to P(correct) is gone: the fit below is the seeded
        # re-score, the same ion_score_v2 the Stage A curve was fitted on, so
        # the curve would no longer be borrowed across scales. Whether a Stage B
        # row should carry a probability is the confidence layer's call and not
        # this conversion's, so nothing here starts asserting one.
        evidence = round(winner["fit"] * winner["plausibility"], 4)
        provenance = {
            "plausibility": winner["plausibility"],
            "evidence": evidence,
            # True of this row rather than aspirational: the finder elected it
            # with the v2 fit and the re-score below measured it with the same
            # one, so there is no second number to name (decision 12).
            "score_version": SCORE_VERSION,
        }
        # What the finder's detectability gate judged this reading's absent
        # lines against. Recorded because it is the difference between a fit
        # scored against the noise and one scored against abundance alone, and
        # nothing else on the row says which happened.
        base_snr = _float_or_none(row.get(PATTERN_BASE_SNR))
        if base_snr is not None:
            provenance["base_snr"] = round(base_snr, 2)
        # How many hypotheses this peak's own evidence could not separate, from
        # the finder's full candidate list. Recorded rather than re-derived: the
        # row keeps at most `max_alternatives` of the competitors, so a reader
        # counting those counts the cap. Absent on an isotopologue, which was
        # predicted from the winner rather than searched.
        density = row.get(CANDIDATE_DENSITY)
        if density is not None and not pd.isna(density):
            provenance[CANDIDATE_DENSITY] = int(density)
        for key in ("neutral_mass", "unsaturation"):
            value = _float_or_none(row.get(key))
            if value is not None:
                provenance[key] = value

        assignment = {
            "peak_assignment_id": gen_id(32),
            "peak_assignment_run_id": peak_assignment_run_id,
            "sample_item_id": sample_item_id,
            "sample_peak_id": str(peak_ids[position]),
            "sample_peak_mz": float(row["mz"]),
            "sample_peak_intensity": float(peak_intensities[position]),
            "sample_peak_tof": None,
            "role": ROLE_M0 if is_m0 else ROLE_ISO_CHILD,
            "assigned_formula": format_formula(formula),
            "ion_formula": _str_or_none(row.get("ion")),
            "ionization_mechanism_id": mechanism_id_by_notation.get(notation),
            "isotope_label": isotope_label,
            "isotope_formula": fit_isotope_formula(row.get("isotope_formula")),
            "source": SOURCE_UNTARGETED,
            "fit_score": _score_or_none(winner["fit"]),
            "mz_error_ppm": winner["mz_error_ppm"],
            "abundance_error": winner["abundance_error"],
            # The same evidence the contest above was settled on, so the tier and
            # the arbitration agree - and, since the fit is the seeded re-score,
            # the same quantity a Stage A row is tiered on. The two stages were
            # on different scales while this one read the finder's envelope
            # score and that one read ion_score_v2; they are one scale now.
            "tier": tier_for_evidence(
                evidence,
                candidate_threshold=candidate_threshold,
                assigned_threshold=assigned_threshold,
            ),
            "target_compound_id": None,
            "target_ion_id": None,
            "owner_peak_assignment_id": None,
            "alternatives": alternatives,
            "provenance": provenance,
        }
        ordered.append((assignment, None if is_m0 else group_key))

        if is_m0:
            m0_assignment_by_group.setdefault(
                group_key, assignment["peak_assignment_id"]
            )

    assignments: list[dict] = []
    orphaned = 0
    for assignment, group_key in ordered:
        if group_key is None:
            assignments.append(assignment)
            continue
        owner = m0_assignment_by_group.get(group_key)
        if owner is None:
            orphaned += 1
            continue
        assignment["owner_peak_assignment_id"] = owner
        assignments.append(assignment)
    if orphaned:
        runtime.logger.info(
            f"Untargeted stage: {orphaned} isotopologue rows whose ion committed no "
            f"monoisotopic peak were not written; their peaks stay unassigned."
        )

    if minor_channels:
        _apply_minor_channel_policy(
            assignments, mechanism_id_by_notation, minor_channels
        )

    return assignments


#: The key an alternative carries when it is an opportunistic reading of the
#: row's ion that the sample did not bear out: set aside by the partner gate,
#: kept on the row for the reader, and no rival in the cross-channel pass.
PARTNER_GATE_UNMET = "unmet"

#: The key an alternative carries when the sample bore it out and bore the
#: row's own reading out decisively more strongly (:data:`PARTNER_MARGIN`):
#: kept on the row, since the sample did show its molecule, and read by the
#: cross-channel pass as settled by the stronger partner rather than as a
#: rival.
PARTNER_GATE_OUTWEIGHED = "outweighed"

#: The verdict a gated reading carries where its ion reads no other way and
#: the sample shows no partner: the row stays as the finder elected it, and
#: the minor-channel policy's cap holds.
PARTNER_GATE_KEPT = "no other reading of the ion"

#: How many times brighter the stronger of two partners committed at one tier
#: has to be for its reading to settle the ion. The stronger partner takes the
#: ion at any margin, since the sample shows that molecule more strongly; the
#: margin decides whether the other reading is still a doubt. On the certified
#: cylinder the benzyl and methylbenzyl cations are decided by 22 to 41 times
#: (toluene against C7H6, xylene against styrene) and C5H7+ by 7 to 10
#: (isoprene against C5H6): the better reading in every file, and under an
#: order of magnitude not a certain one, which is what the top tier is kept
#: for. Partners at different tiers settle it at any margin.
PARTNER_MARGIN = 10.0

#: How many rounds the gate walks the ledger. Every round judges every row
#: again against the partners the ledger holds at that moment - a reading's
#: partner is often a row the gate itself turned back to the mode's own
#: reading in an earlier round, or one it swapped back and so took away - and
#: the walk ends when a round changes nothing. Four rounds settled every
#: sample measured; the cap is a guard against a ledger with no settled
#: reading, not a budget, and the summary says when it was hit.
MAX_PARTNER_GATE_ROUNDS = 6

#: A tier's name by its rank, for the record of what decided a contest.
_TIER_BY_RANK = {rank: tier for tier, rank in TIER_RANK.items()}


def apply_partner_gates(
    assignments: list[dict],
    *,
    notation_by_id: dict[str, str],
    minor_channels: frozenset[str],
    partner_gated_channels: frozenset[str],
    tier_bands: dict[str, float] | None,
) -> dict:
    """Let an opportunistic reading stand only where the sample shows its neutral.

    One ion, read two ways, is one measurement split differently between the
    analyte and the mechanism, and the finder elects the reading whose
    mechanism carries the most mass. Through an opportunistic channel that
    prior is wrong as often as it is right: every deprotonated acid also reads
    as the molecule 46 Da lighter with formate, and the C11 acid the chamber
    dataset does not contain and the C10 acid it does are told apart by one
    fact only - whether the C10 product is seen through one of the mode's own
    channels. Tropylium is the same case in positive mode: protonated C7H6
    outweighs toluene less a hydride in the election, and toluene is what the
    sample shows through electron transfer.

    Runs over the judged ledger, both stages' rows together, because the
    partner is as often a reference list's row as the search's: on the
    chamber dataset most C10 products are Stage A matches, and a gate that
    read the search's rows alone left 142 of 240 pseudo-acids standing. It
    runs after the mass gate, so that a partner is a reading that gate left
    committed, and before the cross-channel pass, so that a reading it sets
    aside is no rival there. A batch search, which judges its rows by neither
    pass, reads it over its own rows (``batch_untargeted.gate_search_rows``).

    A partner is a monoisotopic row committed at candidate or better through
    one of the mode's own channels whose neutral is the reading's, compared as
    compositions (:func:`formula_identity`): a target library's ``CH3COOH``
    partners a formate reading of ``C2H4O2``.

    The partners are kept per neutral with how strongly each commits it - its
    tier, then the height of its peak - so the strongest is read off the
    ledger as it stands, current through every swap.

    For a monoisotopic row whose reading is through a partner-gated channel:

    - the reading stands where it has a partner. Where the minor-channel
      policy capped it for want of one among the search's own rows, the cap
      is lifted here and the row says the second channel corroborated it;
    - unless the family holds another reading through a gated channel whose
      neutral has a partner too, and a stronger one: committed at the higher
      tier, or at one tier on the brighter peak. That reading becomes the
      row's, swapped in as below, and the row names the reading it took the
      ion from (``took_from``). The election's prior for the heavier
      mechanism does not decide between two readings the sample bears out;
      how strongly it bears each out does. Only an opportunistic reading is
      contested this way: a reading through one of the mode's own channels
      keeps its formula;
    - otherwise the mode's own reading of the ion, where the family holds one
      whose neutral is a molecule, becomes the row's, keeping the fit and mass
      error that are the ion's, with its tier read off its own plausibility
      under the ceiling the mass gate recorded for the ion's line, which
      every reading of the ion shares (a lifted cap is held the same way,
      and the row then says the mass gate holds it); failing that, the
      opportunistic reading whose neutral the sample shows most strongly;
      failing that, the row stays as it is and the policy's cap holds -
      re-imposed where the partner that once lifted it is gone.

    The ledger is walked to a fixed point: every round judges every row
    against the partners the ledger holds at that moment, a swap changes the
    partners the next row sees at once, a row swapped to the mode's reading
    whose set-aside reading a later round bears out swaps back, and a row
    that stood on a partner a swap back took is judged again. The walk ends
    when a round changes nothing (:data:`MAX_PARTNER_GATE_ROUNDS`).

    Read against the partners the walk settled on, a row standing on a
    partner through a gated channel records every other reading of its ion
    the contest weighed and what decided it (``contest``): the partners'
    tiers and the ratio of their peaks. Where the tiers differ, or the
    stronger peak is :data:`PARTNER_MARGIN` times the other's or more, the
    other reading is marked :data:`PARTNER_GATE_OUTWEIGHED`, and the
    cross-channel pass reads the ion as settled by the stronger partner.
    Within the margin it is left unmarked: a rival whose molecule the sample
    also shows, which the cross-channel pass reads as the doubt it is.

    For every monoisotopic row, a same-ion reading through a gated channel
    whose neutral has no partner is marked :data:`PARTNER_GATE_UNMET`: a
    reference list's acid is not doubted for a formate reading nothing bore
    out. The opportunistic reading a swap displaced for want of a partner is
    marked the same way, kept on the row for the reader, and the
    cross-channel pass counts neither as a rival. A reading displaced with a
    partner of its own is not marked unmet, since the sample bore it out: the
    contest weighs it. The mode's own reading a swap back displaced is not
    marked either: it is a reading of the ion the sample settled, and the
    cross-channel pass records by what. An isotopologue follows its owner's
    reading, as it does everywhere.

    :param assignments: Both stages' rows, modified in place.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param minor_channels: The notations that are secondary in this run.
    :param partner_gated_channels: The secondary channels held to a partner.
    :param tier_bands: The run's evidence bands, for a swapped reading's tier.
    :return: A JSON-serializable summary of the ledger the walk left: rows
        standing on a partner, of them the caps lifted and the swaps undone,
        rows swapped to another reading, rows left to the cap, of them the
        caps re-imposed, rows held under the mass gate's ceiling, readings
        set aside, rows that weighed another partnered reading and of them
        the ones the contest moved, the readings outweighed and those within
        the margin, the rounds it took, and whether a round changed nothing
        before the cap.
    """
    summary = {
        "partnered": 0,
        "uncapped": 0,
        "swapped": 0,
        "swapped_back": 0,
        "kept": 0,
        "recapped": 0,
        "held": 0,
        "set_aside": 0,
        "contested": 0,
        "contest_swapped": 0,
        "outweighed": 0,
        "within_margin": 0,
        "rounds": 0,
        "settled": True,
    }
    if not partner_gated_channels:
        return summary
    minor_ids = {mid for mid, n in notation_by_id.items() if n in minor_channels}
    gated_ids = {
        mid for mid, n in notation_by_id.items() if n in partner_gated_channels
    }
    if not gated_ids:
        return summary
    bands = tier_bands or {}
    candidate_threshold = bands.get(TIER_CANDIDATE)
    assigned_threshold = bands.get(TIER_ASSIGNED)

    held: set[str] = set()

    def tier_of(evidence: float, row: dict, provenance: dict) -> str | None:
        """The tier the evidence earns, under the mass gate's ceiling."""
        if candidate_threshold is None or assigned_threshold is None:
            return None
        tier = tier_for_evidence(
            evidence,
            candidate_threshold=candidate_threshold,
            assigned_threshold=assigned_threshold,
        )
        # The mass gate judged the ion's line, which every reading of it
        # shares. Where its ceiling binds, the row's tier is that gate's
        # word, and the row says so.
        mass_gate = provenance.get("mass_gate") or {}
        ceiling = mass_gate.get("ceiling") or mass_gate.get("capped")
        if ceiling in TIER_RANK and TIER_RANK[ceiling] < TIER_RANK[tier]:
            mass_gate["capped"] = ceiling
            held.add(str(row.get("peak_assignment_id")))
            return ceiling
        return tier

    def partner_key(row: dict) -> str | None:
        """The neutral a row commits through a mode channel, or None."""
        if (
            row.get("role") == ROLE_M0
            and row.get("ionization_mechanism_id") not in minor_ids
            and row.get("assigned_formula")
            and row.get("tier") in (TIER_ASSIGNED, TIER_CANDIDATE)
        ):
            return formula_identity(str(row["assigned_formula"]))
        return None

    def strength(row: dict) -> tuple[int, float]:
        """How strongly a partner commits its neutral: its tier, then its peak."""
        return (
            TIER_RANK.get(str(row.get("tier")), 0),
            _float_or_none(row.get("sample_peak_intensity")) or 0.0,
        )

    # Per neutral, the rows that commit it through a mode channel and how
    # strongly - kept current through every swap, so a row is judged against
    # the ledger as it is, not as it was when the round began.
    partners: dict[str, dict[int, tuple[int, float]]] = {}

    def track(row: dict, was: str | None) -> None:
        """Keep a row's entry current once its reading or its tier moved."""
        if was is not None:
            partners.get(was, {}).pop(id(row), None)
        now = partner_key(row)
        if now is not None:
            partners.setdefault(now, {})[id(row)] = strength(row)

    for row in assignments:
        track(row, None)

    def strongest(formula: str | None) -> tuple[int, float] | None:
        """A neutral's strongest partner, or None where it has none."""
        found = partners.get(formula_identity(str(formula or "")))
        return max(found.values()) if found else None

    def partnered(formula: str | None) -> bool:
        return bool(formula) and strongest(formula) is not None

    def same_neutral(formula: str | None, other: str | None) -> bool:
        return formula_identity(str(formula or "")) == formula_identity(
            str(other or "")
        )

    children: dict[str, list[dict]] = {}
    for row in assignments:
        if row.get("role") == ROLE_ISO_CHILD and row.get("owner_peak_assignment_id"):
            children.setdefault(str(row["owner_peak_assignment_id"]), []).append(row)

    def molecule(alternative: dict) -> bool:
        formula = alternative.get("assigned_formula")
        return bool(
            alternative.get("same_ion")
            and formula
            and alternative.get("ionization_mechanism_id")
            and neutral_is_closed_shell(str(formula))
        )

    def contenders(row: dict) -> list[dict]:
        """The family's other readings through a gated channel with a partner.

        Asked whatever mark an earlier round left on them: a reading set aside
        for want of a partner that the ledger now bears out is weighed like
        any other.
        """
        return [
            alt
            for alt in row.get("alternatives") or []
            if molecule(alt)
            and alt["ionization_mechanism_id"] in gated_ids
            and not same_neutral(alt["assigned_formula"], row.get("assigned_formula"))
            and partnered(alt["assigned_formula"])
        ]

    def weigh(row: dict, alternative: dict) -> dict:
        """What the row's partner has over another reading's, and by how much."""
        own = strongest(row.get("assigned_formula")) or (0, 0.0)
        other = strongest(alternative.get("assigned_formula")) or (0, 0.0)
        ratio = round(own[1] / other[1], 2) if other[1] > 0 else None
        decisive = own > other and (
            own[0] > other[0] or (own[1] > 0 and own[1] >= PARTNER_MARGIN * other[1])
        )
        return {
            "reading": alternative.get("assigned_formula"),
            "via": notation_by_id.get(str(alternative.get("ionization_mechanism_id"))),
            "on": "tier" if own[0] != other[0] else "intensity",
            "tiers": [_TIER_BY_RANK.get(own[0]), _TIER_BY_RANK.get(other[0])],
            "ratio": ratio,
            "decisive": decisive,
        }

    def swap(row: dict, chosen: dict, provenance: dict, gate: dict) -> None:
        """Make ``chosen`` the row's reading and set the current one aside."""
        was = partner_key(row)
        displaced = {
            "assigned_formula": row["assigned_formula"],
            "ion_formula": row.get("ion_formula"),
            "ionization_mechanism_id": row["ionization_mechanism_id"],
            "isotope_label": row.get("isotope_label"),
            "fit_score": row.get("fit_score"),
            "mz_error_ppm": row.get("mz_error_ppm"),
            "plausibility": provenance.get("plausibility"),
            "same_ion": True,
            "source": row.get("source"),
        }
        if row["ionization_mechanism_id"] in gated_ids and not partnered(
            row["assigned_formula"]
        ):
            # An opportunistic reading the sample did not bear out. One it did
            # bear out is left to the contest to weigh.
            displaced["partner_gate"] = PARTNER_GATE_UNMET
        plausibility = float(chosen.get("plausibility") or 0.0)
        fit = float(row.get("fit_score") or 0.0)
        evidence = round(fit * plausibility, 4)
        row["assigned_formula"] = chosen["assigned_formula"]
        row["ionization_mechanism_id"] = chosen["ionization_mechanism_id"]
        restored = tier_of(evidence, row, provenance)
        if restored is not None:
            row["tier"] = restored
        provenance["plausibility"] = plausibility
        provenance["evidence"] = evidence
        if "neutral_mass" in provenance:
            provenance["neutral_mass"] = float(
                calculate_mass(formula=str(chosen["assigned_formula"]))
            )
        provenance.pop("unsaturation", None)
        if chosen["ionization_mechanism_id"] in minor_ids:
            # Another opportunistic reading, taken because the sample shows
            # its neutral: that is the policy's own corroboration.
            provenance["minor_channel"] = {
                "corroborated_by": "second_channel",
                "capped": False,
            }
        else:
            # The mode's own reading, so the policy's verdict is moot.
            provenance.pop("minor_channel", None)
        provenance["partner_gate"] = gate
        row["alternatives"] = [displaced] + [
            alt for alt in row.get("alternatives") or [] if alt is not chosen
        ]
        for child in children.get(str(row.get("peak_assignment_id")), ()):
            child["assigned_formula"] = row["assigned_formula"]
            child["ionization_mechanism_id"] = row["ionization_mechanism_id"]
        track(row, was)

    for _round in range(MAX_PARTNER_GATE_ROUNDS):
        summary["rounds"] += 1
        changed = False
        for row in assignments:
            if row.get("role") != ROLE_M0:
                continue
            provenance = row.setdefault("provenance", {})
            gate = provenance.get("partner_gate") or {}
            standing = row.get("ionization_mechanism_id") in gated_ids and partnered(
                row.get("assigned_formula")
            )
            # A row an earlier round swapped, whose set-aside reading the
            # ledger now bears out: swap back. Where the row's reading stands
            # on a partner of its own, the two are weighed below instead.
            if gate.get("displaced") and partnered(gate["displaced"]) and not standing:
                back = next(
                    (
                        alt
                        for alt in row.get("alternatives") or []
                        if alt.get("partner_gate") == PARTNER_GATE_UNMET
                        and alt.get("ionization_mechanism_id") in gated_ids
                        and same_neutral(alt.get("assigned_formula"), gate["displaced"])
                    ),
                    None,
                )
                if back is not None:
                    chosen = dict(back)
                    chosen.pop("partner_gate", None)
                    own = notation_by_id.get(chosen["ionization_mechanism_id"])
                    swap(
                        row,
                        chosen,
                        provenance,
                        {"channel": own, "partner": True, "returned": True},
                    )
                    row["alternatives"] = [
                        alt for alt in row["alternatives"] if alt is not back
                    ]
                    changed = True
                    continue
            if row.get("ionization_mechanism_id") not in gated_ids:
                continue
            own = notation_by_id.get(row["ionization_mechanism_id"])
            verdict = provenance.get("minor_channel")
            if standing:
                # Two readings of the ion the sample bears out: the one whose
                # partner is the stronger is the row's, at a tie the one it
                # holds.
                stronger = max(
                    contenders(row),
                    key=lambda alt: strongest(alt["assigned_formula"]),
                    default=None,
                )
                if stronger is not None and strongest(
                    stronger["assigned_formula"]
                ) > strongest(row["assigned_formula"]):
                    swap(
                        row,
                        stronger,
                        provenance,
                        {
                            "channel": notation_by_id.get(
                                stronger["ionization_mechanism_id"]
                            ),
                            "partner": True,
                            "took_from": row["assigned_formula"],
                        },
                    )
                    changed = True
                    continue
                record = {
                    key: value
                    for key, value in gate.items()
                    if key not in ("kept", "recapped")
                }
                record.setdefault("channel", own)
                record["partner"] = True
                if verdict is not None and verdict.get("corroborated_by") is None:
                    # The policy saw the search's rows alone; the partner is here.
                    verdict["corroborated_by"] = "second_channel"
                    if verdict.get("capped"):
                        verdict["capped"] = False
                        was = partner_key(row)
                        restored = tier_of(
                            float(provenance.get("evidence") or 0.0), row, provenance
                        )
                        if restored is not None:
                            row["tier"] = restored
                        track(row, was)
                        record["uncapped"] = True
                if record != gate:
                    provenance["partner_gate"] = record
                    changed = True
                continue
            family = [alt for alt in row.get("alternatives") or [] if molecule(alt)]
            chosen = next(
                (
                    alt
                    for alt in family
                    if alt["ionization_mechanism_id"] not in minor_ids
                ),
                None,
            ) or max(
                (
                    alt
                    for alt in family
                    if alt["ionization_mechanism_id"] in minor_ids
                    and partnered(alt["assigned_formula"])
                ),
                key=lambda alt: strongest(alt["assigned_formula"]),
                default=None,
            )
            if chosen is None:
                record = {"channel": own, "partner": False, "kept": PARTNER_GATE_KEPT}
                if gate.get("recapped"):
                    record["recapped"] = True
                if verdict is not None and verdict.get("corroborated_by") == (
                    "second_channel"
                ):
                    # The partner the policy or an earlier round read is gone.
                    verdict["corroborated_by"] = None
                    verdict["capped"] = row.get("tier") == TIER_ASSIGNED
                    if verdict["capped"]:
                        was = partner_key(row)
                        row["tier"] = TIER_CANDIDATE
                        track(row, was)
                    record["recapped"] = True
                if record != gate:
                    provenance["partner_gate"] = record
                    changed = True
                continue
            swap(
                row,
                chosen,
                provenance,
                {
                    "channel": own,
                    "partner": False,
                    "displaced": row["assigned_formula"],
                    "through": notation_by_id.get(chosen["ionization_mechanism_id"]),
                },
            )
            changed = True
        if not changed:
            break
    else:
        summary["settled"] = False
    summary["held"] = len(held)

    # Read last, against the partners the walk settled on: the contests a
    # standing reading won, with the readings it outweighed marked, and the
    # same-ion readings through a gated channel that nothing bore out, set
    # aside on every row, a reference list's included. The ledger is tallied
    # as it is.
    for row in assignments:
        if row.get("role") != ROLE_M0:
            continue
        gate = (row.get("provenance") or {}).get("partner_gate") or {}
        if row.get("ionization_mechanism_id") in gated_ids:
            if gate.get("partner") is True:
                summary["partnered"] += 1
                summary["uncapped"] += bool(gate.get("uncapped"))
                summary["swapped_back"] += bool(gate.get("returned"))
            elif gate.get("kept"):
                summary["kept"] += 1
                summary["recapped"] += bool(gate.get("recapped"))
        if gate.get("displaced") and gate.get("partner") is False:
            summary["swapped"] += 1
        weighed = (
            contenders(row)
            if row.get("ionization_mechanism_id") in gated_ids
            and partnered(row.get("assigned_formula"))
            else []
        )
        contest = []
        for alternative in row.get("alternatives") or []:
            if any(alternative is contender for contender in weighed):
                entry = weigh(row, alternative)
                contest.append(entry)
                if entry["decisive"]:
                    alternative["partner_gate"] = PARTNER_GATE_OUTWEIGHED
                    summary["outweighed"] += 1
                else:
                    alternative.pop("partner_gate", None)
                    summary["within_margin"] += 1
            elif alternative.get("partner_gate") == PARTNER_GATE_UNMET:
                summary["set_aside"] += 1
            elif (
                alternative.get("same_ion")
                and alternative.get("ionization_mechanism_id") in gated_ids
                and not partnered(alternative.get("assigned_formula"))
            ):
                alternative["partner_gate"] = PARTNER_GATE_UNMET
                summary["set_aside"] += 1
        if contest:
            row["provenance"]["partner_gate"] = {**gate, "contest": contest}
            summary["contested"] += 1
            summary["contest_swapped"] += bool(gate.get("took_from"))
        elif "contest" in gate:
            row["provenance"]["partner_gate"] = {
                key: value for key, value in gate.items() if key != "contest"
            }
    return summary


def _apply_minor_channel_policy(
    assignments: list[dict],
    mechanism_id_by_notation: dict[str, str],
    minor_channels: frozenset[str],
) -> None:
    """Cap an uncorroborated secondary-channel winner at ``candidate``, in place.

    A secondary channel is searched because the sample's own spectrum shows its
    carrier, not because any neutral it proposes has been demonstrated. So a
    winner on one commits at the ledger's strongest word only when something
    beyond the mass fit agrees:

    - its isotope envelope was confirmed, meaning the search paired the peak
      with at least one isotopologue of the formula it proposes; or
    - the same neutral won a peak on one of the mode's own channels in this
      sample, which is cross-channel corroboration in its simplest form.

    Neither is a tier rule of its own - stage 2's mechanical tiers take this
    over and will say it better, with the reasons in provenance. Until then the
    cap is what keeps an opportunistic channel from adding "assigned" rows on
    its own authority, which is the failure mode the sodium measurement showed:
    317 committed readings on a source with no sodium cluster in it.

    Every secondary-channel row records the verdict either way, so a run says
    which of its rows leaned on a channel it opened for itself.

    :param assignments: The rows built for this sample, modified in place.
    :param mechanism_id_by_notation: Notation to mechanism id, to recognise a
        row's channel from the id it carries.
    :param minor_channels: The notations that are secondary in this run.
    """
    minor_ids = {
        mechanism_id_by_notation[notation]
        for notation in minor_channels
        if mechanism_id_by_notation.get(notation)
    }
    if not minor_ids:
        return
    owners_with_children = {
        row["owner_peak_assignment_id"]
        for row in assignments
        if row["role"] == ROLE_ISO_CHILD and row["owner_peak_assignment_id"]
    }
    primary_formulas = {
        row["assigned_formula"]
        for row in assignments
        if row["role"] == ROLE_M0
        and row["ionization_mechanism_id"] not in minor_ids
        and row["assigned_formula"]
    }
    for row in assignments:
        if row["role"] != ROLE_M0 or row["ionization_mechanism_id"] not in minor_ids:
            continue
        corroboration = None
        if row["peak_assignment_id"] in owners_with_children:
            corroboration = "isotopologue"
        elif row["assigned_formula"] in primary_formulas:
            corroboration = "second_channel"
        capped = corroboration is None and row["tier"] == TIER_ASSIGNED
        provenance = row.setdefault("provenance", {})
        provenance["minor_channel"] = {
            "corroborated_by": corroboration,
            "capped": capped,
        }
        if capped:
            row["tier"] = TIER_CANDIDATE


def build_unassigned_assignments(
    peaks_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
) -> list[dict]:
    """Build placeholder rows for peaks no stage explained.

    Every observed peak gets exactly one row per run; peaks left over after
    Stage A and Stage B are persisted with role/tier 'unassigned' so the
    ledger is complete and queryable.

    :param peaks_df: DataFrame with sample_peak_id, mz, and intensity columns.
    """
    return [
        {
            "peak_assignment_id": gen_id(32),
            "peak_assignment_run_id": peak_assignment_run_id,
            "sample_item_id": sample_item_id,
            "sample_peak_id": str(row.sample_peak_id),
            "sample_peak_mz": float(row.mz),
            "sample_peak_intensity": float(row.intensity),
            "sample_peak_tof": None,
            "role": ROLE_UNASSIGNED,
            "assigned_formula": None,
            "ion_formula": None,
            "ionization_mechanism_id": None,
            "isotope_label": None,
            "isotope_formula": None,
            "source": None,
            "fit_score": None,
            "mz_error_ppm": None,
            "abundance_error": None,
            "tier": TIER_UNASSIGNED,
            "target_compound_id": None,
            "target_ion_id": None,
            "owner_peak_assignment_id": None,
            "alternatives": None,
            "provenance": None,
        }
        for row in peaks_df.itertuples(index=False)
    ]
