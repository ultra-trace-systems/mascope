"""Ratio stability on the batch ledger: step 4.11 of the assignment plan.

Every per-sample effect that scales a whole spectrum - the source's strength,
its drift, the scan count, a dilution - multiplies both peaks of a related
pair, so their within-sample ratio cancels it point by point. An isotope
line's ratio to its M0 is fixed by isotopic abundance and a neutral's two
channels' ratio by the source's chemistry: a species that is what it claims to
be holds a flat ratio across a batch while its own trace swings by orders of
magnitude. This module reads that ratio for every pair of related batch peaks
of a time-ordered batch and grades it. It says whether two peaks behave as one
species; it never says which species.

Under decision 35 (MAS-709) the batch's primary reading of a time series is
the co-variation of the log traces (step 4.2, ``batch_coherence.py``), and
this is its diagnostic: a smooth drift of the ratio while the two traces
co-vary says the channel chemistry moved and the assignment stands; a drift
nothing says is shared, or a ratio that jumps, says two species may share a
trace. The grade is display-only: no tier, reason or level reads it until the
null model of step 4.13 has given it a likelihood ratio. The correlation it
carries for a channel pair is 4.2's reading, read off the anchors' records,
never recomputed here.

**The pairs** (:func:`form_pairs`) are keyed on batch peaks, never on a
formula string:

- *An isotope line and its M0.* The line's ``isotopologue_of``, walked to its
  family's root by the rule the batch ledger draws the family with
  (:func:`root_parent_id`), so a chained line pairs with the row it is drawn
  under. A line whose link is NULL forms no pair: the column cannot tell an
  M0 from a split vote. Both must read the same formula.
- *Two channels of one neutral*: the pairs 4.2 reads
  (``batch_coherence.partition_anchors``: monoisotopic, unclaimed, per ionization
  mode and instrument type, a formula through a mechanism), one batch peak per
  channel - the brightest, where two read the neutral through one channel -
  and neither ambiguous.

Both peaks of either kind must be ``assigned`` or ``candidate``: a ratio is a
joint statistic, and a noise peak must not make a stability claim.

**The series** (:func:`ratio_series`): ``ln(I_b / I_a)`` over the samples
both members are in, from the members' ``intensity``, in acquisition order.
A sample is left out where it is a blank (labelled ``BLANK``, or with no
instrument function), where either intensity is zero or missing, or where
either member dissents: its registry entry (its ``candidate`` index) names
another formula than its batch peak's consensus. An absence is a gap, never a
point against the formula.

**The reading** (:func:`read_ratio`): the median, the spread (1.4826 times the
median absolute deviation, robust to a spiked sample; Hampel, J. Am. Stat.
Assoc. 1974, 69, 383; Rousseeuw and Croux, J. Am. Stat. Assoc. 1993, 88,
1273), and the drift, which needs both a monotonicity leg (Spearman's rho of
the ratio against the acquisition order; Spearman, Am. J. Psychol. 1904, 15,
72) and an effect-size leg (the Theil-Sen slope against elapsed time times the
pair's span, in log units so a rise and an equal fall trip alike; Theil, Proc.
K. Ned. Akad. Wet. 1950, 53, 386; Sen, J. Am. Stat. Assoc. 1968, 63, 1379).

**The batch** must be time-ordered (:func:`time_ordering`): an ACQUISITION
batch is; an ANALYSIS batch is where its largest gap between distinct
acquisition times is at most ``MAX_GAP_FACTOR`` times the median gap. A batch
that is not has its grades withdrawn, with the reason, rather than left
standing.

**The pass** (:func:`run_batch_continuity`) runs after the folds, over the
whole batch, never inside a fold: its unit is a pair of anchors, every new
sample moves every pair's series, and it needs the acquisition times, a join
the fold does not make. It is an idempotent overwrite under a lock of its own,
taken with ``pg_try_advisory_xact_lock`` so a second pass on the batch skips
rather than queues. It runs from its endpoint, at the end of a ledger rebuild,
and after a fold where ``CONTINUITY_ON_FOLD`` says so (off until step 4.13 has
measured its cost). A grade the ledger has moved past since is served marked
(``batch_peaks.continuity_stale``).

The gates are constants beside the consensus constants in ``batch_peaks.py``,
every one provisional. Design: the continuity note, sections 1, 2, 8 and 9
(``docs/dev/peak_assignment_continuity.md``, in git history before
2026-10-22), and ``docs/dev/assignment_quality_plan.md``, step 4.11.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

import numpy as np
from scipy.stats import rankdata
from sqlalchemy import bindparam, func, select, update

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_coherence import (
    partition_anchors,
    partition_of,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    CONTINUITY_GRADES,
    DRIFT_LOG_RED,
    GRADE_DRIFT,
    GRADE_INCONCLUSIVE,
    GRADE_INSUFFICIENT,
    GRADE_RED,
    GRADE_STABLE,
    MAX_GAP_FACTOR,
    MAX_PAIRS_PER_ANCHOR,
    MAX_PAIRS_PER_BATCH,
    MIN_DISTINCT_TIMES,
    MIN_DRIFT_SAMPLES,
    MIN_PAIRED_SAMPLES,
    PAIR_ADDUCT,
    PAIR_ISOTOPOLOGUE,
    RHO_DRIFT,
    SPREAD_RED,
    SPREAD_STABLE,
    resolve_candidate,
    standing_co_variation_record,
)
from mascope_backend.api.new.peak_assignments.batch_runs import touch_ledger
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    Sample,
    SampleBatch,
    async_session,
)
from mascope_tools.composition.coherence import VERDICT_CO_VARYING


#: The version of the detail's shape.
DETAIL_VERSION = 1

#: The tiers a pair's two peaks must both hold.
PAIRED_TIERS = (TIER_ASSIGNED, TIER_CANDIDATE)

#: 1.4826 x MAD estimates a normal standard deviation.
MAD_TO_SIGMA = 1.4826

#: The batch types and the sample type the gates read.
BATCH_ACQUISITION = "ACQUISITION"
SAMPLE_BLANK = "BLANK"

#: Why a batch is not time-ordered.
REASON_FEW_TIMES = "few_times"
REASON_GAP = "gap"

#: Why a pair got its grade.
REASON_FEW_SAMPLES = "few_samples"
REASON_DRIFT = "drift"
REASON_DRIFT_CO_VARYING = "drift_co_varying"
REASON_SPREAD = "spread"
REASON_TIGHT = "tight"
REASON_UNTESTED_DRIFT = "drift_untested"
REASON_TIED = "tied"
REASON_BETWEEN = "between"

#: The warning an anchor carries when the batch had more pairs than one pass
#: grades and some of its own were left ungraded.
WARNING_PARTIAL = "partial"

#: What the pass reports when it did not run.
SKIPPED_IN_PROGRESS = "in_progress"
SKIPPED_CURRENT = "current"


# --- the batch's time axis -----------------------------------------------------


@dataclass(frozen=True)
class SampleTime:
    """When a sample was acquired, and whether the pass reads it.

    :ivar sample_item_id: The sample.
    :ivar acquired: Its file's acquisition time (``SampleFile.datetime_utc``,
        the column for calculations; not the instrument's local time).
    :ivar t0: Where its segment starts in the file, which orders the segments
        of one file; None for a whole file.
    :ivar blank: A blank: labelled one, or a file with no instrument function.
    """

    sample_item_id: str
    acquired: datetime
    t0: Optional[float] = None
    blank: bool = False


def acquisition_order(samples: Iterable[SampleTime]) -> dict[str, int]:
    """Sample -> its place in acquisition order: by acquisition time, the
    segments of one file by where they start, then by id so the order is
    total."""
    ordered = sorted(samples, key=lambda s: (s.acquired, s.t0 or 0.0, s.sample_item_id))
    return {s.sample_item_id: index for index, s in enumerate(ordered)}


def time_ordering(
    batch_type: Optional[str], samples: Iterable[SampleTime]
) -> tuple[bool, Optional[str], dict]:
    """Whether a batch is time-ordered, the reason where it is not, and its
    time axis's facts.

    An ACQUISITION batch is: it is made per day of continuous acquisition.
    Any other is where its largest gap between distinct acquisition times is
    at most :data:`MAX_GAP_FACTOR` times the median one. The gaps are between
    distinct times, not samples: the segments of one file share its time, and
    a median of zero would refuse every batch of segmented files. A batch with
    fewer than :data:`MIN_DISTINCT_TIMES` distinct times is not, as no pair
    could pass that gate anyway. Blanks are left out.

    :return: ``(time_ordered, reason, facts)``.
    """
    distinct = sorted({s.acquired for s in samples if not s.blank})
    facts: dict[str, Any] = {
        "distinct_times": len(distinct),
        "max_gap_seconds": None,
        "median_gap_seconds": None,
    }
    if len(distinct) >= 2:
        gaps = np.diff([d.timestamp() for d in distinct])
        facts["max_gap_seconds"] = round(float(gaps.max()), 3)
        facts["median_gap_seconds"] = round(float(np.median(gaps)), 3)
    if batch_type == BATCH_ACQUISITION:
        return True, None, facts
    if len(distinct) < MIN_DISTINCT_TIMES:
        return False, REASON_FEW_TIMES, facts
    if facts["max_gap_seconds"] > MAX_GAP_FACTOR * facts["median_gap_seconds"]:
        return False, REASON_GAP, facts
    return True, None, facts


# --- the pairs -------------------------------------------------------------------


@dataclass(frozen=True)
class Pair:
    """Two related batch peaks whose ratio is read: ``b`` over ``a``.

    :ivar kind: :data:`PAIR_ISOTOPOLOGUE` or :data:`PAIR_ADDUCT`.
    :ivar a: The denominator: an isotope line's M0, or the lower id of two
        channels.
    :ivar b: The numerator: the isotope line, or the other channel.
    :ivar support: The smaller of the two prevalences, which orders the pairs
        a pass grades first.
    """

    kind: str
    a: str
    b: str
    support: int


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def root_parent_id(anchor: Any, index: Mapping[str, Any]) -> Optional[str]:
    """The family root an isotope line is drawn under, or None.

    The batch ledger's own rule (``rootParentId`` in
    ``PaneBrowserBatchPeaks.vue``), so a line pairs with the row it is drawn
    under: walk ``isotopologue_of`` to an anchor that has none; a link to an
    anchor the batch does not hold, or a cycle, ends the walk with no parent.

    :param anchor: The anchor.
    :param index: Batch peak id -> anchor, the whole batch.
    """
    seen = {_get(anchor, "batch_peak_id")}
    parent_id = _get(anchor, "isotopologue_of")
    while parent_id and parent_id not in seen:
        parent = index.get(parent_id)
        if parent is None:
            return None
        if not _get(parent, "isotopologue_of"):
            return parent_id
        seen.add(parent_id)
        parent_id = _get(parent, "isotopologue_of")
    return None


def _paired_tier(anchor: Any) -> bool:
    return _get(anchor, "consensus_tier") in PAIRED_TIERS


def _brighter(anchor: Any, other: Any) -> bool:
    """Whether ``anchor`` reads its channel ahead of ``other``: the brighter,
    the lower id on a tie."""
    mine = _get(anchor, "max_intensity") or 0.0
    theirs = _get(other, "max_intensity") or 0.0
    if mine != theirs:
        return mine > theirs
    return _get(anchor, "batch_peak_id") < _get(other, "batch_peak_id")


def form_pairs(anchors: Iterable[Any], notation_by_id: Mapping[str, str]) -> list[Pair]:
    """Every pair of the batch whose ratio is read, the best supported first.

    :param anchors: The batch's anchors (``BatchPeak`` rows or anything with
        their attributes).
    :param notation_by_id: Mechanism id -> its notation, which names a channel.
    :return: The pairs, by support (descending), isotope pairs before channel
        pairs at equal support, then by id.
    """
    anchors = list(anchors)
    index = {_get(a, "batch_peak_id"): a for a in anchors}
    pairs: list[Pair] = []

    def support(a: Any, b: Any) -> int:
        return min(_get(a, "n_present") or 0, _get(b, "n_present") or 0)

    for line in anchors:
        if not _get(line, "isotopologue_of"):
            continue
        root = root_parent_id(line, index)
        if root is None:
            continue
        parent = index[root]
        if (
            partition_of(parent) != partition_of(line)
            or not (_paired_tier(parent) and _paired_tier(line))
            or _get(line, "consensus_formula") != _get(parent, "consensus_formula")
        ):
            continue
        pairs.append(
            Pair(
                PAIR_ISOTOPOLOGUE,
                root,
                _get(line, "batch_peak_id"),
                support(parent, line),
            )
        )

    for part in partition_anchors(anchors, dict(notation_by_id)).values():
        for _, channels in sorted(part.neutrals.items()):
            # One batch peak per channel: two through one channel are the same
            # evidence twice, and the brighter reads it.
            held: dict[str, Any] = {}
            for channel in channels:
                anchor = index[channel.batch_peak_id]
                if not _paired_tier(anchor) or _get(anchor, "is_ambiguous"):
                    continue
                current = held.get(channel.channel)
                if current is None or _brighter(anchor, current):
                    held[channel.channel] = anchor
            readers = [held[notation] for notation in sorted(held)]
            for i, first in enumerate(readers):
                for second in readers[i + 1 :]:
                    a, b = sorted(
                        (first, second), key=lambda x: _get(x, "batch_peak_id")
                    )
                    pairs.append(
                        Pair(
                            PAIR_ADDUCT,
                            _get(a, "batch_peak_id"),
                            _get(b, "batch_peak_id"),
                            support(a, b),
                        )
                    )

    order = {PAIR_ISOTOPOLOGUE: 0, PAIR_ADDUCT: 1}
    return sorted(pairs, key=lambda p: (-p.support, order[p.kind], p.a, p.b))


# --- the series ------------------------------------------------------------------


@dataclass
class RatioSeries:
    """A pair's log ratio, sample by sample, in acquisition order.

    :ivar log_ratio: ``ln(I_b / I_a)`` per counted sample.
    :ivar seconds: Each one's acquisition time, in seconds.
    :ivar order: Each one's place in the batch's acquisition order.
    :ivar n_both: The samples both members are in.
    :ivar n_excluded_blank: Of those, the blanks.
    :ivar n_excluded_dissenting: The samples where a member reads another
        formula than its batch peak's consensus.
    :ivar n_excluded_zero: The samples where an intensity is zero or missing.
    """

    log_ratio: list[float]
    seconds: list[float]
    order: list[int]
    n_both: int = 0
    n_excluded_blank: int = 0
    n_excluded_dissenting: int = 0
    n_excluded_zero: int = 0


def member_reads_consensus(anchor: Any, candidate: Optional[int]) -> bool:
    """Whether a member reads its batch peak's consensus formula: the registry
    entry its ``candidate`` index names carries that formula. An unassigned
    member, or one whose entry names another formula, dissents."""
    formula = _get(anchor, "consensus_formula")
    if not formula:
        return False
    entry = resolve_candidate(_get(anchor, "candidates"), candidate)
    return entry.get("formula") == formula


def _positive(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def ratio_series(
    pair: Pair,
    anchor_a: Any,
    anchor_b: Any,
    members: Mapping[str, Mapping[str, tuple]],
    samples: Mapping[str, SampleTime],
    order: Mapping[str, int],
) -> RatioSeries:
    """A pair's log ratio over the samples that count.

    :param members: Batch peak id -> sample -> ``(intensity, candidate)``.
    :param samples: Sample -> its time; a sample not in it is left out as a
        blank, since the pass cannot place it.
    :param order: Sample -> its place in acquisition order.
    """
    side_a = members.get(pair.a, {})
    side_b = members.get(pair.b, {})
    shared = sorted(side_a.keys() & side_b.keys(), key=lambda s: order.get(s, -1))
    series = RatioSeries(log_ratio=[], seconds=[], order=[], n_both=len(shared))
    for sample_item_id in shared:
        sample = samples.get(sample_item_id)
        if sample is None or sample.blank:
            series.n_excluded_blank += 1
            continue
        intensity_a, candidate_a = side_a[sample_item_id]
        intensity_b, candidate_b = side_b[sample_item_id]
        if not (
            member_reads_consensus(anchor_a, candidate_a)
            and member_reads_consensus(anchor_b, candidate_b)
        ):
            series.n_excluded_dissenting += 1
            continue
        if not (_positive(intensity_a) and _positive(intensity_b)):
            series.n_excluded_zero += 1
            continue
        series.log_ratio.append(math.log(intensity_b / intensity_a))
        series.seconds.append(sample.acquired.timestamp())
        series.order.append(order[sample_item_id])
    return series


# --- the reading -----------------------------------------------------------------


@dataclass(frozen=True)
class RatioReading:
    """What one pair's ratio series says, in the direction ``b`` over ``a``.

    :ivar grade: One of ``batch_peaks.CONTINUITY_GRADES``.
    :ivar reason: Why it is that grade.
    :ivar n: The samples counted.
    :ivar n_times: The distinct acquisition times they span.
    :ivar median_log: The median log ratio; None under the gates.
    :ivar sigma_log: 1.4826 x its median absolute deviation; None under the
        gates.
    :ivar rho: Spearman's rho against the acquisition order, where the drift
        test ran and the ratio moves at all.
    :ivar drift_log: The Theil-Sen slope against elapsed time times the span,
        signed, where the drift test ran.
    :ivar span_seconds: The time between the first and the last counted
        sample.
    :ivar drift_tested: Whether the drift test ran.
    """

    grade: str
    reason: str
    n: int
    n_times: int
    median_log: Optional[float] = None
    sigma_log: Optional[float] = None
    rho: Optional[float] = None
    drift_log: Optional[float] = None
    span_seconds: Optional[float] = None
    drift_tested: bool = False


def spearman_rho(values: np.ndarray, order: np.ndarray) -> Optional[float]:
    """Spearman's rho of ``values`` against ``order``: Pearson's r of their
    ranks, ties at their average rank. None where either does not vary."""
    ranks_v = rankdata(values)
    ranks_o = rankdata(order)
    if np.ptp(ranks_v) == 0 or np.ptp(ranks_o) == 0:
        return None
    return float(np.corrcoef(ranks_v, ranks_o)[0, 1])


def theil_sen_slope(values: np.ndarray, seconds: np.ndarray) -> float:
    """The median of the slopes between every two samples at distinct times
    (Theil 1950, Sen 1968): two segments of one file share a time and give no
    slope. Zero where no two times differ."""
    dt = seconds[None, :] - seconds[:, None]
    dv = values[None, :] - values[:, None]
    later = dt > 0
    if not later.any():
        return 0.0
    return float(np.median(dv[later] / dt[later]))


def read_ratio(
    series: RatioSeries, kind: str, *, co_varying: bool = False
) -> RatioReading:
    """Grade one pair's ratio series.

    In this order: under the gates, ``insufficient``; a drift - both legs, on
    at least :data:`MIN_DRIFT_SAMPLES` samples - is ``drift`` where 4.2's
    reading says the two traces co-vary, ``red`` otherwise; a spread at or
    above :data:`SPREAD_RED` is ``red``; a spread at or under the kind's
    :data:`SPREAD_STABLE`, with the drift tested and the median absolute
    deviation above zero, is ``stable``; anything else is ``inconclusive``.
    A stability claim needs the drift test, so a pair too short for it is
    never ``stable``; and a deviation of zero, where most of the ratios tie,
    is not precision.

    :param series: The pair's series (:func:`ratio_series`).
    :param kind: :data:`PAIR_ISOTOPOLOGUE` or :data:`PAIR_ADDUCT`.
    :param co_varying: Whether the batch's co-variation reading says the
        pair's two traces co-vary. An isotope pair has none: 4.2 reads
        channels, not isotope lines.
    """
    values = np.asarray(series.log_ratio, dtype=float)
    seconds = np.asarray(series.seconds, dtype=float)
    n = int(values.size)
    n_times = len(set(series.seconds))
    if n < MIN_PAIRED_SAMPLES or n_times < MIN_DISTINCT_TIMES:
        return RatioReading(GRADE_INSUFFICIENT, REASON_FEW_SAMPLES, n, n_times)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    sigma = MAD_TO_SIGMA * mad
    span = float(seconds.max() - seconds.min())
    tested = n >= MIN_DRIFT_SAMPLES
    rho = drift_log = None
    if tested:
        rho = spearman_rho(values, np.asarray(series.order, dtype=float))
        drift_log = theil_sen_slope(values, seconds) * span
    facts = dict(
        n=n,
        n_times=n_times,
        median_log=median,
        sigma_log=sigma,
        rho=rho,
        drift_log=drift_log,
        span_seconds=span,
        drift_tested=tested,
    )
    if (
        tested
        and rho is not None
        and abs(rho) >= RHO_DRIFT
        and abs(drift_log) >= DRIFT_LOG_RED[kind]
    ):
        if co_varying:
            return RatioReading(GRADE_DRIFT, REASON_DRIFT_CO_VARYING, **facts)
        return RatioReading(GRADE_RED, REASON_DRIFT, **facts)
    if sigma >= SPREAD_RED:
        return RatioReading(GRADE_RED, REASON_SPREAD, **facts)
    if not tested:
        return RatioReading(GRADE_INCONCLUSIVE, REASON_UNTESTED_DRIFT, **facts)
    if mad == 0:
        return RatioReading(GRADE_INCONCLUSIVE, REASON_TIED, **facts)
    if sigma <= SPREAD_STABLE[kind]:
        return RatioReading(GRADE_STABLE, REASON_TIGHT, **facts)
    return RatioReading(GRADE_INCONCLUSIVE, REASON_BETWEEN, **facts)


# --- the anchors' records ----------------------------------------------------------


def _severity(grade: Optional[str]) -> int:
    return (
        CONTINUITY_GRADES.index(grade)
        if grade in CONTINUITY_GRADES
        else len(CONTINUITY_GRADES)
    )


def rollup(grades: Iterable[str]) -> Optional[str]:
    """An anchor's grade: its most alarming pair's, None for no pair."""
    ranked = sorted(grades, key=_severity)
    return ranked[0] if ranked else None


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def pair_co_variation(pair: Pair, index: Mapping[str, Any]) -> Optional[dict]:
    """What the batch's co-variation reading (4.2) said of a channel pair:
    its verdict, r and count, read off either anchor's standing record; None
    for a pair it has not read, which an isotope pair never is."""
    for own, other in ((pair.a, pair.b), (pair.b, pair.a)):
        record = standing_co_variation_record(index.get(own))
        if not record:
            continue
        for partner in record.get("partners") or []:
            if partner.get("batch_peak_id") == other:
                return {
                    "verdict": partner.get("verdict"),
                    "r": partner.get("r"),
                    "n_spectra": partner.get("n_spectra"),
                }
    return None


def pair_entry(
    pair: Pair,
    own_id: str,
    reading: RatioReading,
    series: RatioSeries,
    index: Mapping[str, Any],
    co_variation: Optional[dict],
) -> dict:
    """A pair as one of its anchors records it: the partner over itself, so
    each anchor reads the ratio from its own side."""
    flip = own_id == pair.b
    partner_id = pair.a if flip else pair.b
    sign = -1.0 if flip else 1.0
    partner = index[partner_id]
    median = None if reading.median_log is None else sign * reading.median_log
    drift = None if reading.drift_log is None else sign * reading.drift_log
    rho = None if reading.rho is None else sign * reading.rho
    sigma = reading.sigma_log
    return {
        "kind": pair.kind,
        "partner_batch_peak_id": partner_id,
        "partner_mz": _round(_get(partner, "mz"), 6),
        "partner_ionization_mechanism_id": _get(partner, "ionization_mechanism_id"),
        "grade": reading.grade,
        "reason": reading.reason,
        "n": reading.n,
        "n_times": reading.n_times,
        "n_both": series.n_both,
        "n_excluded_blank": series.n_excluded_blank,
        "n_excluded_dissenting": series.n_excluded_dissenting,
        "n_excluded_zero": series.n_excluded_zero,
        "ratio_median": _round(None if median is None else math.exp(median), 6),
        "sigma_log": _round(sigma),
        "ratio_spread": _round(None if sigma is None else math.exp(sigma) - 1.0),
        "rho": _round(rho, 3),
        "drift_log": _round(drift),
        "drift_fold": _round(None if drift is None else math.exp(abs(drift))),
        "drift_tested": reading.drift_tested,
        "span_seconds": _round(reading.span_seconds, 1),
        "co_variation": co_variation,
    }


def _shown_first(entry: dict) -> tuple:
    """The detail lists the alarms first, then the best supported."""
    return (_severity(entry["grade"]), -entry["n"], entry["partner_batch_peak_id"])


def _claim(anchor: Any) -> dict:
    """What the pass graded the anchor as, which a reader holds the ledger
    against (``batch_peaks.continuity_stale``)."""
    return {
        "formula": _get(anchor, "consensus_formula"),
        "ionization_mechanism_id": _get(anchor, "ionization_mechanism_id"),
        "n_present_at_compute": _get(anchor, "n_present"),
    }


def grade_batch(
    anchors: Iterable[Any],
    notation_by_id: Mapping[str, str],
    members: Mapping[str, Mapping[str, tuple]],
    samples: Iterable[SampleTime],
    batch_type: Optional[str],
    *,
    computed_utc: str,
    pairs: Optional[list[Pair]] = None,
    max_pairs: Optional[int] = None,
) -> tuple[dict[str, tuple[Optional[str], dict]], dict]:
    """Grade every pair of a batch and roll the grades up onto the anchors.

    :param anchors: The batch's anchors.
    :param notation_by_id: Mechanism id -> notation.
    :param members: Batch peak id -> sample -> ``(intensity, candidate)``, for
        the anchors in a pair at least.
    :param samples: The batch's samples, with their times.
    :param batch_type: ``SampleBatch.sample_batch_type``.
    :param computed_utc: When the pass read the ledger, ISO 8601.
    :param pairs: The pairs, where the caller already formed them
        (:func:`form_pairs`).
    :param max_pairs: The pairs graded, :data:`MAX_PAIRS_PER_BATCH` unless
        given; the rest leave their anchors ``partial``.
    :return: Batch peak id -> ``(grade, detail)`` for every anchor in a pair,
        and the pass's summary.
    """
    anchors = list(anchors)
    index = {_get(a, "batch_peak_id"): a for a in anchors}
    if pairs is None:
        pairs = form_pairs(anchors, notation_by_id)
    if max_pairs is None:
        max_pairs = MAX_PAIRS_PER_BATCH
    samples = list(samples)
    by_sample = {s.sample_item_id: s for s in samples}
    ordered, reason, facts = time_ordering(batch_type, samples)
    summary: dict[str, Any] = {
        "batch_type": batch_type,
        "time_ordered": ordered,
        "reason": reason,
        **facts,
        "pairs": len(pairs),
        "pairs_by_kind": dict(Counter(p.kind for p in pairs)),
        "pairs_graded": 0,
        "pairs_skipped": 0,
        "grades": {grade: 0 for grade in CONTINUITY_GRADES},
        "anchors": {grade: 0 for grade in CONTINUITY_GRADES},
        "thresholds": {
            "min_paired_samples": MIN_PAIRED_SAMPLES,
            "min_distinct_times": MIN_DISTINCT_TIMES,
            "min_drift_samples": MIN_DRIFT_SAMPLES,
            "rho_drift": RHO_DRIFT,
            "drift_log_red": dict(DRIFT_LOG_RED),
            "spread_stable": dict(SPREAD_STABLE),
            "spread_red": SPREAD_RED,
            "max_pairs_per_batch": max_pairs,
            "max_gap_factor": MAX_GAP_FACTOR,
        },
    }
    pairs_of: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        pairs_of[pair.a].append(pair)
        pairs_of[pair.b].append(pair)

    records: dict[str, tuple[Optional[str], dict]] = {}
    if not ordered:
        # Withdrawn, with the reason, rather than left standing.
        for anchor_id, own in sorted(pairs_of.items()):
            records[anchor_id] = (
                None,
                {
                    "v": DETAIL_VERSION,
                    "computed_utc": computed_utc,
                    "eligible": False,
                    "reason": reason,
                    **_claim(index[anchor_id]),
                    "n_pairs": len(own),
                    "grades": {},
                    "warnings": [],
                    "pairs": [],
                },
            )
        return records, summary

    graded, skipped = pairs[:max_pairs], pairs[max_pairs:]
    summary["pairs_graded"] = len(graded)
    summary["pairs_skipped"] = len(skipped)
    order = acquisition_order(samples)
    entries: dict[str, list[dict]] = defaultdict(list)
    for pair in graded:
        series = ratio_series(
            pair, index[pair.a], index[pair.b], members, by_sample, order
        )
        co_variation = pair_co_variation(pair, index)
        reading = read_ratio(
            series,
            pair.kind,
            co_varying=bool(
                co_variation and co_variation.get("verdict") == VERDICT_CO_VARYING
            ),
        )
        summary["grades"][reading.grade] += 1
        for own_id in (pair.a, pair.b):
            entries[own_id].append(
                pair_entry(pair, own_id, reading, series, index, co_variation)
            )
    truncated = {anchor_id for pair in skipped for anchor_id in (pair.a, pair.b)}

    for anchor_id, own in sorted(pairs_of.items()):
        listed = sorted(entries.get(anchor_id, []), key=_shown_first)
        grade = rollup(entry["grade"] for entry in listed)
        if grade is not None:
            summary["anchors"][grade] += 1
        records[anchor_id] = (
            grade,
            {
                "v": DETAIL_VERSION,
                "computed_utc": computed_utc,
                "eligible": True,
                "reason": None,
                **_claim(index[anchor_id]),
                "n_pairs": len(own),
                "grades": dict(Counter(entry["grade"] for entry in listed)),
                "warnings": [WARNING_PARTIAL] if anchor_id in truncated else [],
                "pairs": listed[:MAX_PAIRS_PER_ANCHOR],
            },
        )
    return records, summary


# --- the pass --------------------------------------------------------------------

#: The pass's own advisory-lock namespace, hashed into the first int of the
#: two-int key space as the fold's is (``batch_peaks_controller``).
_CONTINUITY_LOCK_NAMESPACE = "mascope_batch_peak_continuity"

#: Anchors per member read, so the IN list stays inside asyncpg's bind
#: parameter cap however many anchors the batch pairs.
_MEMBER_CHUNK = 500


async def _try_continuity_lock(session, sample_batch_id: str) -> bool:
    """Take the batch's continuity lock for the rest of the transaction, or
    report that another pass holds it.

    Transaction-scoped like the fold's. Tried rather than waited for: the
    production workers are parallel processes no in-process gate reaches, and
    a pass that finds another running on the batch has nothing to add - the
    one running is an idempotent overwrite of the whole batch. Taken first,
    before anything is read: the pass opens no other session, so it can hold
    this while it waits for the fold lock to write.
    """
    return bool(
        (
            await session.execute(
                select(
                    func.pg_try_advisory_xact_lock(
                        func.hashtext(_CONTINUITY_LOCK_NAMESPACE),
                        func.hashtext(sample_batch_id),
                    )
                )
            )
        ).scalar()
    )


def _newest_computed(anchors: Iterable[Any]) -> Optional[datetime]:
    newest = None
    for anchor in anchors:
        detail = anchor.continuity
        stamp = detail.get("computed_utc") if isinstance(detail, dict) else None
        if not stamp:
            continue
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if newest is None or when > newest:
            newest = when
    return newest


async def _load(session, sample_batch_id: str):
    """The batch's type, anchors, mechanism notations and samples."""
    batch_type = (
        await session.execute(
            select(SampleBatch.sample_batch_type).where(
                SampleBatch.sample_batch_id == sample_batch_id
            )
        )
    ).scalar_one_or_none()
    anchors = (
        (
            await session.execute(
                select(BatchPeak).where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        )
        .scalars()
        .all()
    )
    mechanism_ids = sorted(
        {a.ionization_mechanism_id for a in anchors if a.ionization_mechanism_id}
    )
    notation_by_id = {
        mechanism_id: notation
        for mechanism_id, notation in (
            await session.execute(
                select(
                    IonizationMechanism.ionization_mechanism_id,
                    IonizationMechanism.ionization_mechanism,
                ).where(IonizationMechanism.ionization_mechanism_id.in_(mechanism_ids))
            )
        ).all()
        if notation
    }
    samples = [
        SampleTime(
            sample_item_id=row.sample_item_id,
            acquired=row.datetime_utc,
            t0=row.t0,
            # A labelled blank is assigned and folds like any sample, since
            # nothing ties the label to the file; a file with no instrument
            # function never folds, and is left out on the same terms.
            blank=row.sample_item_type == SAMPLE_BLANK
            or row.instrument_function_id is None,
        )
        for row in (
            await session.execute(
                select(
                    Sample.sample_item_id,
                    Sample.sample_item_type,
                    Sample.instrument_function_id,
                    Sample.datetime_utc,
                    Sample.t0,
                ).where(Sample.sample_batch_id == sample_batch_id)
            )
        ).all()
        if row.datetime_utc is not None
    ]
    return batch_type, list(anchors), notation_by_id, samples


async def _load_members(session, anchor_ids: Iterable[str]) -> dict:
    """Batch peak id -> sample -> ``(intensity, candidate)``, for these
    anchors only."""
    members: dict[str, dict[str, tuple]] = defaultdict(dict)
    ids = sorted(set(anchor_ids))
    for start in range(0, len(ids), _MEMBER_CHUNK):
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.batch_peak_id,
                    BatchPeakOccurrence.sample_item_id,
                    BatchPeakOccurrence.intensity,
                    BatchPeakOccurrence.candidate,
                ).where(
                    BatchPeakOccurrence.batch_peak_id.in_(
                        ids[start : start + _MEMBER_CHUNK]
                    )
                )
            )
        ).all()
        for batch_peak_id, sample_item_id, intensity, candidate in rows:
            members[batch_peak_id][sample_item_id] = (intensity, candidate)
    return members


def _settled(detail: Optional[dict]) -> Optional[dict]:
    """A detail without its stamp: two passes that reach one reading compare
    equal, so the second writes nothing."""
    if not isinstance(detail, dict):
        return detail
    return {key: value for key, value in detail.items() if key != "computed_utc"}


async def _write(
    session, anchors: Iterable[BatchPeak], records: Mapping[str, tuple]
) -> int:
    """Write every anchor whose grade or detail moved, and clear the anchors
    an earlier pass graded that this one did not. Returns the rows written.

    Through the table rather than the ORM rows: an anchor a fold deleted since
    the read is no row to update, and the statement simply matches nothing.
    Only the two columns move; ``batch_peak_utc_modified`` is the fold's.
    """
    rows = []
    for anchor in anchors:
        grade, detail = records.get(anchor.batch_peak_id, (None, None))
        if grade == anchor.continuity_grade and _settled(detail) == _settled(
            anchor.continuity
        ):
            continue
        rows.append({"b_id": anchor.batch_peak_id, "grade": grade, "detail": detail})
    if rows:
        table = BatchPeak.__table__
        await session.execute(
            update(table)
            .where(table.c.batch_peak_id == bindparam("b_id"))
            .values(
                continuity_grade=bindparam("grade"),
                continuity=bindparam("detail"),
            ),
            rows,
        )
    return len(rows)


async def run_batch_continuity(
    sample_batch_id: str, *, triggered_at: Optional[datetime] = None
) -> dict:
    """Grade the ratio stability of every pair of a batch and record it on the
    anchors: an idempotent overwrite of the whole batch.

    :param sample_batch_id: The batch.
    :param triggered_at: When the fold that triggered the pass committed. A
        pass that finds a grade read after it has nothing to add: the pass
        that wrote it saw the fold. None for a pass asked for outright.
    :return: The summary (:func:`grade_batch`), with ``written`` (anchors
        whose grade or detail moved) and ``seconds``; or ``skipped`` with why
        it did not run.
    """
    started = time.perf_counter()
    async with async_session() as session:
        if not await _try_continuity_lock(session, sample_batch_id):
            return {"skipped": SKIPPED_IN_PROGRESS}
        read_at = datetime.now(timezone.utc)
        batch_type, anchors, notation_by_id, samples = await _load(
            session, sample_batch_id
        )
        if triggered_at is not None:
            newest = _newest_computed(anchors)
            if newest is not None and newest >= triggered_at:
                return {"skipped": SKIPPED_CURRENT}
        pairs = form_pairs(anchors, notation_by_id)
        members = await _load_members(
            session, {anchor_id for pair in pairs for anchor_id in (pair.a, pair.b)}
        )
        records, summary = await asyncio.to_thread(
            grade_batch,
            anchors,
            notation_by_id,
            members,
            samples,
            batch_type,
            computed_utc=read_at.isoformat(),
            pairs=pairs,
        )
        # The write waits for the folds of the batch: a fold rewrites the
        # anchors' consensus row by row, and two writers over one batch's rows
        # in different orders is a deadlock waiting to happen.
        from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
            _acquire_batch_fold_lock,
        )

        await _acquire_batch_fold_lock(session, sample_batch_id)
        written = await _write(session, anchors, records)
        if written:
            await touch_ledger(session, sample_batch_id)
        await session.commit()
    summary["written"] = written
    summary["seconds"] = round(time.perf_counter() - started, 3)
    return summary


def continuity_outcome(summary: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    if summary.get("skipped"):
        return {
            "status": "partial",
            "message": (
                "Ratio stability was not graded: another pass is grading this "
                "batch now."
            ),
            "data": summary,
            "_notification_data": notification_data,
        }
    if not summary["pairs"]:
        return {
            "status": "partial",
            "message": (
                "No ratio stability was graded: no batch peak of this batch has "
                "an isotope line or another channel to pair with at an assigned "
                "or candidate tier."
            ),
            "data": summary,
            "_notification_data": notification_data,
        }
    if not summary["time_ordered"]:
        return {
            "status": "partial",
            "message": (
                "No ratio stability was graded: this batch is not time-ordered "
                + (
                    "(too few distinct acquisition times)."
                    if summary["reason"] == REASON_FEW_TIMES
                    else "(a gap between its acquisitions is more than "
                    f"{MAX_GAP_FACTOR} times the median one)."
                )
            ),
            "data": summary,
            "_notification_data": notification_data,
        }
    grades = summary["grades"]
    skipped = (
        f" {summary['pairs_skipped']} more were left ungraded (the pass's limit)."
        if summary["pairs_skipped"]
        else ""
    )
    return {
        "status": "success",
        "message": (
            f"Graded the ratio stability of {summary['pairs_graded']} pair"
            f"{'s' if summary['pairs_graded'] != 1 else ''} of batch peaks: "
            f"{grades[GRADE_STABLE]} stable, {grades[GRADE_DRIFT]} drift, "
            f"{grades[GRADE_RED]} red, {grades[GRADE_INCONCLUSIVE]} inconclusive "
            f"and {grades[GRADE_INSUFFICIENT]} on too few samples.{skipped} "
            "No tier moved."
        ),
        "data": summary,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def recompute_batch_continuity(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Grade the ratio stability of the batch's pairs, asked for outright.
    Writes no member, no consensus and no run; emits
    ``peak_assignment_reload`` so the ledgers refresh."""
    summary = await run_batch_continuity(sample_batch_id)
    return continuity_outcome(summary, sample_batch_id)


__all__ = [
    "Pair",
    "RatioReading",
    "RatioSeries",
    "SampleTime",
    "acquisition_order",
    "continuity_outcome",
    "form_pairs",
    "grade_batch",
    "member_reads_consensus",
    "pair_co_variation",
    "pair_entry",
    "ratio_series",
    "read_ratio",
    "recompute_batch_continuity",
    "rollup",
    "root_parent_id",
    "run_batch_continuity",
    "spearman_rho",
    "theil_sen_slope",
    "time_ordering",
]
