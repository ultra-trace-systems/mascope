"""The composition finder's matches as ledger rows.

Stage B's matches, one row per peak with its isotope lines as children,
joined back to the peaks they were found at, and the seeds a re-score
measures them by (``untargeted_matches_to_peak_assignments``,
``untargeted_seeds``).
"""

import numpy as np
import pandas as pd

from mascope_backend.api.new.peak_assignments.engine import (
    _float_or_none,
    _score_or_none,
    _str_or_none,
    cap_alternatives,
    fit_isotope_formula,
    tier_for_evidence,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_UNTARGETED,
    UNTARGETED_IONIZATION,
    UNTARGETED_NO_MATCH,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.arbitration import CANDIDATE_DENSITY
from mascope_tools.composition.heuristic_filter import (
    PATTERN_BASE_SNR,
    SAME_ION_ALTERNATIVES,
    SCORE_VERSION,
    formula_plausibility,
)


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
        channel explains. A winner on one is judged after the reading changes
        settle (:func:`cap_second_channel`), not here.
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
        # Cut at the cap, counting the rival formulas it leaves unstored: the
        # finder's shortlist runs to dozens on a dense peak, and a rival the row
        # does not store is one no reader can see tested.
        cut = {"assigned_formula": format_formula(formula), "provenance": {}}
        alternatives = cap_alternatives(cut, alternatives, max_alternatives)

        # Chemical plausibility (Seven Golden Rules) is stage-agnostic and the headline
        # provenance metric, so the untargeted winner reports it on the same footing as a
        # database winner (Stage A above). Evidence -- the same fit x plausibility product
        # -- rides along for a different reason: it is the quantity `create_verification`
        # snapshots as a verdict's calibration label, and a Stage B label whose evidence is
        # null can never be fit. It is indistinguishable from a verdict nobody recorded, in
        # the one table no re-run can rebuild, so the number is captured now even though
        # nothing fits it yet.
        #
        # Confidence is not written here. It needs the peak's full scored
        # candidate set, which the untargeted search does not expose here
        # (other_candidates carries formulas only, no per-candidate fit); a run
        # that asks for it measures the shortlist once the ledger is judged
        # (`alternatives_scoring.seed_shortlists`, the run option
        # `stage_b_confidence`). The scale objection to P(correct) is gone: the
        # fit below is the seeded re-score, the same ion_score_v2 the Stage A
        # curve was fitted on, so the curve would no longer be borrowed across
        # scales. Whether a Stage B row should carry a probability is the
        # confidence layer's call and not this conversion's, so nothing here
        # starts asserting one.
        evidence = round(winner["fit"] * winner["plausibility"], 4)
        provenance = {
            "plausibility": winner["plausibility"],
            "evidence": evidence,
            # True of this row rather than aspirational: the finder elected it
            # with the v2 fit and the re-score below measured it with the same
            # one, so there is no second number to name (decision 12).
            "score_version": SCORE_VERSION,
            **cut["provenance"],
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

    return assignments
