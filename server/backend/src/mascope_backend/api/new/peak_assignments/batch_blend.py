"""A probable blend: a batch peak whose members disagree along intensity or time.

The consensus vote (``batch_peaks.vote_consensus``) reads how much an anchor's
assigned members agree on a formula; where the top two tie, or the winner holds
too few of them, it marks the anchor ``is_ambiguous`` and keeps the runner-up
as an alternative. What it does not read is *how* they disagree. Where the
members that carry one formula are the bright ones and those that carry the
other the dim ones, or the one formula holds the batch's early samples and the
other its late ones, the disagreement is not scatter: two chemistries share
one m/z trace, each dominating where it is the stronger, and the vote's one
answer averages them. This module tests the two formulas the vote ranked first
for exactly that, after the vote and with the members it read
(step 4.8 of the assignment quality plan, MAS-320):

- **Intensity** - a Mann-Whitney rank test of the two formulas' member
  intensities: whether one formula's members are systematically the brighter.
  The separation is the probability a member of the winner is brighter than
  one of the runner-up (U over n1 x n2); 0.5 is no separation.
- **Acquisition time** - a Wald-Wolfowitz runs test of the two formulas'
  labels in acquisition order: whether they come in fewer runs than an
  interleaving would give, one-sided. The members are ordered as the
  continuity pass orders samples (time, a segment's start in its file, id).

Two things are not tested, as neither is two chemistries. Two formulas whose
members read one ion - one neutral through two mechanisms, which a change of
the source's reagent switches between - are one ion on the trace, however
cleanly they part. And a member below assignability does not commit to its
formula: the engine declined to assign it. Read on the testbed's batch
ledgers, the first was seven in ten of one batch's flags, a block of early
samples read through another adduct, and the second a third of what was
left, mostly dim members a weak fit had flipped to another formula. So the
two sides are the members at candidate or better, and the two formulas must
name different ions.

A clean separation on either axis - significant at :data:`BLEND_ALPHA` and
past the axis' own effect bar - marks the anchor a probable blend with the two
formulas, their ions and the axis: a reason recorded on the anchor, which the
ledger and the chart show. Nothing reads it back: the tier, the vote and ``is_ambiguous``
stay as they were. Splitting such an anchor into two traces is the second half
of the step, built once the flag has been read on real batches.

Pure: no DB. Every threshold is provisional, a first-principles starting point
until the flag has been counted on real batches (the plan's step 4.8 row).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Optional

from scipy.stats import mannwhitneyu

from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
)


#: The key a probable blend is recorded under in ``batch_peak.provenance``.
BLEND_KEY = "blend"

#: The two axes a blend can separate along.
AXIS_INTENSITY = "intensity"
AXIS_TIME = "time"

#: Provisional. The fewest members each of the two formulas must have for
#: either test to run. Under three a side, no ordering of the members is
#: significant at :data:`BLEND_ALPHA` anyway; the floor says so up front.
BLEND_MIN_MEMBERS = 3
#: Provisional. The significance either test must reach: two-sided for the
#: rank test, one-sided (too few runs) for the runs test.
BLEND_ALPHA = 0.01
#: Provisional. How cleanly the intensities must separate beside being
#: significant: the probability that a member of one formula is the brighter
#: of a pair across the two, at least this or at most one less it. A large
#: batch makes a slight shift significant; a blend is two populations that
#: barely overlap.
BLEND_MIN_SEPARATION = 0.9
#: Provisional. The most runs a time separation may come in, as a share of the
#: runs an interleaving would give on average: a blend in time is the two
#: formulas in a few blocks, not a slight clustering a large batch makes
#: significant.
BLEND_MAX_RUNS_SHARE = 0.5
#: The tiers at which a member commits to its formula and enters the test.
BLEND_TIERS = frozenset({TIER_ASSIGNED, TIER_CANDIDATE})


def _get(member: Any, key: str, default=None):
    if isinstance(member, dict):
        return member.get(key, default)
    return getattr(member, key, default)


def _significant(p: float) -> float:
    """A p-value to three significant digits: what the record keeps, so a
    recompute over the same members writes nothing."""
    return float(f"{p:.3g}")


def intensity_reading(first: list, second: list) -> Optional[dict]:
    """The rank test of two formulas' member intensities, or None where either
    has fewer than :data:`BLEND_MIN_MEMBERS` members carrying an intensity.

    :param first: The first formula's members.
    :param second: The second formula's members.
    :return: ``p`` (two-sided), ``separation`` (the probability a member of
        the first is the brighter of a pair across the two) and ``separates``.
    """
    a = [float(v) for v in (_get(m, "intensity") for m in first) if v is not None]
    b = [float(v) for v in (_get(m, "intensity") for m in second) if v is not None]
    if len(a) < BLEND_MIN_MEMBERS or len(b) < BLEND_MIN_MEMBERS:
        return None
    result = mannwhitneyu(a, b, alternative="two-sided")
    p = float(result.pvalue)
    separation = float(result.statistic) / (len(a) * len(b))
    clean = max(separation, 1.0 - separation) >= BLEND_MIN_SEPARATION
    return {
        "p": _significant(p),
        "separation": round(separation, 4),
        "separates": bool(p <= BLEND_ALPHA and clean),
    }


def runs_p_value(n1: int, n2: int, runs: int) -> float:
    """The exact probability of ``runs`` runs or fewer in a random arrangement
    of ``n1`` items of one kind and ``n2`` of another (Wald and Wolfowitz).

    Counted, not approximated: the batches a blend is read on are often too
    small for the normal approximation, and Python's integers carry the binomial
    coefficients of a batch of thousands exactly.
    """
    total = math.comb(n1 + n2, n1)
    count = 0
    for r in range(2, runs + 1):
        k, odd = divmod(r, 2)
        if not odd:
            count += 2 * math.comb(n1 - 1, k - 1) * math.comb(n2 - 1, k - 1)
        else:
            count += math.comb(n1 - 1, k) * math.comb(n2 - 1, k - 1) + math.comb(
                n1 - 1, k - 1
            ) * math.comb(n2 - 1, k)
    return min(1.0, count / total)


def _acquisition_key(member: Any) -> tuple:
    return (
        _get(member, "acquired"),
        _get(member, "t0") or 0.0,
        _get(member, "sample_item_id") or "",
    )


def time_reading(first: list, second: list) -> Optional[dict]:
    """The runs test of two formulas' members in acquisition order, or None
    where either has fewer than :data:`BLEND_MIN_MEMBERS` members with an
    acquisition time.

    :param first: The first formula's members.
    :param second: The second formula's members.
    :return: ``p`` (one-sided: this few runs or fewer), ``runs``,
        ``expected_runs`` under interleaving, ``first_earlier`` (whether the
        first formula's members sit earlier on average) and ``separates``.
    """
    timed = [(m, 0) for m in first if _get(m, "acquired") is not None] + [
        (m, 1) for m in second if _get(m, "acquired") is not None
    ]
    n1 = sum(1 for _, side in timed if side == 0)
    n2 = len(timed) - n1
    if n1 < BLEND_MIN_MEMBERS or n2 < BLEND_MIN_MEMBERS:
        return None
    timed.sort(key=lambda pair: _acquisition_key(pair[0]))
    sides = [side for _, side in timed]
    runs = 1 + sum(1 for x, y in zip(sides, sides[1:]) if x != y)
    expected = 1.0 + 2.0 * n1 * n2 / (n1 + n2)
    p = runs_p_value(n1, n2, runs)
    first_rank = sum(i for i, side in enumerate(sides) if side == 0) / n1
    second_rank = sum(i for i, side in enumerate(sides) if side == 1) / n2
    clean = runs <= BLEND_MAX_RUNS_SHARE * expected
    return {
        "p": _significant(p),
        "runs": runs,
        "expected_runs": round(expected, 2),
        "first_earlier": first_rank < second_rank,
        "separates": bool(p <= BLEND_ALPHA and clean),
    }


def _modal_ion(members: list) -> Optional[str]:
    """The ion most of ``members`` read, the first seen on a tie; None for
    none."""
    counts = Counter(_get(m, "ion_formula") for m in members)
    counts.pop(None, None)
    return counts.most_common(1)[0][0] if counts else None


def blend_of(
    first_formula: str,
    first: Iterable[Any],
    second_formula: str,
    second: Iterable[Any],
) -> Optional[dict]:
    """The probable-blend record of an anchor, or None where neither axis
    separates its two leading formulas cleanly.

    Each side is the members at a tier of :data:`BLEND_TIERS`; two formulas
    whose sides' most common ions are one are not tested.

    :param first_formula: The vote's winner (or the pinned formula).
    :param first: The assigned members that carry it.
    :param second_formula: The formula ranked next by the vote.
    :param second: The assigned members that carry it.
    :return: ``formulas`` (the two, the winner first), ``ions`` (each side's
        most common ion), ``n`` (each side's members at candidate or better),
        ``axis`` (the separating axis, the more significant where both
        separate), and each axis' reading under ``intensity`` and ``time`` -
        None where it could not be tested - with the formula on its bright
        side (``brighter``) or its early side (``earlier``).
    """
    first = [m for m in first if _get(m, "tier") in BLEND_TIERS]
    second = [m for m in second if _get(m, "tier") in BLEND_TIERS]
    ions = [_modal_ion(first), _modal_ion(second)]
    if ions[0] is not None and ions[0] == ions[1]:
        return None
    intensity = intensity_reading(first, second)
    time = time_reading(first, second)
    separating = [
        (reading["p"], axis)
        for axis, reading in ((AXIS_INTENSITY, intensity), (AXIS_TIME, time))
        if reading is not None and reading["separates"]
    ]
    if not separating:
        return None
    if intensity is not None:
        intensity["brighter"] = (
            first_formula if intensity["separation"] >= 0.5 else second_formula
        )
    if time is not None:
        time["earlier"] = first_formula if time.pop("first_earlier") else second_formula
    return {
        "formulas": [first_formula, second_formula],
        "ions": ions,
        "n": [len(first), len(second)],
        "axis": min(separating)[1],
        AXIS_INTENSITY: intensity,
        AXIS_TIME: time,
    }


def blend_record_of(anchor: Any) -> Optional[dict]:
    """The probable-blend record an anchor's consensus left, or None.

    Computed afresh by every consensus, so it always describes the members the
    anchor holds now; no staleness to test.
    """
    provenance = _get(anchor, "provenance") or {}
    record = provenance.get(BLEND_KEY) if isinstance(provenance, dict) else None
    return record if isinstance(record, dict) else None
