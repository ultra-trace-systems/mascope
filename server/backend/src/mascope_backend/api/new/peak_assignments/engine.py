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

import numpy as np
import pandas as pd

from mascope_backend.api.controllers.match.lib.match_score_v2 import (
    PRED_SIGMA_PPM,
    fit_sample_mass_accuracy,
    ion_score_v2,
    sample_noise_floor,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.arbitration import arbitrate_candidates
from mascope_tools.composition.calibration import (
    Calibration,
    apply_calibration,
    apply_corroboration,
    calibration_for,
)
from mascope_tools.composition.heuristic_filter import (
    SAME_ION_ALTERNATIVES,
    SCORE_VERSION,
    element_counts,
    formula_plausibility,
)
from mascope_tools.composition.models import PatternScoring


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


def pattern_scoring_for(
    match_params,
    mass_accuracy: tuple[float, float | None],
) -> PatternScoring:
    """How the untargeted stage scores this sample's isotope envelopes.

    Everything the composition finder needs to judge a candidate as a
    measurement of THIS sample rather than of a generic Orbitrap: the width its
    mass errors actually have, the offset they sit at, the window a line may be
    matched in, and how deep an envelope may be predicted.

    The width comes from Stage A - the fitted spread of the curated library's
    own matched isotopologues, which is the instrument's measured accuracy on
    this sample - widened by ``PRED_SIGMA_PPM`` exactly as Stage A's own fit
    widens it, so a Stage B row and a Stage A row are judged at one width.
    Where Stage A found too few anchors to fit anything, the match tolerance
    stands in: it is the instrument's own statement about its accuracy, and a
    tolerance is about three sigma of one.

    :param match_params: The sample's resolved match parameters.
    :param mass_accuracy: ``(mu, sigma)`` in ppm from Stage A's matched rows;
        sigma is None when there were too few to fit.
    :return: The scoring parameters for this sample's search.
    """
    mu, sigma = mass_accuracy
    tolerance = float(match_params.mz_tolerance)
    if sigma is None:
        sigma = tolerance / 3.0
    return PatternScoring(
        sigma_ppm=float(np.hypot(float(sigma), PRED_SIGMA_PPM)),
        mu_ppm=float(mu),
        mz_tolerance_ppm=tolerance,
        abundance_floor=float(match_params.isotope_abundance_threshold),
    )


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


def is_monoisotopic_formula(formula) -> bool:
    """Whether an isotopologue formula names the ion's monoisotopic isotopologue.

    The generator writes a substituted isotope in brackets (``C5[13C]H13O6+``,
    ``[81Br]Br2-``) and the monoisotopic isotopologue - every element at its
    most abundant isotope - without (``C6H13O6+``, ``Br3-``).
    """
    return isinstance(formula, str) and bool(formula) and "[" not in formula


def monoisotopic_row(ion_rows: pd.DataFrame) -> pd.Series:
    """The row of an ion's monoisotopic isotopologue: the M0 every role and
    offset label counts from, the way an isotope table counts - which for a
    bromine- or chlorine-rich ion is the lightest peak of the cluster, not the
    tallest. The lightest row stands in when no formula carries the isotope
    marker that tells the two apart, and is the same row wherever an element's
    most abundant isotope is also its lightest.

    Positionally off a sort rather than ``.loc[idxmin()]``: a frame that has
    been through a gate and a scorer can carry a duplicated index, and that
    lookup would then hand back a frame where every caller expects one row.

    :param ion_rows: One ion's rows of an isotope frame (``mz``, and
        ``target_isotope_formula`` where the frame has it).
    """
    ordered = ion_rows.sort_values("mz")
    if "target_isotope_formula" in ordered.columns:
        mono = ordered[
            ordered["target_isotope_formula"].map(is_monoisotopic_formula).astype(bool)
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
    behind with nothing to belong to: they invert as ``iso_child`` rows with a
    null owner, one of them relabelled M0, and the ledger carries an
    isotopologue family whose ion is not in it.

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


def score_ions_by_fit(match_isotope_df: pd.DataFrame) -> pd.DataFrame:
    """Set each isotopologue's ``match_score`` to its ion's fit score (Stage A).

    The peak-centric engine adopts the fit score (`score_pattern_v2`) *deliberately*
    as its scoring engine -- unconditionally, not gated on the legacy
    ``MASCOPE_MATCH_SCORE_VERSION`` switch, per the epic's "coexist, don't replace"
    principle. Where the targeted matcher emits a per-isotopologue
    ``abundance_term * mz_term``, this replaces it with the consolidated ion-level
    fit quality: the whole predicted isotope envelope scored against the spectrum
    (mass, intensity, SNR-detectability), computed exactly as the aggregate match
    path does (`ion_score_v2` per ``target_ion_id`` with the sample's fitted mass
    accuracy). Every isotopologue of an ion carries that ion's fit, so the
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

    mu, sigma = fit_sample_mass_accuracy(df)
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
    as the winner to be one of its ion's satellites rather than the main one -
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
    formula_identities = reference_identities_by_formula.get(formula)
    if formula_identities:
        alternative["reference_identities"] = formula_identities
    return alternative


def _restates_winner(contenders: "pd.DataFrame", winner) -> "pd.Series":
    """Mask of contender rows that restate the winner's own hypothesis.

    Same formula through the same ionization mechanism is one explanation of the
    peak, however many rows carried it into the frame - the reference mirror sits
    in the same frame as the curated targets, so a compound that is both a target
    and a known reference contributes two rows, and two targets can share a
    formula outright. Excluding the winner by position alone left those twins in
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

    :param contenders: The peak's rows other than the winning one.
    :param winner: The row that won the peak.
    :return: Boolean mask, True where the row is the winner's hypothesis again.
    """
    same = contenders["target_compound_formula"].astype(str) == str(
        winner.get("target_compound_formula")
    )
    if "ionization_mechanism_id" in contenders.columns:
        same &= contenders["ionization_mechanism_id"].astype(str) == str(
            winner.get("ionization_mechanism_id")
        )
    return same


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
    cluster rather than the tallest - otherwise 'iso_child' pointing at the
    assignment that holds the ion's M0 peak (when that peak was also won by the
    same ion).

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
        reference_rows = match_isotope_df[
            match_isotope_df[REFERENCE_IDENTITIES_COL].apply(
                lambda value: isinstance(value, list) and bool(value)
            )
        ]
        for formula, group in reference_rows.groupby("target_compound_formula"):
            reference_identities_by_formula[str(formula)] = group.iloc[0][
                REFERENCE_IDENTITIES_COL
            ]

    # Reference isotope per ion - the monoisotopic isotopologue, the M0 an isotope
    # table counts from - used for role attribution and isotope labelling. For a
    # bromine- or chlorine-rich ion that is the lightest peak of the cluster, not
    # the most intense one. Computed over the full target set so an ion whose M0
    # went unmatched still labels its children correctly.
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
    formulas = matched["target_compound_formula"].astype(str)
    plaus_by_formula = {f: formula_plausibility(f) for f in formulas.unique()}
    matched["_plaus"] = formulas.map(plaus_by_formula)
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
    matched["_formula_key"] = formulas
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
        winner_formula = _str_or_none(winner.get("target_compound_formula"))
        formula_identities = reference_identities_by_formula.get(winner_formula)
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
            "isotope_formula": _str_or_none(winner.get("target_isotope_formula")),
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

        if is_main and ion_id is not None:
            m0_assignment_by_ion[ion_id] = assignment["peak_assignment_id"]
            compound_id = _str_or_none(winner.get("target_compound_id"))
            notation = _str_or_none(winner.get("ionization_mechanism"))
            if compound_id and notation:
                m0_corroboration.append((assignment, compound_id, notation))
        else:
            child_assignments.append((assignment, ion_id))

    # Attribute isotope children to their ion's M0 assignment. Stays None
    # when the ion's M0 peak was not won by the same ion in this run.
    for assignment, ion_id in child_assignments:
        assignment["owner_peak_assignment_id"] = m0_assignment_by_ion.get(ion_id)

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

    Every committed row is seeded, not only the M0 rows: a satellite's ion is a
    hypothesis about the peak it sits on, it can lose that peak to another
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
    a satellite is owned by that row.

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

    Two measurements of one assignment, and they answer different questions. The
    finder's ``isotopic_pattern_score`` decides which READING of a peak wins: it
    is the v2 fit of a candidate's predicted envelope against the peak list,
    computed on every candidate of every searched peak, and it is what ranks
    them. What a committed row is TIERED on is ``fit_by_seed`` - the same ion
    measured again through the full match path, one ``compute_match_isotopes``
    pass over the sample with the run's match-params gating, which is how a
    Stage A row is measured. Reading the tier off that is what puts the two
    stages' evidence on one scale; the finder's own score stays in provenance,
    where a reader can see the two disagree.

    Without a seeded fit for a row's ion the finder's score stands, and the row
    then says so by carrying the same number twice. When no envelope was scored
    either (the column is absent/NaN) it falls back to the legacy single-peak
    maths ``score = (1 - min(1, |intensity_error|)) * max(0, 1 - |mz_error_ppm|/100)``.

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

    A satellite is written by the monoisotopic row that claims it and names that row as
    its owner from the start. It is never linked to a parent afterwards, and when the
    ion's M0 wins no peak of its own the satellite is not written at all: an
    isotopologue row that belongs to nothing states that a peak is part of an envelope
    whose ion the ledger never commits, which is not a verdict a reader can act on. Its
    peak stays unassigned instead, which is what it is.

    :param matches_df: First element returned by assign_compositions.
    :param peaks_df: The observed peaks the search results are joined back to, by
        position (see :func:`_resolve_peak_positions`), with ``sample_peak_id`` / ``mz``
        / ``intensity`` columns. This is the sample's whole peak list, not only the
        peaks that were enumerated: the finder scores an envelope against every peak it
        is given, so a satellite lands wherever it sits rather than only inside the
        searched set.
    :param fit_by_seed: The seeded re-score's fit per ``(formula, mechanism id)``,
        from :func:`untargeted_seeds` measured through ``score_seeds``. Optional:
        a caller that cannot run a match pass (a unit test, a path with no
        sample file) gets the finder's own score on every row instead, which is
        the behaviour this had before the re-score existed.
    :param excluded_peak_ids: Peaks another pass already owns - the reagent and artifact
        pre-passes, and Stage A. Rows landing on them are dropped rather than written:
        the ledger holds one row per peak, and a stage that arrives second does not get
        to restate a peak somebody else has already accounted for. Dropping the M0 this
        way takes its satellites with it, by the rule above.
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
    # line can sit at a lower m/z than a satellite already seen - so which
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
            "score_version": SCORE_VERSION,
            # What the finder made of this reading's envelope against the peak
            # list, which is what ranked it against the other readings of its
            # peak. Kept beside the fit the row is tiered on because the two are
            # different measurements of the same ion - different envelope depth,
            # different gating, noise read from the peak list rather than from
            # the match frame - and where they disagree, that is the finding.
            "pattern_fit": _score_or_none(winner["score"]),
        }
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
            "isotope_formula": _str_or_none(row.get("isotope_formula")),
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
      with at least one satellite of the formula it proposes; or
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
