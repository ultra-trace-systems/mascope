"""Spectral-neighbourhood corroboration (assignment-confidence Phase 3, P3).

The fit score and arbitration judge each peak (and its isotope envelope) on its own. But
a real compound rarely appears as a single ion: it shows up through several **adducts**
(e.g. ``[M+H]+``, ``[M+NH4]+``, ``[M+Na]+``; ``[M-H]-``, ``[M+Br]-`` …) that co-occur in the
same spectrum. Their **co-occurrence is independent, corroborating evidence** beyond any one
peak's fit — the basis of CAMERA ([Kuhl et al. 2012][camera]) and IPA
([Del Carratore et al. 2019][ipa]). This module is the first P3 increment: for each assigned
compound, count the distinct adduct channels that independently support it and turn that into
a bounded corroboration signal the confidence layer can fold in.

Design (consistent with the rest of the confidence layer):
- **Cross-peak, competitor-blind within a compound.** It reads only *which* adducts of a
  compound were assigned, not the fit score — so it stays a separate, independently
  inspectable layer (it does not re-import the measurement).
- **Bounded and saturating.** ``corroboration = 1 - 2**-(n_adducts - 1)``: 0 for a lone
  adduct, 0.5 for two, 0.75 for three … — extra adducts help with diminishing returns.
- **Conservative.** Only *accepted* assignments (per the caller's ``accept`` predicate, e.g.
  assigned/candidate tier) count; a lone adduct yields 0 (no boost, no penalty).

Two later layers of the same model (step 4.16 of the assignment plan) sit below it:

- **Intensity consistency across a neutral's channels** (:func:`channel_ratio_consistency`).
  The channels of one neutral hold a ratio the source's chemistry sets, so within one sample
  the ratio of a neutral's two channels is read against the same pair's ratio over the
  sample's other neutrals: its distance from their median, in their robust spread, graded
  into a band (:data:`RATIO_BANDS`). The batch's reading of the same ratio over time is the
  continuity pass's (step 4.11); it can take the per-sample log ratio from
  :func:`channel_log_ratio`.
- **In-source fragments** (:func:`in_source_fragment_pairs`): an ion that sheds a small
  neutral in the source (:data:`IN_SOURCE_LOSSES`) shows a second peak, read as the neutral
  less the loss through the same channel. A pair of committed neutrals one loss apart through
  one channel may be one compound and its fragment ([Xu et al. 2015][xu2015];
  [Domingo-Almenara et al. 2019][misa]).

Each enters a probability only once the anchor-swap benchmark
(``tooling/score_eval/corroboration_benchmark.py``) measures a likelihood ratio above one with
its interval clear of one; until then it is recorded, not weighed.

[camera]: https://pubs.acs.org/doi/10.1021/ac202450g
[ipa]: https://pubs.acs.org/doi/10.1021/acs.analchem.9b02354
[xu2015]: https://pubs.acs.org/doi/10.1021/ac504118y
[misa]: https://pubs.acs.org/doi/10.1021/acs.analchem.8b03126
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

import numpy as np


@dataclass(frozen=True)
class AdductCorroboration:
    """How independently a compound is supported by co-occurring adducts."""

    compound: str
    adducts: tuple[str, ...]  # the distinct adduct channels it was assigned via
    n_adducts: int
    corroboration: float  # [0, 1): 0 for a lone adduct, rising with more


def _corroboration_score(n_adducts: int) -> float:
    """Bounded, saturating boost from the number of co-occurring adducts:
    0 (1 adduct), 0.5 (2), 0.75 (3), 0.875 (4) …"""
    if n_adducts <= 1:
        return 0.0
    return 1.0 - 2.0 ** (-(n_adducts - 1))


def adduct_corroboration(
    assignments: Iterable[Any],
    *,
    compound_key: str = "target_compound_id",
    adduct_key: str = "ionization_mechanism_id",
    accept: Callable[[Any], bool] | None = None,
) -> dict[str, AdductCorroboration]:
    """Per-compound adduct co-occurrence corroboration over a run's assignments.

    :param assignments: assignment records (mappings or objects) carrying a compound id
        and an adduct/ionization id.
    :param compound_key: field naming the compound (records with a falsy value are skipped —
        e.g. untargeted/unassigned rows have no ``target_compound_id``).
    :param adduct_key: field naming the adduct / ionization channel.
    :param accept: optional predicate to keep only confident assignments (e.g. tier in
        {assigned, candidate}); default keeps all.
    :returns: ``{compound_id: AdductCorroboration}`` for every compound with >= 1 accepted
        assignment. ``corroboration`` is 0 for a compound seen via a single adduct.
    """

    def get(rec: Any, key: str):
        return rec.get(key) if isinstance(rec, dict) else getattr(rec, key, None)

    by_compound: dict[str, set[str]] = {}
    for rec in assignments:
        if accept is not None and not accept(rec):
            continue
        compound = get(rec, compound_key)
        if not compound:
            continue
        adduct = get(rec, adduct_key)
        if adduct is None:
            continue
        by_compound.setdefault(str(compound), set()).add(str(adduct))

    out: dict[str, AdductCorroboration] = {}
    for compound, adducts in by_compound.items():
        ordered = tuple(sorted(adducts))
        out[compound] = AdductCorroboration(
            compound=compound,
            adducts=ordered,
            n_adducts=len(ordered),
            corroboration=_corroboration_score(len(ordered)),
        )
    return out


# ---------------------------------------------------------------------------
# Intensity consistency across a neutral's channels (step 4.16).
# ---------------------------------------------------------------------------

#: How many other neutrals a sample has to show through a pair of channels
#: before it has a typical ratio to read one neutral's against. A median and a
#: MAD over fewer move by a whole band when one neutral comes or goes.
MIN_RATIO_REFERENCE = 5

#: The least robust spread a reference is read at, in natural log units. A
#: handful of neutrals can agree closely by chance, and a spread of a few
#: percent would call every other neutral's ratio outlying. 0.25 is the spread
#: at which step 4.11 calls a channel pair's ratio stable over a batch, the
#: tightest the same ratio is expected to hold.
RATIO_SPREAD_FLOOR = 0.25

#: The bands a ratio's distance from the sample's typical ratio falls in, in
#: reference spreads: within one, within two, beyond. Set before the
#: measurement rather than from it, so the benchmark's likelihood ratios are not
#: read off edges chosen on the same data.
RATIO_CONSISTENT = "consistent"
RATIO_LOOSE = "loose"
RATIO_OUTLYING = "outlying"
RATIO_BANDS: tuple[tuple[float, str], ...] = (
    (1.0, RATIO_CONSISTENT),
    (2.0, RATIO_LOOSE),
    (math.inf, RATIO_OUTLYING),
)


def channel_log_ratio(height_a: float | None, height_b: float | None) -> float | None:
    """The within-sample ratio of two peaks of one neutral, ``ln(I_b / I_a)``.

    Every effect that scales a whole spectrum scales both peaks, so the ratio
    cancels it. The natural log, as the continuity pass reads the same ratio
    over a batch (step 4.11), so a reading here is one point of its series.

    :param height_a: The first channel's peak height.
    :param height_b: The second channel's peak height.
    :return: The log ratio, or None where either height is missing, not
        finite or not positive.
    """
    try:
        a = float(height_a)  # type: ignore[arg-type]
        b = float(height_b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(b)) or a <= 0.0 or b <= 0.0:
        return None
    return math.log(b / a)


@dataclass(frozen=True)
class RatioReference:
    """A pair of channels' typical ratio in one sample, and how far it spreads."""

    centre: float  # median log ratio over the reference neutrals
    spread: float  # 1.4826 x MAD of it, at least the floor
    n: int  # how many neutrals it was read over


def ratio_reference(
    log_ratios: Iterable[float | None],
    *,
    min_count: int = MIN_RATIO_REFERENCE,
    spread_floor: float = RATIO_SPREAD_FLOOR,
) -> RatioReference | None:
    """The typical ratio of a pair of channels over a sample's neutrals.

    :param log_ratios: The neutrals' log ratios through the pair; None and
        non-finite values are left out.
    :param min_count: The fewest neutrals a reference is read over.
    :param spread_floor: The least spread it is read at.
    :return: The median and the robust spread (1.4826 x MAD, at least
        ``spread_floor``), or None under ``min_count`` neutrals.
    """
    values = np.asarray(
        [v for v in log_ratios if v is not None and math.isfinite(v)], dtype=float
    )
    if len(values) < max(min_count, 1):
        return None
    centre = float(np.median(values))
    mad = float(np.median(np.abs(values - centre)))
    return RatioReference(
        centre=centre, spread=max(1.4826 * mad, spread_floor), n=len(values)
    )


def ratio_band(z: float) -> str:
    """The band a distance from the typical ratio, in spreads, falls in."""
    for edge, band in RATIO_BANDS:
        if z <= edge:
            return band
    return RATIO_BANDS[-1][1]


@dataclass(frozen=True)
class RatioConsistency:
    """One neutral's ratio through a pair of channels, read against its sample's."""

    pair: tuple[str, str]  # the channels, the ratio being the second's over the first's
    log_ratio: float
    centre: float
    spread: float
    n_reference: int
    z: float  # |log_ratio - centre| / spread
    band: str  # one of RATIO_BANDS

    def as_record(self) -> dict:
        """The reading as a row records it, rounded for storage."""
        return {
            "pair": list(self.pair),
            "log_ratio": round(self.log_ratio, 4),
            "centre": round(self.centre, 4),
            "spread": round(self.spread, 4),
            "n_reference": self.n_reference,
            "z": round(self.z, 3),
            "band": self.band,
        }


def ratio_consistency(
    log_ratio: float, reference: RatioReference, *, pair: tuple[str, str] = ("", "")
) -> RatioConsistency:
    """How far a neutral's ratio sits from its sample's typical one, graded.

    :param log_ratio: The neutral's :func:`channel_log_ratio`.
    :param reference: The pair's :func:`ratio_reference` over the sample's
        other neutrals.
    :param pair: The two channels, for the record.
    :return: The distance in spreads and its band (:data:`RATIO_BANDS`).
    """
    z = abs(float(log_ratio) - reference.centre) / reference.spread
    return RatioConsistency(
        pair=pair,
        log_ratio=float(log_ratio),
        centre=reference.centre,
        spread=reference.spread,
        n_reference=reference.n,
        z=z,
        band=ratio_band(z),
    )


def channel_ratio_consistency(
    heights: Mapping[str, Mapping[str, float]],
    *,
    min_count: int = MIN_RATIO_REFERENCE,
    spread_floor: float = RATIO_SPREAD_FLOOR,
) -> dict[str, list[RatioConsistency]]:
    """Each neutral's channel ratios in one sample, read against its other neutrals'.

    The channels of one neutral hold a ratio the source's chemistry sets: the
    ammonium adduct against the protonated molecule, the bromide cluster against
    the deprotonated one. It differs from compound to compound, but within one
    sample it does so about a typical value per pair of channels, which the
    sample's other neutrals seen through both give. A neutral is read against
    those others alone, never itself, so it cannot vouch for its own ratio.

    :param heights: Per neutral, the peak height it was seen at through each
        channel (one peak per channel: the caller picks it).
    :param min_count: The fewest other neutrals a pair's reference is read over.
    :param spread_floor: The least spread a reference is read at.
    :return: Per neutral, one reading per pair of its channels that has a
        reference, the pairs in channel order. A neutral seen through one
        channel, or only through pairs too few others share, is absent.
    """
    by_pair: dict[tuple[str, str], dict[str, float]] = {}
    for neutral, channels in heights.items():
        names = sorted(channels)
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                ratio = channel_log_ratio(channels[first], channels[second])
                if ratio is not None:
                    by_pair.setdefault((first, second), {})[neutral] = ratio
    readings: dict[str, list[RatioConsistency]] = {}
    for pair in sorted(by_pair):
        ratios = by_pair[pair]
        neutrals = list(ratios)
        values = np.asarray([ratios[n] for n in neutrals], dtype=float)
        for index, neutral in enumerate(neutrals):
            reference = ratio_reference(
                np.delete(values, index).tolist(),
                min_count=min_count,
                spread_floor=spread_floor,
            )
            if reference is None:
                continue
            readings.setdefault(neutral, []).append(
                ratio_consistency(values[index], reference, pair=pair)
            )
    return readings


# ---------------------------------------------------------------------------
# In-source fragments (step 4.16).
# ---------------------------------------------------------------------------

#: Small neutrals an ion sheds in the source, by formula, with their element
#: counts. Water from any hydroxylated or acidic analyte; carbon monoxide and
#: carbon dioxide from carbonyls and carboxylates; formic acid and methanol from
#: acids and methoxy groups; nitric acid from an organonitrate; sulfur trioxide
#: from an organosulfate. A fragment of an ion through a channel is read as the
#: neutral less the loss through the same channel.
IN_SOURCE_LOSSES: dict[str, dict[str, int]] = {
    "H2O": {"H": 2, "O": 1},
    "CO": {"C": 1, "O": 1},
    "CO2": {"C": 1, "O": 2},
    "CH2O2": {"C": 1, "H": 2, "O": 2},
    "CH4O": {"C": 1, "H": 4, "O": 1},
    "HNO3": {"H": 1, "N": 1, "O": 3},
    "SO3": {"S": 1, "O": 3},
}


def subtract_loss(
    counts: Mapping[str, int], loss: Mapping[str, int]
) -> dict[str, int] | None:
    """A neutral's element counts less a loss's, or None where it holds too few.

    :param counts: The neutral's element counts.
    :param loss: The loss's element counts.
    :return: What is left, elements at zero dropped; None where an element
        would go negative or nothing would be left.
    """
    left = {element: int(n) for element, n in counts.items() if n}
    for element, n in loss.items():
        remaining = left.get(element, 0) - n
        if remaining < 0:
            return None
        if remaining:
            left[element] = remaining
        else:
            left.pop(element, None)
    return left or None


@dataclass(frozen=True)
class FragmentPair:
    """Two neutrals one in-source loss apart, seen through one channel."""

    parent: str
    fragment: str
    loss: str  # a key of IN_SOURCE_LOSSES


def in_source_fragment_pairs(
    neutrals: Mapping[str, Mapping[str, int]],
    *,
    losses: Mapping[str, Mapping[str, int]] | None = None,
) -> list[FragmentPair]:
    """The pairs among one channel's neutrals that are one loss apart.

    A neutral less a loss that is itself among them may be the first one's
    in-source fragment rather than a compound of its own: nothing measured in
    one spectrum tells the two apart, so the pair is a grouping to record, not
    evidence ([Xu et al. 2015][xu2015]).

    :param neutrals: One channel's neutrals, by id, with their element counts.
    :param losses: The losses to look for; :data:`IN_SOURCE_LOSSES` by default.
    :return: Every pair, ordered by parent, loss and fragment.
    """
    table = IN_SOURCE_LOSSES if losses is None else losses
    by_counts: dict[frozenset, list[str]] = {}
    for neutral, counts in neutrals.items():
        key = frozenset((element, int(n)) for element, n in counts.items() if n)
        by_counts.setdefault(key, []).append(neutral)
    pairs = []
    for parent in sorted(neutrals):
        for loss in sorted(table):
            left = subtract_loss(neutrals[parent], table[loss])
            if left is None:
                continue
            for fragment in sorted(by_counts.get(frozenset(left.items()), ())):
                pairs.append(FragmentPair(parent, fragment, loss))
    return pairs
