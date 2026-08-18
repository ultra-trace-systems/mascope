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
    fit_sample_mass_accuracy,
    ion_score_v2,
    sample_noise_floor,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.arbitration import DEFAULT_TIE_TOL
from mascope_tools.composition.calibration import (
    Calibration,
    apply_calibration,
    apply_corroboration,
    calibration_for,
)
from mascope_tools.composition.heuristic_filter import (
    SCORE_VERSION,
    formula_plausibility,
)


# Sentinel so a caller can pass calibration=None (explicitly uncalibrated) distinctly from
# "not provided" (fall back to the in-code registry via calibration_for).
_CALIBRATION_UNSET = object()


# Confidence tiers (a richer replacement for match_category 0/1/2)
TIER_IDENTIFIED = "identified"
TIER_CANDIDATE = "candidate"
TIER_BELOW_ASSIGNABILITY = "below_assignability"
TIER_UNASSIGNED = "unassigned"

# Peak roles within an assignment run
ROLE_M0 = "M0"
ROLE_ISO_CHILD = "iso_child"
ROLE_UNASSIGNED = "unassigned"

# Which stage won the peak
SOURCE_DATABASE = "database"
SOURCE_UNTARGETED = "untargeted"

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
# as one; a dedicated reagent role is a later phase.
UNTARGETED_IONIZATION = "()"


def tier_for_score(
    score: float | None,
    possible_threshold: float,
    probable_threshold: float,
) -> str:
    """Map a match score onto a confidence tier."""
    if score is None or not np.isfinite(score) or score <= 0:
        return TIER_BELOW_ASSIGNABILITY
    if score >= probable_threshold:
        return TIER_IDENTIFIED
    if score >= possible_threshold:
        return TIER_CANDIDATE
    return TIER_BELOW_ASSIGNABILITY


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
    """Label an isotopologue by its nominal mass offset from the ion's M0."""
    if main_mz is None or not np.isfinite(main_mz):
        return None
    offset = int(round(iso_mz - main_mz))
    if offset == 0:
        return "M0"
    return f"M+{offset}" if offset > 0 else f"M{offset}"


# Columns the ion-level fit score needs on the isotope frame. Absent (e.g. a lighter
# match path) -> the fit scoring is skipped and the per-isotopologue score stands.
_FIT_SCORE_COLS = frozenset(
    {"target_ion_id", "relative_abundance", "match_mz_error", "sample_peak_intensity"}
)


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
    as ABSENT claim its peak anyway, carrying the ion's (possibly "identified") fit
    score and blocking Stage B from explaining that peak. One gating decision, one
    frame.

    Real per-peak ``signal_to_noise`` (carried from the filestore by
    `compute_match_isotopes`) makes this the full v2 fit; rows without it are
    scored in `ion_score_v2`'s no-SNR mode. No-op (returns the frame
    unchanged) when empty or missing the required columns.

    NOTE: the confidence-tier bands sit on the fit scale --
    identified/candidate = 0.8/0.5 on `PeakAssignmentConfig` (config.py), the v2
    estimates rather than the legacy `match_params` 0.8/0.7, because a lone
    mass-only match scores low by design on this scale. Per-instrument
    recalibration of the bands is a follow-up once verification labels
    accumulate.
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


def _alternative_dict(row, reference_identities_by_formula: dict) -> dict:
    """Build one runner-up candidate dict for a peak's ``alternatives`` list.

    Null the target FKs when the runner-up is itself a reference row, and attach
    the formula's reference identities (if any) so a runner-up known compound is
    still named.
    """
    is_reference_row = _row_reference_identities(row) is not None
    formula = _str_or_none(row.get("target_compound_formula"))
    alternative = {
        "assigned_formula": formula,
        "ion_formula": _str_or_none(row.get("target_ion_formula")),
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


def invert_matches_to_peak_assignments(
    match_isotope_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
    possible_threshold: float,
    probable_threshold: float,
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

    Roles: the winner is 'M0' when it is its ion's reference (most abundant)
    isotope, otherwise 'iso_child' pointing at the assignment that holds the
    ion's M0 peak (when that peak was also won by the same ion).

    :param match_isotope_df: Output of compute_match_isotopes enriched with
        target metadata columns (target_compound_id, target_compound_formula,
        target_ion_formula, ionization_mechanism_id).
    :param sample_item_id: Sample the peaks belong to.
    :param peak_assignment_run_id: Run the assignments belong to.
    :param possible_threshold: Score threshold for the 'candidate' tier.
    :param probable_threshold: Score threshold for the 'identified' tier.
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

    # Reference (most abundant) isotope per ion, used for role attribution
    # and isotope labelling. Computed over the full target set so an ion
    # whose M0 went unmatched still labels its children correctly.
    main_idx = match_isotope_df.groupby("target_ion_id")["relative_abundance"].idxmax()
    main_isotopes = match_isotope_df.loc[main_idx]
    main_isotope_ids = set(main_isotopes["target_isotope_id"])
    main_mz_by_ion = main_isotopes.set_index("target_ion_id")["mz"].to_dict()

    # Arbitration (P2): rank a peak's competing candidates by evidence =
    # fit x chemical plausibility, not fit alone, so a chemically implausible formula
    # cannot win a peak on mass fit. The stored fit_score stays the pure measurement;
    # evidence only drives the winner selection and the reported confidence.
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
    # The ranking mirrors `arbitration.arbitrate_candidates` (evidence first, formula last
    # for a stable order) but is not delegated to it: that helper competes bare
    # (formula, fit) candidates and returns a dataclass, while a peak's winner here has to
    # keep the whole match row - target FKs, reference identities, isotope role, mass and
    # abundance errors - which the dataclass cannot carry. Mass error is the extra middle
    # key, and it is the domain-meaningful one: between two equally plausible formulas that
    # fit equally well, the closer mass is the better assignment. The formula key is last
    # so two candidates equal on all three still resolve by the data rather than by whatever
    # order the matcher happened to emit them in.
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
        runners = (
            group.iloc[1 : 1 + max_alternatives]
            if max_alternatives
            else group.iloc[1:1]
        )

        # Arbitration confidence for the chosen winner: its share of the peak's total
        # evidence, plus an honest tie flag when a runner-up is within tie_tol.
        #
        # Confidence answers "which of this peak's candidates", NOT "how good is this
        # assignment" - an uncontested peak scores 1.0 however poorly its single candidate
        # fits, because there was nothing to lose to. That is the same normalisation
        # `arbitrate_candidates` reports and the UI already reads, so it is kept rather than
        # redefined; `n_candidates` is recorded alongside it so a 1.0 earned against
        # competitors is distinguishable from a 1.0 that was uncontested. The question
        # "how good" is `p_correct` (the calibrated evidence), which does not divide by the
        # field and does fall for a poor lone candidate.
        evid = group["_evidence"].to_numpy(dtype=float)
        total_evidence = float(evid.sum())
        confidence = float(evid[0] / total_evidence) if total_evidence > 0 else 0.0
        if total_evidence <= 0:
            is_tie = len(group) > 1
        else:
            # bool() so provenance stays JSON-serializable (evid is a numpy array,
            # whose comparisons yield numpy.bool_, which the JSON column rejects).
            is_tie = bool(len(group) > 1 and (evid[0] - evid[1]) <= DEFAULT_TIE_TOL)

        # Calibrated P(correct) for the winner's evidence — only when this instrument
        # has a calibration; otherwise the assignment is honestly left uncalibrated.
        if calibration is not None:
            p_correct = round(float(apply_calibration(evid[0], calibration)), 4)
            calibration_meta = {
                "instrument": calibration.instrument,
                "provisional": calibration.provisional,
                "source": calibration.source,
            }
        else:
            p_correct = None
            calibration_meta = None
        alternatives = [
            _alternative_dict(row, reference_identities_by_formula)
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
            "tier": tier_for_score(
                _score_or_none(winner["match_score"]),
                possible_threshold,
                probable_threshold,
            ),
            "target_compound_id": _str_or_none(winner.get("target_compound_id")),
            # A reference-row winner carries only a synthetic ion id (for in-run
            # grouping), never persisted as a dangling FK; a target winner keeps its.
            "target_ion_id": None if winner_is_reference_row else ion_id,
            "owner_peak_assignment_id": None,
            "alternatives": alternatives or None,
            "provenance": {
                "confidence": round(confidence, 4),
                # How many candidates the confidence was normalised across; 1 means the
                # peak was uncontested and the 1.0 above was won by default.
                "n_candidates": int(len(group)),
                "plausibility": round(float(winner["_plaus"]), 4),
                "evidence": round(float(winner["_evidence"]), 4),
                "is_tie": is_tie,
                # The fit-score generation this evidence is on. Snapshotted here because
                # a verification label is only interpretable against the scoring that
                # produced it (see AssignmentVerification.score_version).
                "score_version": SCORE_VERSION,
                # P(correct) is the calibrated probability; null when uncalibrated,
                # so the UI can show "uncalibrated" instead of a fabricated number.
                "p_correct": p_correct,
                "calibrated": calibration is not None,
                "calibration": calibration_meta,
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
    ``apply_corroboration``). Only confident (identified/candidate) winners count toward the
    co-occurrence set, so a low-confidence sibling can't manufacture corroboration. No-op when the
    calibration carries no weights. Records the co-occurrence + boost in provenance for the UI."""
    if not weights or not m0_items:
        return
    adducts_by_compound: dict[str, set[str]] = {}
    for assignment, compound_id, notation in m0_items:
        if assignment["tier"] in (TIER_IDENTIFIED, TIER_CANDIDATE):
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


def untargeted_matches_to_peak_assignments(
    matches_df: pd.DataFrame,
    peaks_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
    possible_threshold: float,
    probable_threshold: float,
    mechanism_id_by_notation: dict[str, str] | None = None,
    formula_formatter=None,
    max_alternatives: int = 5,
) -> list[dict]:
    """Map untargeted composition results onto peak assignments (Stage B).

    `assign_compositions` already yields one row per input peak with the best
    composition, isotope labelling, and runner-up formulas; this converts
    those rows into the persisted PeakAssignment shape. Rows with the '---'
    placeholder are skipped (their peaks stay unassigned).

    Scoring uses the fit score. `assign_compositions` (via `match_isotopic_pattern`)
    already scores each candidate's whole predicted isotope envelope against the
    spectrum and carries it as ``isotopic_pattern_score``; Stage B uses that as the
    match score. The untargeted path has no per-peak signal-to-noise, so this is the
    isotope-pattern fit score (mascope_tools ``score_pattern``) -- the fit score's
    documented degradation where SNR evidence is absent, not the crude single-peak
    term the engine used before. When no envelope was scored (the column is
    absent/NaN) it falls back to the legacy single-peak maths
    ``score = (1 - min(1, |intensity_error|)) * max(0, 1 - |mz_error_ppm|/100)``.

    Two results can land on the same observed peak - typically one composition's isotope
    child on another composition's M0 - and only one may own it. The contest is settled by
    ``evidence = fit x plausibility``, the same currency Stage A arbitrates in, and the
    loser is kept as an alternative on the winner rather than dropped: a peak that two
    compositions explain is exactly the peak an analyst needs to see both explanations for.

    :param matches_df: First element returned by assign_compositions.
    :param peaks_df: The observed peaks that were fed into the untargeted search, with
        ``sample_peak_id`` / ``mz`` / ``intensity`` columns. Results are joined back to it
        by position (see :func:`_resolve_peak_positions`).
    :param mechanism_id_by_notation: Maps the ionization notation used in the
        search back to IonizationMechanism ids.
    :param formula_formatter: Optional callable applied to formulas (e.g.
        explicit-isotope to custom element notation conversion).
    :return: One assignment dict per assigned peak, ready for bulk insert.
    """
    if matches_df.empty or peaks_df.empty:
        return []

    mechanism_id_by_notation = mechanism_id_by_notation or {}
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
    for row, position in zip(assigned_rows, positions):
        isotope_label = _str_or_none(row.get("isotope_label")) or "M0"
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
        contenders_by_position.setdefault(position, []).append(
            {
                "row": row,
                "formula": formula,
                "isotope_label": isotope_label,
                "fit": _score_or_none(score) or 0.0,
                "score": score,
                "plausibility": plausibility,
                "mz_error_ppm": mz_error_ppm,
                "abundance_error": abundance_error,
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

    assignments: list[dict] = []
    m0_assignment_by_group: dict[tuple, str] = {}
    child_assignments: list[tuple[dict, tuple]] = []

    for position, contenders in contenders_by_position.items():
        # Same ranking as Stage A: evidence first, closest mass next, formula last so a
        # dead heat resolves by the data rather than by the finder's row order.
        contenders.sort(
            key=lambda c: (
                -(c["fit"] * c["plausibility"]),
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
        alternatives = [
            {
                "assigned_formula": format_formula(loser["formula"]),
                "ion_formula": _str_or_none(loser["row"].get("ion")),
                "isotope_label": loser["isotope_label"],
                "fit_score": _score_or_none(loser["score"]),
                "mz_error_ppm": loser["mz_error_ppm"],
                "plausibility": loser["plausibility"],
                "source": SOURCE_UNTARGETED,
            }
            for loser in losers
        ]
        other_candidates = _str_or_none(row.get("other_candidates"))
        if other_candidates:
            alternatives.extend(
                {
                    "assigned_formula": format_formula(alt.strip()),
                    "plausibility": round(float(formula_plausibility(alt.strip())), 4),
                    "source": SOURCE_UNTARGETED,
                }
                for alt in other_candidates.split(",")
                if alt.strip()
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
        # Confidence and P(correct) stay database-arbitration concepts. Confidence needs
        # the peak's full scored candidate set, which the untargeted search does not expose
        # here (other_candidates carries formulas only, no per-candidate fit). P(correct)
        # would mean applying the Stage A curve to a Stage B number: this stage's fit is
        # score_pattern (v1 -- no per-peak SNR, no penalty for an absent isotopologue), a
        # different scale from the ion_score_v2 the curve was fit on. Borrowing it across
        # scales is the fabricated probability the calibration layer exists to refuse.
        provenance = {
            "plausibility": winner["plausibility"],
            "evidence": round(winner["fit"] * winner["plausibility"], 4),
            "score_version": SCORE_VERSION,
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
            "fit_score": _score_or_none(winner["score"]),
            "mz_error_ppm": winner["mz_error_ppm"],
            "abundance_error": winner["abundance_error"],
            "tier": tier_for_score(
                winner["score"], possible_threshold, probable_threshold
            ),
            "target_compound_id": None,
            "target_ion_id": None,
            "owner_peak_assignment_id": None,
            "alternatives": alternatives,
            "provenance": provenance,
        }
        assignments.append(assignment)

        if is_m0:
            m0_assignment_by_group.setdefault(
                group_key, assignment["peak_assignment_id"]
            )
        else:
            child_assignments.append((assignment, group_key))

    for assignment, group_key in child_assignments:
        assignment["owner_peak_assignment_id"] = m0_assignment_by_group.get(group_key)

    return assignments


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
