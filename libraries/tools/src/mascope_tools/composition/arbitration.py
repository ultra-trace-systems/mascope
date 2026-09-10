"""Candidate arbitration (assignment-confidence Phase 3, P2).

The fit score measures how well the data fit ONE candidate; it is competitor-blind
(``fit_score.md``). Arbitration is the layer that, for a single peak, *competes* the
candidates that all fit the mass and decides which is the real assignment -- exactly
the problem the field frames as "accurate mass alone cannot determine a composition"
(Kind & Fiehn 2006). Here we combine two independent, one-directional pieces of
evidence:

- **fit** -- ``score_pattern_v2`` (mass + isotope pattern + SNR detectability), and
- **chemical plausibility** -- the graded Seven Golden Rules score
  (``heuristic_filter.formula_plausibility``),

as their product (``evidence = fit x plausibility``), then reports a per-candidate
**confidence** (the evidence normalised across the peak's candidates) and is honest
about **ties** -- when two candidates are within ``tie_tol`` we say so rather than
inventing a winner (Schymanski et al. 2014, L5).

Design rule (``assignment_confidence.md`` S3): the dependency points one way. This
layer imports the fit score's *values* and the chemistry plausibility; neither of
those imports arbitration. Calibrating the confidence to a true P(correct) per
instrument and a target-decoy FDR are the remaining P2 work; the ranking + the honest
tie report are here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from mascope_tools.composition.heuristic_filter import formula_plausibility


# Two candidates whose evidence is within this gap of the best are reported as a tie
# rather than ranked as a confident winner/loser. The gap is RELATIVE (a fraction of the
# best evidence), not absolute: evidence is a product of two [0, 1] factors and spans
# orders of magnitude between a well-supported peak and a marginal one, so one absolute
# gap means different things at different points on the scale. An absolute 0.05 called
# 0.02 vs 0.01 a tie -- one candidate with twice the support of the other -- while
# separating 0.95 from 0.89, which are 6% apart and genuinely ambiguous. 0.10 keeps the
# behaviour where winners actually live: at evidence 0.5-1.0 the old absolute 0.05 was
# 5-10% relative.
DEFAULT_TIE_TOL = 0.10

#: Column and key the finder records :func:`candidate_density` under, so the
#: engine, the schemas and the tiering all name it the same thing.
CANDIDATE_DENSITY = "candidate_density"

# ...floored in absolute terms, because a relative gap stops meaning anything once the
# evidence itself is noise: 0.0002 vs 0.0001 is a 100% relative gap between two
# candidates that both have essentially no support, and declaring a resolved winner
# there is exactly the overconfidence the tie report exists to prevent.
TIE_ABS_FLOOR = 0.005


@dataclass(frozen=True)
class ArbitratedCandidate:
    """One candidate after arbitration, for a single peak."""

    formula: str
    fit_score: float
    plausibility: float
    evidence: float  # fit_score * plausibility
    confidence: float  # evidence normalised across the peak's candidates -> [0, 1]
    rank: int  # 1 = best evidence
    is_tie: bool  # within tie_tol of the best evidence (an unresolved competitor)


def _as_formula_fit(candidate: Any) -> tuple[str, float]:
    """Accept a candidate as a ``{"formula", "fit_score"}`` mapping or a
    ``(formula, fit_score)`` pair. ``match_score`` is accepted as an alias for
    ``fit_score`` during the rename transition."""
    if isinstance(candidate, dict):
        formula = candidate.get("formula")
        fit = candidate.get("fit_score", candidate.get("match_score"))
    else:
        formula, fit = candidate
    try:
        fit = float(fit)
    except (TypeError, ValueError):
        fit = 0.0
    if math.isnan(fit) or fit < 0.0:  # NaN or negative -> no evidence
        fit = 0.0
    return str(formula), fit


def arbitrate_candidates(
    candidates: Iterable[Any],
    *,
    tie_tol: float = DEFAULT_TIE_TOL,
) -> list[ArbitratedCandidate]:
    """Compete a single peak's candidates by ``fit x plausibility``.

    :param candidates: The peak's candidates, each a ``{"formula", "fit_score"}``
        mapping (``match_score`` accepted as an alias) or a ``(formula, fit_score)``
        pair. ``formula`` is the NEUTRAL formula (plausibility is a neutral-chemistry
        judgement, as produced by ``find_compositions`` before ionization). Repeats of
        the same formula are collapsed (see below).
    :param tie_tol: Gap below the best evidence, as a FRACTION of that best evidence
        and floored at :data:`TIE_ABS_FLOOR`, at or below which a candidate is flagged
        a tie.
    :returns: The candidates as :class:`ArbitratedCandidate`, best evidence first, one
        entry per distinct formula. ``confidence`` sums to 1 across candidates with
        positive evidence; when no candidate has any evidence every ``confidence`` is 0
        and every ``is_tie`` is True (nothing to distinguish). Deterministic; ties
        broken by descending fit then formula for a stable order.

    The same neutral formula can reach one peak more than once -- via two adducts, or
    listed in two target collections -- and those are not competitors, they are one
    hypothesis arriving twice. Left uncollapsed they split the normalisation between
    themselves and then tie against each other, so a peak whose assignment is not in
    doubt reports 50% confidence and an unresolved tie with itself. Only the strongest
    arrival of each formula is kept; since plausibility is a pure function of the
    formula, that is the same as keeping its best evidence.
    """
    best_fit: dict[str, float] = {}
    for cand in candidates:
        formula, fit = _as_formula_fit(cand)
        if formula not in best_fit or fit > best_fit[formula]:
            best_fit[formula] = fit
    scored: list[tuple[str, float, float, float]] = []
    for formula, fit in best_fit.items():
        plaus = formula_plausibility(formula)
        scored.append((formula, fit, plaus, fit * plaus))
    if not scored:
        return []

    # Best evidence first; stable, deterministic tie-break (fit desc, then formula).
    scored.sort(key=lambda t: (-t[3], -t[1], t[0]))
    total = sum(t[3] for t in scored)
    best_evidence = scored[0][3]
    tie_gap = max(tie_tol * best_evidence, TIE_ABS_FLOOR)

    out: list[ArbitratedCandidate] = []
    for rank, (formula, fit, plaus, evidence) in enumerate(scored, start=1):
        confidence = evidence / total if total > 0 else 0.0
        # A candidate is "tied" when it sits within tie_gap of the best evidence and
        # is not the sole contender. When there is no evidence at all, everything ties.
        near_best = (best_evidence - evidence) <= tie_gap
        is_tie = near_best and (
            total <= 0
            or sum(1 for t in scored if (best_evidence - t[3]) <= tie_gap) > 1
        )
        out.append(
            ArbitratedCandidate(
                formula=formula,
                fit_score=fit,
                plausibility=plaus,
                evidence=evidence,
                confidence=confidence,
                rank=rank,
                is_tie=is_tie,
            )
        )
    return out


def candidate_density(
    candidates: Iterable[Any],
    *,
    around: str | None = None,
    tie_tol: float = DEFAULT_TIE_TOL,
) -> int:
    """How many distinct formulas a peak's own evidence cannot separate.

    The count the tie report already implies, named so a caller can read it
    without re-deriving the gap: 1 means the winner stands alone at the top of
    the arbitration, and anything above it is the number of hypotheses that
    explain the peak equally well. It is the honest form of "unique formula" -
    unique inside the element box that was enumerated, which is what
    :func:`mascope_tools.composition.degeneracy.measure_degeneracy` widens.

    Counted over DISTINCT formulas, because arbitration collapses a formula
    that reached the peak twice; two adducts of one neutral are one hypothesis
    arriving twice and were never competitors. Readings of the SAME ion are a
    different case and never reach here: the finder elects one of them before
    anything is ranked, so a family arrives as a single candidate.

    ``around`` anchors the count on ONE formula - the one the caller committed -
    and is what a caller that has committed something should pass. The two
    rankings need not agree: the finder orders by fit score with plausibility
    only as a third tie-break, its envelope filter can commit the second
    candidate, and arbitration orders by the product. Counted at the top
    instead, a density of 1 would say the ARBITRATION's best stands alone,
    which is not a statement about the row it is stored on. Anchored, it says
    how many formulas the evidence cannot separate from the committed one -
    including any that beat it, since a rival the evidence ranks above the
    commit is exactly what the count exists to surface.

    Computed in one pass rather than by arbitrating and counting the result.
    The finder asks this of every peak it searches, and on a dense spectrum
    building the full ranking per peak - a dataclass and a normalised confidence
    per candidate, sorted - costs about forty seconds a sample where the count
    itself costs nothing. :func:`density_of` is the same number read off an
    arbitration a caller already holds, and the two agree by test.

    :param candidates: The peak's candidates, in any form
        :func:`arbitrate_candidates` accepts.
    :param around: The formula to count around - the committed one. Omitted,
        the count is taken at the top of the arbitration, which is what a
        caller with nothing committed yet means. A formula not among the
        candidates counts as itself alone.
    :param tie_tol: The gap that counts as unresolved, as passed to
        :func:`arbitrate_candidates`.
    :return: How many distinct formulas the evidence cannot separate, at least
        1; 0 only when there were no candidates at all. When no candidate has
        any evidence every one of them ties, so the density is the whole list -
        nothing was measured, and saying "unique" there would be a claim the
        measurement did not make.
    """
    # One entry per DISTINCT formula, keeping its best evidence: the same
    # collapse `arbitrate_candidates` makes, and for the same reason - one
    # formula reaching a peak twice is one hypothesis, not two competitors.
    best_evidence: dict[str, float] = {}
    for candidate in candidates:
        formula, fit = _as_formula_fit(candidate)
        evidence = fit * formula_plausibility(formula)
        if formula not in best_evidence or evidence > best_evidence[formula]:
            best_evidence[formula] = evidence
    if not best_evidence:
        return 0
    evidences = list(best_evidence.values())
    best = max(evidences)
    if sum(evidences) <= 0:
        # Nothing was measured, so nothing was separated.
        return len(evidences)
    # The gap is a property of the peak - a fraction of its best evidence -
    # whichever candidate the count is anchored on, so moving the anchor never
    # changes how far apart two candidates have to be to count as separated.
    gap = max(tie_tol * best, TIE_ABS_FLOOR)
    if around is None:
        return max(1, sum(1 for evidence in evidences if best - evidence <= gap))
    anchor = best_evidence.get(str(around))
    if anchor is None:
        return 1
    return max(1, sum(1 for evidence in evidences if abs(evidence - anchor) <= gap))


def density_of(
    arbitrated: Sequence[ArbitratedCandidate], *, around: str | None = None
) -> int:
    """The same count, read off an arbitration a caller already has.

    Stage A arbitrates every peak to get its confidence and tie flag; asking
    :func:`candidate_density` there would compete the same candidates a second
    time for a number the first pass already knows.

    :param arbitrated: The output of :func:`arbitrate_candidates`.
    :param around: The committed formula, as in :func:`candidate_density`.
        Omitted, the count is taken at the top of the arbitration.
    :return: How many distinct formulas the evidence cannot separate, at least
        1; 0 for an empty arbitration.
    """
    if not arbitrated:
        return 0
    if around is None:
        return max(1, sum(1 for candidate in arbitrated if candidate.is_tie))
    best = max(candidate.evidence for candidate in arbitrated)
    if sum(candidate.evidence for candidate in arbitrated) <= 0:
        return len(arbitrated)
    gap = max(DEFAULT_TIE_TOL * best, TIE_ABS_FLOOR)
    anchor = next(
        (c.evidence for c in arbitrated if c.formula == str(around)),
        None,
    )
    if anchor is None:
        return 1
    return max(1, sum(1 for c in arbitrated if abs(c.evidence - anchor) <= gap))


# ---------------------------------------------------------------------------
# False-discovery-rate estimation for arbitrated assignments (P2).
#
# Given, across many peaks, each winner's confidence and whether it was correct
# (from a labelled golden set, or a target-decoy search where "wrong" = a decoy
# won), the FDR at a confidence cut is the fraction of accepted winners that are
# wrong. This is the established significance-estimation view of large-scale
# annotation (Scheubert et al. 2017): pick a confidence threshold for a tolerated
# FDR, report how many assignments survive. The q-value monotonises the raw FDR so
# a threshold can be chosen unambiguously.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FdrPoint:
    """One point on the FDR-vs-acceptance curve (peaks sorted by confidence)."""

    threshold: float  # confidence cut (accept winners with confidence >= this)
    n_accepted: int  # winners accepted at this cut
    fdr: float  # fraction of the accepted winners that are wrong
    q_value: float  # smallest FDR achievable while still accepting this cut


def fdr_curve(
    confidences: Sequence[float], is_correct: Sequence[bool]
) -> list[FdrPoint]:
    """FDR vs acceptance for arbitrated winners, sorted by descending confidence.

    At each cut the accepted set is the confidence-ordered prefix; ``fdr`` is the
    share of those winners that are wrong. ``q_value`` is the running minimum FDR
    from the largest acceptance upward, so it is monotone in the threshold and safe
    to threshold on (the standard target-decoy q-value). Winners tied on confidence
    are ordered WRONG FIRST, so a cut landing inside a tie charges it the wrong ones
    it cannot exclude rather than crediting it the corrects it cannot claim.
    """
    if len(confidences) != len(is_correct):
        raise ValueError("confidences and is_correct must be the same length")
    # Sort by confidence desc; within a tie put wrong first so the FDR at the tie is
    # not understated (conservative).
    order = sorted(
        range(len(confidences)),
        key=lambda i: (-float(confidences[i]), bool(is_correct[i])),
    )
    wrong = 0
    raw: list[list[float]] = []
    for rank, i in enumerate(order, start=1):
        if not is_correct[i]:
            wrong += 1
        raw.append([float(confidences[i]), rank, wrong / rank])
    # q-value: min FDR over all cuts at least as permissive as this one.
    q = 1.0
    points: list[FdrPoint] = [FdrPoint(0.0, 0, 0.0, 0.0)] * len(raw)
    for j in range(len(raw) - 1, -1, -1):
        q = min(q, raw[j][2])
        points[j] = FdrPoint(raw[j][0], int(raw[j][1]), raw[j][2], q)
    return points


def threshold_at_fdr(
    confidences: Sequence[float],
    is_correct: Sequence[bool],
    target_fdr: float,
) -> float | None:
    """Lowest confidence cut whose accepted set holds FDR (q-value) <= ``target_fdr``.

    Returns the confidence to threshold at (accept winners with confidence >= it) for
    the most permissive acceptance meeting the target, or ``None`` when no cut reaches
    the target FDR.
    """
    points = fdr_curve(confidences, is_correct)
    accepted = [p for p in points if p.q_value <= target_fdr]
    return accepted[-1].threshold if accepted else None
