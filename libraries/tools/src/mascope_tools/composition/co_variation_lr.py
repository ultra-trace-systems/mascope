"""The co-variation null model: a likelihood ratio for 4.2's reading (step 4.21).

Step 4.2 of the assignment plan (``docs/dev/assignment_quality_plan.md``)
reads two channels of one neutral against each other across a batch: Pearson's
r of their residual traces (:mod:`mascope_tools.composition.coherence`), and
calls a pair co-varying at r >= 0.6. That line is a reading, not evidence. Two
traces chance puts together correlate too, the more often the fewer spectra
they share, and decision 35 holds the reading display-only until a null model
says how much more often a pair of one molecule's channels reads a given r
than a chance pair does. This module is that statement and its lookup.

The model
---------

For a stratum of shared spectra (:data:`STRATA`) and a bin of r
(:data:`R_EDGES`), the likelihood ratio is

    LR(r) = f_true(r) / f_null(r)

where ``f_null`` is the density of r over *decoy* pairs - a channel's trace
read against one of another neutral of the same partition, of a prevalence
near its real partner's, so only chance relates the two - and ``f_true`` the
density over pairs that are one species. The true density is not observed
cleanly, and the step reads it two ways:

- **The mixture** (:func:`mixture_lr`). The channel pairs themselves are a mix
  of true pairs and chance pairings a formula coincidence made:
  ``f_channel = (1 - pi) f_null + pi f_true``. ``1 - pi`` is read where the
  null dominates, below the null's own median r (:func:`null_share`, the
  estimate of Storey, J. R. Stat. Soc. B 2002, 64, 479, read on r rather than
  on a p-value), and then ``LR = (f_channel / f_null - (1 - pi)) / pi``. This
  is the estimate the table keeps. Where ``pi`` does not stand clear of
  nought (:func:`excess_shown`) the channel pairs show no excess over chance
  and the ratio is 1 throughout: dividing by a share near nought would only
  magnify each bin's noise.
- **The upper bound** (:func:`ratio_lr`): an M0 against its own isotope line,
  one species by construction. Two channels of one neutral can move apart
  with the source state where an isotope line cannot, so this ratio is the
  most the channel reading could reach.

A ratio from binned counts is noisy where a bin holds few pairs. The null's
histogram takes a pseudo-count per bin (:data:`PSEUDO_COUNT`), so that a bin
no decoy reached gives a large ratio rather than an infinite one; the
numerator's takes none, since the same pseudo-count on the smaller sample of
channel or isotope pairs would raise every bin they leave empty above the
null's, a ratio no pair gave. The ratio is then made non-decreasing in r by the pool-adjacent-violators algorithm
(:func:`isotonic_increasing`; Barlow et al., Statistical Inference under Order
Restrictions, 1972), each bin weighted by the pairs it holds: a higher r is
never weaker evidence than a lower one.

The table
---------

:class:`LikelihoodRatioTable` holds the ratio per stratum and bin with its
version and provenance, as :class:`~mascope_tools.composition.calibration.
Calibration` keeps a fitted curve: what it was measured on, how many pairs,
and whether it is provisional. :data:`TABLE` is the one measured on the
testbed's batch ledgers; its numbers are the measurement in
``docs/dev/assignment_quality_plan_log.md`` (step 4.21), made by
``tooling/assignment_bench/covariation_null.py``. :func:`likelihood_ratio`
looks a reading up in it. A bar (:attr:`LikelihoodRatioTable.bars`) is the
lowest r of a stratum at which the ratio reaches :data:`LR_BAR`; nothing in
the engine reads either yet (step 5.8d is the first reader).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from scipy.stats import beta, norm

from mascope_tools.composition.coherence import MIN_SPECTRA, CoVariation


#: The lower edge of each stratum of shared spectra: 10 to 19, 20 to 49, 50 to
#: 199, 200 or more. Under :data:`~mascope_tools.composition.coherence.
#: MIN_SPECTRA` a pair is untestable and has no stratum.
STRATA: tuple[int, ...] = (MIN_SPECTRA, 20, 50, 200)

#: The edges of the bins of r the ratio is read in: twenty bins of 0.1 from -1
#: to 1. A reading of exactly 1 falls in the last bin.
R_EDGES: tuple[float, ...] = tuple(round(-1.0 + 0.1 * i, 1) for i in range(21))

#: The pseudo-count each bin of the null's histogram takes before a density
#: is read (Jeffreys' prior on a multinomial's cells), so that a bin no decoy
#: reached gives a large ratio rather than an infinite one. The numerator's
#: histogram takes none.
PSEUDO_COUNT = 0.5

#: How many standard errors above nought the true share must stand before the
#: mixture gives a ratio: one-sided 95 %. Under it the channel pairs show no
#: excess over chance, and every ratio is 1.
EXCESS_Z = 1.645

#: The ratio a bar asks for: a reading ten times likelier under one species
#: than under chance, "strong" on Jeffreys' scale (Theory of Probability,
#: 1961, appendix B) and the planner's proposal of 2026-11-22.
LR_BAR = 10.0


def stratum_of(n_spectra: int, strata: Sequence[int] = STRATA) -> Optional[int]:
    """The index of the stratum a pair of ``n_spectra`` shared spectra falls
    in; None under the first edge."""
    if n_spectra < strata[0]:
        return None
    index = 0
    for i, edge in enumerate(strata):
        if n_spectra >= edge:
            index = i
    return index


def r_bin(r: float, edges: Sequence[float] = R_EDGES) -> int:
    """The index of the bin of r a reading falls in: ``edges[i] <= r <
    edges[i + 1]``, with r = 1 in the last bin and anything outside the edges
    clipped to the nearest bin."""
    index = int(np.searchsorted(np.asarray(edges), r, side="right")) - 1
    return min(max(index, 0), len(edges) - 2)


def _bins(values: Sequence[float], edges: Sequence[float]) -> np.ndarray:
    """:func:`r_bin` of every value at once."""
    index = np.searchsorted(np.asarray(edges), np.asarray(values, dtype=float), "right")
    return np.clip(index - 1, 0, len(edges) - 2)


def _counts(values: Sequence[float], edges: Sequence[float]) -> np.ndarray:
    """How many values fall in each bin of r."""
    return np.bincount(_bins(values, edges), minlength=len(edges) - 1).astype(float)


def histogram(
    values: Sequence[float],
    edges: Sequence[float] = R_EDGES,
    *,
    pseudo_count: float = PSEUDO_COUNT,
) -> np.ndarray:
    """The share of ``values`` in each bin, each bin's count raised by
    ``pseudo_count`` first: a probability mass that sums to one."""
    counts = _counts(values, edges) + pseudo_count
    total = counts.sum()
    return counts / total if total else counts


def isotonic_increasing(
    values: Sequence[float], weights: Optional[Sequence[float]] = None
) -> np.ndarray:
    """The weighted least-squares non-decreasing fit to ``values``: the
    pool-adjacent-violators algorithm. A run of bins that falls is pooled
    into its weighted mean until nothing falls."""
    values = np.asarray(values, dtype=float)
    weights = (
        np.ones_like(values) if weights is None else np.asarray(weights, dtype=float)
    )
    # Each block: its mean, its weight, how many bins it covers.
    means: list[float] = []
    sums: list[float] = []
    sizes: list[int] = []
    for value, weight in zip(values, weights):
        weight = max(float(weight), 1e-12)
        means.append(float(value))
        sums.append(weight)
        sizes.append(1)
        while len(means) > 1 and means[-2] > means[-1]:
            total = sums[-2] + sums[-1]
            merged = (means[-2] * sums[-2] + means[-1] * sums[-1]) / total
            size = sizes[-2] + sizes[-1]
            del means[-1], sums[-1], sizes[-1]
            means[-1], sums[-1], sizes[-1] = merged, total, size
    return np.repeat(means, sizes)


def _below(
    channel_r: Sequence[float], null_r: Sequence[float], split: Optional[float]
) -> Optional[tuple[float, int, float, int]]:
    """The channel and decoy pairs' shares under ``split`` (the null's median
    when None), with their counts; None where either is empty or no decoy is
    under it."""
    channel_r = np.asarray(channel_r, dtype=float)
    null_r = np.asarray(null_r, dtype=float)
    if not channel_r.size or not null_r.size:
        return None
    split = float(np.median(null_r)) if split is None else split
    below_null = float(np.mean(null_r < split))
    if below_null <= 0.0:
        return None
    below_channel = float(np.mean(channel_r < split))
    return below_channel, int(channel_r.size), below_null, int(null_r.size)


def null_share(
    channel_r: Sequence[float],
    null_r: Sequence[float],
    *,
    split: Optional[float] = None,
) -> float:
    """The share of channel pairs that are chance pairings, ``1 - pi``: the
    share of them under the null's median r over the null's own share there
    (one half), held in [0, 1].

    :param channel_r: The channel pairs' r.
    :param null_r: The decoy pairs' r.
    :param split: The r the shares are read below; the null's median when
        omitted.
    """
    below = _below(channel_r, null_r, split)
    if below is None:
        return 1.0
    below_channel, _, below_null, _ = below
    return min(1.0, max(0.0, below_channel / below_null))


def null_share_error(
    channel_r: Sequence[float],
    null_r: Sequence[float],
    *,
    split: Optional[float] = None,
) -> float:
    """The standard error of :func:`null_share`'s ratio of two binomial
    shares, by the delta method, on its upper side; infinite where it cannot
    be read.

    The channel share's term is not the binomial ``p (1 - p) / n``, which is
    nought when no channel pair lies under the split, so that two pairs over
    the median would stand infinitely clear of chance. It is the distance from
    the share to its exact one-sided upper bound at :data:`EXCESS_Z`
    (Clopper and Pearson, Biometrika 1934, 26, 404), over :data:`EXCESS_Z`:
    the error that bound implies, which is what :func:`excess_shown` reads.
    On many pairs the two agree; on a handful the exact one keeps the guard
    at its nominal 5 %.
    """
    below = _below(channel_r, null_r, split)
    if below is None:
        return float("inf")
    below_channel, n_channel, below_null, n_null = below
    share = below_channel / below_null
    k = round(below_channel * n_channel)
    if k >= n_channel:
        upper = 1.0
    else:
        upper = float(beta.ppf(norm.cdf(EXCESS_Z), k + 1, n_channel - k))
    error_channel = (upper - below_channel) / EXCESS_Z
    var_null = below_null * (1 - below_null) / n_null
    return float(
        np.sqrt(error_channel * error_channel + share * share * var_null) / below_null
    )


def excess_shown(
    channel_r: Sequence[float],
    null_r: Sequence[float],
    *,
    z: float = EXCESS_Z,
) -> bool:
    """Whether the channel pairs hold more than chance pairings: the true
    share ``pi`` at least ``z`` standard errors above nought."""
    pi = 1.0 - null_share(channel_r, null_r)
    return pi - z * null_share_error(channel_r, null_r) > 0.0


def _monotone(raw: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return np.maximum(isotonic_increasing(raw, weights), 0.0)


def ratio_lr(
    alt_r: Sequence[float],
    null_r: Sequence[float],
    edges: Sequence[float] = R_EDGES,
    *,
    pseudo_count: float = PSEUDO_COUNT,
) -> np.ndarray:
    """The ratio of two densities of r per bin, made non-decreasing: the
    alternative's (pairs that are one species) over the null's.

    :return: One ratio per bin.
    """
    alt = histogram(alt_r, edges, pseudo_count=0.0)
    null = histogram(null_r, edges, pseudo_count=pseudo_count)
    weights = _bin_weights([alt_r, null_r], edges)
    return _monotone(alt / null, weights)


def mixture_lr(
    channel_r: Sequence[float],
    null_r: Sequence[float],
    edges: Sequence[float] = R_EDGES,
    *,
    pseudo_count: float = PSEUDO_COUNT,
    z: float = EXCESS_Z,
) -> tuple[np.ndarray, float]:
    """The likelihood ratio per bin under the mixture estimate: the channel
    pairs read as ``(1 - pi)`` of the null and ``pi`` of a true component.

    :param channel_r: The channel pairs' r.
    :param null_r: The decoy pairs' r.
    :param z: How many standard errors above nought ``pi`` must stand
        (:func:`excess_shown`, :data:`EXCESS_Z`).
    :return: One ratio per bin, non-decreasing and never negative, and the
        true share ``pi``. Where the channel pairs show no excess over the
        null every ratio is 1: the reading tells nothing. Without that
        guard a share near nought would divide the noise of every bin by
        itself.
    """
    share = null_share(channel_r, null_r)
    pi = 1.0 - share
    bins = len(edges) - 1
    if pi <= 0.0 or not excess_shown(channel_r, null_r, z=z):
        return np.ones(bins), max(pi, 0.0)
    channel = histogram(channel_r, edges, pseudo_count=0.0)
    null = histogram(null_r, edges, pseudo_count=pseudo_count)
    raw = (channel / null - share) / pi
    weights = _bin_weights([channel_r, null_r], edges)
    return _monotone(raw, weights), pi


def _bin_weights(
    groups: Sequence[Sequence[float]], edges: Sequence[float]
) -> np.ndarray:
    """The pairs each bin holds over every group, one at least: the weight a
    bin's ratio has in the monotone fit."""
    weights = np.ones(len(edges) - 1)
    for group in groups:
        weights += _counts(group, edges)
    return weights


def bar_of(
    ratios: Sequence[float],
    edges: Sequence[float] = R_EDGES,
    *,
    lr_bar: float = LR_BAR,
) -> Optional[float]:
    """The lowest r at which a non-decreasing ratio reaches ``lr_bar``: the
    lower edge of the first bin that does; None where none does."""
    for index, ratio in enumerate(ratios):
        if ratio >= lr_bar:
            return float(edges[index])
    return None


@dataclass(frozen=True)
class LikelihoodRatioTable:
    """The co-variation reading's likelihood ratio per stratum of shared
    spectra and bin of r, with its provenance.

    :ivar version: The table's version; a record names it beside a ratio, and
        a new measurement is a new version.
    :ivar strata: The lower edge of each stratum (:data:`STRATA`).
    :ivar r_edges: The bins of r (:data:`R_EDGES`).
    :ivar ratios: Per stratum, one ratio per bin, non-decreasing in r.
    :ivar bars: Per stratum, the lowest r at which the ratio reaches
        :attr:`lr_bar`; None where it never does.
    :ivar lr_bar: The ratio a bar asks for (:data:`LR_BAR`).
    :ivar measured: What the table was measured on and when.
    :ivar pairs: Per stratum, the channel and decoy pairs it was read from.
    :ivar provisional: Whether the bar has not yet cleared on two reagent
        chemistries read separately (decision 35): while it is, nothing may
        read a ratio as evidence.
    """

    version: int
    strata: tuple[int, ...]
    r_edges: tuple[float, ...]
    ratios: tuple[tuple[float, ...], ...]
    bars: tuple[Optional[float], ...]
    lr_bar: float = LR_BAR
    measured: str = ""
    pairs: tuple[dict, ...] = field(default=())
    provisional: bool = True

    def __post_init__(self) -> None:
        if len(self.ratios) != len(self.strata) or len(self.bars) != len(self.strata):
            raise ValueError("a table holds one row of ratios and one bar per stratum")
        for row in self.ratios:
            if len(row) != len(self.r_edges) - 1:
                raise ValueError("a row holds one ratio per bin of r")
            if any(b < a for a, b in zip(row, row[1:])):
                raise ValueError("a row of ratios must not fall as r rises")

    def lookup(self, r: float, n_spectra: int) -> Optional[float]:
        """The ratio of a reading of ``r`` over ``n_spectra`` shared spectra;
        None under the first stratum's edge, where the pair is untestable."""
        stratum = stratum_of(n_spectra, self.strata)
        if stratum is None:
            return None
        return self.ratios[stratum][r_bin(r, self.r_edges)]

    def bar(self, n_spectra: int) -> Optional[float]:
        """The bar of the stratum ``n_spectra`` falls in; None under the first
        edge or where the stratum's ratio never reaches :attr:`lr_bar`."""
        stratum = stratum_of(n_spectra, self.strata)
        return None if stratum is None else self.bars[stratum]

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "strata": list(self.strata),
            "r_edges": list(self.r_edges),
            "ratios": [list(row) for row in self.ratios],
            "bars": list(self.bars),
            "lr_bar": self.lr_bar,
            "measured": self.measured,
            "pairs": [dict(p) for p in self.pairs],
            "provisional": self.provisional,
        }


#: The table measured on the testbed's batch ledgers (step 4.21): the mixture
#: estimate over every ledger, every chemistry pooled. Provisional: the bar
#: cleared on one reagent chemistry only, uronium over 50 to 199 shared
#: spectra, at r >= 0.9. The 15N-nitrate batches show no excess of their
#: channel pairs over chance that stands clear of its error under 200
#: spectra, and no other chemistry holds the 30 testable pairs a stratum
#: needs (charge transfer holds 44 over all four). Over 200 shared
#: spectra or more no chemistry reaches a ratio of 10: a chance pair reads
#: r >= 0.6 there in 15 to 21 % of cases. Over 10 to 19 the pooled channel
#: pairs show no excess and the row is 1; over 20 to 49 it is also held down
#: by one charge-transfer batch whose short traces rise and fall together. ``docs/dev/assignment_quality_plan_log.md`` has every
#: number.
TABLE = LikelihoodRatioTable(
    version=1,
    strata=STRATA,
    r_edges=R_EDGES,
    ratios=(
        (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        + (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.266, 0.266)
        + (0.266, 0.266, 3.299, 3.299, 3.299, 6.881, 6.881, 6.881, 6.881, 6.881),
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.241)
        + (0.241, 1.382, 1.905, 1.905, 1.905, 5.539, 5.539, 6.059, 6.059, 31.717),
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.188, 0.188, 0.269, 0.347)
        + (0.953, 0.953, 0.953, 1.171, 1.478, 1.614, 2.296, 3.077, 3.077, 6.849),
    ),
    bars=(None, None, 0.9, None),
    measured=(
        "the testbed's 20 batch ledgers (its copy of 2026-10-16), 2026-11-23, "
        "tooling/assignment_bench/covariation_null.py --seed 421 --decoys 20"
    ),
    pairs=(
        {"channel": 36, "decoy": 707, "isotope": 1},
        {"channel": 78, "decoy": 1449, "isotope": 11},
        {"channel": 261, "decoy": 4870, "isotope": 46},
        {"channel": 748, "decoy": 14976, "isotope": 326},
    ),
    provisional=True,
)


def likelihood_ratio(
    reading: CoVariation, table: Optional[LikelihoodRatioTable] = None
) -> Optional[float]:
    """The likelihood ratio of a co-variation reading in ``table``
    (:data:`TABLE` when omitted); None for an untestable reading."""
    if reading.r is None:
        return None
    return (table or TABLE).lookup(reading.r, reading.n_spectra)


__all__ = [
    "EXCESS_Z",
    "LR_BAR",
    "PSEUDO_COUNT",
    "R_EDGES",
    "STRATA",
    "TABLE",
    "LikelihoodRatioTable",
    "bar_of",
    "histogram",
    "isotonic_increasing",
    "likelihood_ratio",
    "excess_shown",
    "mixture_lr",
    "null_share",
    "null_share_error",
    "r_bin",
    "ratio_lr",
    "stratum_of",
]
