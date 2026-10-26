"""Which chemistry a spectrum was acquired under, read off its reagent ions.

Rung 5 of the binding ladder (``docs/dev/ingest_routing_and_splitting.md``,
section 5.4): a chemical-ionization source is by far the brightest thing in its
own spectrum, so a spectrum names its reagent - where it recorded the reagent's
ions at all. This module reads that, and nothing else. It binds nothing: what
it answers is stored beside the rung that bound the file, as an audit, until a
measurement says it may route.

**The fingerprint** is each shipped profile's own ions, the reagent and
calibrant families of :data:`~mascope_tools.composition.reagents.REAGENT_CLUSTERS`:
the reagent ion and its dimer (the library's anchors), the hydrates, the acid
adducts and the higher rungs. The air's ions are left out. Every negative
library carries the air's nitrate and carbonate, and the positive ones its
protonated water, so they say a discharge was there, not which reagent it ran.
A profile with no reagent - electrospray, protonation, an ambient-ion mode - has
no fingerprint and is never called. Its spectrum's correct answer is no call.

**Four guards** stand between a fingerprint and a call, each for a way the
naive probe of section 2.3 went wrong:

- *Coverage.* A fingerprint whose anchors all fall outside the scan ranges is
  unobservable, not absent: a method that starts its range above the reagent
  ions recorded nothing to read. Only observable fingerprints compete, and
  where none is observable the answer says so rather than guessing.
- *Dominance.* The score is the share of the spectrum's whole peak intensity
  the fingerprint claims, and its brightest ion must rank near the top. A
  reagent source puts most of its signal into its own ions; an ambient-ion
  spectrum spreads its signal over many, its natural nitrate among them. A
  spectrum of a handful of peaks has nothing for a reagent to stand out
  among, and is not read at all.
- *Label ratio.* A labelled reagent and its unlabelled form both show both
  forms of each anchor: the natural 15N line of nitrate sits exactly on the
  labelled reagent's mass, and a 15N source carries its 14N remainder. Which
  one ran is the ratio of the two forms' anchor intensities, never presence:
  the labelled form is called where it out-weighs the other several times
  over, the unlabelled one where the labelled line stays near natural
  abundance, and anything between is a mixture.
- *Margin.* The winner must lead every other observable fingerprint by a set
  factor. A spectrum two fingerprints explain alike is ambiguous.

The answer is one of four decisions - a profile, ``ambiguous``, ``unobservable``
or ``none`` - with the evidence behind it: per fingerprint the score, the
anchors in range and the anchors found, and the reasons in words.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence

import numpy as np

from mascope_tools.composition.profiles import (
    CHARGE_TRANSFER_INSTRUMENTS,
    REAGENT_PROFILES,
    ReagentProfile,
)
from mascope_tools.composition.reagents import (
    REAGENT_LINE_FAMILIES,
    ReagentCluster,
    match_reagent_clusters,
    reagent_library,
)


#: The version of the rules below. Stored with every answer, so an answer read
#: back says which rules made it, and a change to a threshold or a window is a
#: new version.
DETECTOR_VERSION = 1

#: A profile's fingerprint dominates its spectrum and names it.
DECISION_PROFILE = "profile"
#: Two observable fingerprints explain the spectrum too alike to choose, or a
#: labelled reagent's two forms are mixed.
DECISION_AMBIGUOUS = "ambiguous"
#: No fingerprint of the polarity has an anchor inside the scan ranges.
DECISION_UNOBSERVABLE = "unobservable"
#: Fingerprints were observable and none dominates: no reagent ran, none that
#: is shipped, or the spectrum is too thin to say.
DECISION_NONE = "none"

DECISIONS = (
    DECISION_PROFILE,
    DECISION_AMBIGUOUS,
    DECISION_UNOBSERVABLE,
    DECISION_NONE,
)

#: The profiles whose source is the fluoranthene beam of an Orbitrap's
#: internal-calibration source, read only on the instrument that has it.
_CHARGE_TRANSFER = frozenset({"EASYIC_POS", "EASYIC_NEG"})


@dataclass(frozen=True)
class DetectionRules:
    """The thresholds of the four guards.

    :param min_peaks: How many peaks a spectrum must hold to be read at all.
        An ambient-ion acquisition near its noise floor holds two or three,
        and any fingerprint that claims one of them explains it all.
    :param min_score: The share of the spectrum's peak intensity the winner
        must claim.
    :param top_rank: The rank, counted from 1 by intensity among all the
        spectrum's peaks, the winner's brightest ion must reach.
    :param margin: How many times the winner's score must exceed every other
        observable fingerprint's.
    :param labelled_ratio: How many times a labelled reagent's anchors must
        out-weigh their unlabelled forms for the labelled profile to be
        called.
    :param natural_ratio: The most a labelled form's anchors may weigh,
        against their unlabelled forms, for the unlabelled profile to be
        called. Natural abundance puts nitrate's 15N line at 0.4% of it; a
        labelled line far above that is a labelled reagent in the source.
    :param anchor_ppm: Window the anchors are found in, per instrument class,
        on the axis as acquired, which is all a file nothing has bound has:
        its calibration needs a chemistry. The corpus holds Orbitrap files
        up to 31 ppm out before calibration and TOF files up to 160 ppm out;
        a TOF's peaks are about 200 ppm wide, so its window is no wider than
        one peak.
    :param claim_ppm: Window every other fingerprint ion is claimed in once
        the anchors have said where the spectrum puts the reagent's masses,
        per instrument class.
    :param default_anchor_ppm: The anchor window of an unknown class.
    :param default_claim_ppm: The claim window of an unknown class.
    """

    min_peaks: int = 30
    min_score: float = 0.2
    top_rank: int = 5
    margin: float = 2.0
    labelled_ratio: float = 5.0
    natural_ratio: float = 0.1
    anchor_ppm: dict[str, float] = field(
        default_factory=lambda: {"orbi": 50.0, "tof": 200.0}
    )
    claim_ppm: dict[str, float] = field(
        default_factory=lambda: {"orbi": 5.0, "tof": 20.0}
    )
    default_anchor_ppm: float = 200.0
    default_claim_ppm: float = 20.0

    def windows(self, instrument_type: str | None) -> tuple[float, float]:
        """``(anchor_ppm, claim_ppm)`` for an instrument class."""
        key = (instrument_type or "").strip().lower()
        return (
            self.anchor_ppm.get(key, self.default_anchor_ppm),
            self.claim_ppm.get(key, self.default_claim_ppm),
        )

    def to_json(self) -> dict:
        """The rules, as stored beside an answer: every field, the windows too.

        An answer read back then says all of what decided it, with the file's
        instrument class to pick the windows by, and ``DetectionRules(**stored)``
        is the rules again.
        """
        return asdict(self)


DEFAULT_RULES = DetectionRules()


def fingerprint(profile_name: str) -> tuple[ReagentCluster, ...]:
    """A profile's own ions: its reagent's ladder and its calibrant beam.

    :param profile_name: The profile's name.
    :return: The ions, empty for a profile with no reagent.
    """
    return tuple(
        cluster
        for cluster in reagent_library(profile_name)
        if cluster.family in REAGENT_LINE_FAMILIES
    )


def candidate_profiles(
    polarity: str, instrument_type: str | None
) -> tuple[ReagentProfile, ...]:
    """The profiles a spectrum of this polarity and instrument may be called.

    Those of the polarity with a fingerprint. The charge-transfer profiles only
    on an instrument class that has the source, as
    :func:`~mascope_tools.composition.profiles.detect_reagent_profile` reads
    them: the fluoranthene beam is Orbitrap hardware.

    :param polarity: ``"+"`` or ``"-"``.
    :param instrument_type: ``"orbi"``, ``"tof"`` or None.
    :return: The profiles, in the registry's order.
    """
    charge_transfer = (
        instrument_type or ""
    ).strip().lower() in CHARGE_TRANSFER_INSTRUMENTS
    return tuple(
        profile
        for profile in REAGENT_PROFILES.values()
        if profile.polarity == polarity
        and fingerprint(profile.name)
        and (charge_transfer or profile.name not in _CHARGE_TRANSFER)
    )


def label_partner(profile: ReagentProfile) -> ReagentProfile | None:
    """The unlabelled profile a labelled one is the isotopic form of.

    Read off the reagents rather than listed: the partner is the profile of
    the same polarity whose reagent is this one's with the label taken off
    (``"^NO3"`` and ``"NO3"``).

    :param profile: A profile.
    :return: Its unlabelled partner, or None when it carries no label or has
        none.
    """
    if not profile.label_isotope or not profile.reagent_formula:
        return None
    plain = profile.reagent_formula.replace(
        profile.label_isotope, profile.label_isotope.lstrip("^")
    )
    for other in REAGENT_PROFILES.values():
        if (
            other is not profile
            and other.polarity == profile.polarity
            and other.reagent_formula == plain
            and not other.label_isotope
        ):
            return other
    return None


def _in_ranges(mz: float, scan_ranges: Sequence[tuple[float, float]]) -> bool:
    """Whether an m/z was recorded: inside one of the scan ranges.

    No ranges known reads as everything recorded, which is the generous
    reading: coverage then rests on the peaks alone.
    """
    if not scan_ranges:
        return True
    return any(low <= mz <= high for low, high in scan_ranges)


@dataclass(frozen=True)
class IonReading:
    """One ion of a fingerprint, found in the spectrum.

    :param label: The ion.
    :param mz: Where the spectrum has it.
    :param error_ppm: How far that sits from the ion's exact mass.
    :param rank: Its peak's rank by intensity among all the spectrum's peaks,
        from 1.
    :param intensity: Its peak's intensity.
    """

    label: str
    mz: float
    error_ppm: float
    rank: int
    intensity: float

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "mz": round(self.mz, 5),
            "error_ppm": round(self.error_ppm, 2),
            "rank": self.rank,
        }


@dataclass(frozen=True)
class FingerprintEvidence:
    """What one profile's fingerprint found in a spectrum.

    :param profile: The profile's name.
    :param observable: Whether any of its anchors is inside the scan ranges.
    :param anchors_in_range: The anchors that are.
    :param anchors_found: The anchors the spectrum shows, brightest first.
    :param brightest: Its brightest ion the spectrum shows, anchor or not.
    :param score: The share of the spectrum's peak intensity it claims, its
        ions' isotope lines included; 0 when it is unobservable.
    :param claimed: How many peaks it claims.
    """

    profile: str
    observable: bool
    anchors_in_range: tuple[str, ...] = ()
    anchors_found: tuple[IonReading, ...] = ()
    brightest: IonReading | None = None
    score: float = 0.0
    claimed: int = 0

    def anchor_intensity(self, labels: Iterable[str]) -> float:
        """The summed intensity of the named anchors it found."""
        wanted = set(labels)
        return sum(
            anchor.intensity for anchor in self.anchors_found if anchor.label in wanted
        )

    def to_json(self) -> dict:
        return {
            "profile": self.profile,
            "observable": self.observable,
            "score": round(self.score, 4),
            "claimed": self.claimed,
            "brightest": None if self.brightest is None else self.brightest.to_json(),
            "anchors_in_range": list(self.anchors_in_range),
            "anchors_found": [anchor.to_json() for anchor in self.anchors_found],
        }


@dataclass(frozen=True)
class ChemistryDetection:
    """The detector's answer for one spectrum.

    :param decision: One of :data:`DECISIONS`.
    :param profile: The profile called; set only for :data:`DECISION_PROFILE`.
    :param score: The leading fingerprint's score, where one was observable.
    :param reasons: Why, in words a person reads.
    :param evidence: Every candidate fingerprint's evidence.
    :param peaks: How many peaks the spectrum held.
    :param scan_ranges: The ranges coverage was judged against; empty when
        none were known.
    :param rules: The thresholds it was decided under.
    """

    decision: str
    profile: str | None = None
    score: float | None = None
    reasons: tuple[str, ...] = ()
    evidence: tuple[FingerprintEvidence, ...] = ()
    peaks: int = 0
    scan_ranges: tuple[tuple[float, float], ...] = ()
    rules: DetectionRules = DEFAULT_RULES

    @property
    def called(self) -> bool:
        """Whether a profile was called."""
        return self.decision == DECISION_PROFILE

    def to_json(self) -> dict:
        """The answer as stored: JSON-safe, rounded, the version included."""
        return {
            "detector": DETECTOR_VERSION,
            "decision": self.decision,
            "profile": self.profile,
            "score": None if self.score is None else round(self.score, 4),
            "reasons": list(self.reasons),
            "peaks": self.peaks,
            "scan_ranges": [
                [round(low, 4), round(high, 4)] for low, high in self.scan_ranges
            ],
            "rules": self.rules.to_json(),
            "candidates": [item.to_json() for item in self.evidence],
        }


def _percent(share: float) -> str:
    return f"{100.0 * share:.0f}%"


def _read_fingerprint(
    profile: ReagentProfile,
    mz: np.ndarray,
    intensity: np.ndarray,
    ranks: np.ndarray,
    total: float,
    scan_ranges: Sequence[tuple[float, float]],
    instrument_type: str | None,
    rules: DetectionRules,
) -> FingerprintEvidence:
    """One fingerprint against one spectrum."""
    ions = fingerprint(profile.name)
    in_range = tuple(
        cluster.label
        for cluster in ions
        if cluster.anchor and _in_ranges(cluster.mz, scan_ranges)
    )
    if not in_range:
        return FingerprintEvidence(profile.name, observable=False)
    anchor_ppm, claim_ppm = rules.windows(instrument_type)
    hits, _ = match_reagent_clusters(
        ions, mz, intensity, claim_ppm=claim_ppm, anchor_ppm=anchor_ppm
    )
    readings = sorted(
        (
            (
                hit.cluster.anchor,
                IonReading(
                    label=hit.cluster.label,
                    mz=hit.mz,
                    error_ppm=hit.mz_error_ppm,
                    rank=int(ranks[hit.index]),
                    intensity=hit.intensity,
                ),
            )
            for hit in hits
            if not hit.is_isotopologue
        ),
        key=lambda pair: pair[1].rank,
    )
    claimed = sorted({hit.index for hit in hits})
    score = float(intensity[claimed].sum()) / total if claimed and total > 0 else 0.0
    return FingerprintEvidence(
        profile.name,
        observable=True,
        anchors_in_range=in_range,
        anchors_found=tuple(reading for anchor, reading in readings if anchor),
        brightest=readings[0][1] if readings else None,
        score=score,
        claimed=len(claimed),
    )


def _label_pairs(
    evidence: Sequence[FingerprintEvidence],
) -> list[tuple[FingerprintEvidence, FingerprintEvidence]]:
    """``(labelled, unlabelled)`` for each labelled pair both observable."""
    by_name = {item.profile: item for item in evidence if item.observable}
    pairs = []
    for name, item in by_name.items():
        partner = label_partner(REAGENT_PROFILES[name])
        if partner is not None and partner.name in by_name:
            pairs.append((item, by_name[partner.name]))
    return pairs


def _paired_anchor_labels(
    labelled: ReagentProfile, unlabelled: ReagentProfile
) -> list[tuple[str, str]]:
    """The anchors the two forms share, as ``(labelled label, unlabelled label)``.

    The labelled ladder is the unlabelled one with the label on every atom of
    the element, so its anchors pair up in order.
    """
    marked = [cluster for cluster in fingerprint(labelled.name) if cluster.anchor]
    plain = [cluster for cluster in fingerprint(unlabelled.name) if cluster.anchor]
    return [(a.label, b.label) for a, b in zip(marked, plain)]


def _label_ratio(
    labelled: FingerprintEvidence, unlabelled: FingerprintEvidence
) -> float | None:
    """The labelled form's anchor intensity over the unlabelled form's.

    Only anchor pairs whose two masses were both recorded are compared.

    :return: The ratio, ``inf`` where only the labelled form was found, and
        None where neither was.
    """
    pairs = [
        (marked, plain)
        for marked, plain in _paired_anchor_labels(
            REAGENT_PROFILES[labelled.profile], REAGENT_PROFILES[unlabelled.profile]
        )
        if marked in labelled.anchors_in_range and plain in unlabelled.anchors_in_range
    ]
    heavy = labelled.anchor_intensity(marked for marked, _ in pairs)
    light = unlabelled.anchor_intensity(plain for _, plain in pairs)
    if heavy <= 0.0 and light <= 0.0:
        return None
    return float("inf") if light <= 0.0 else heavy / light


def _shown(ratio: float) -> str:
    return "inf" if ratio == float("inf") else f"{ratio:.3g}"


def detect_chemistry(
    mz: Sequence[float] | np.ndarray,
    intensity: Sequence[float] | np.ndarray,
    *,
    polarity: str,
    instrument_type: str | None,
    scan_ranges: Sequence[tuple[float, float]] | None = None,
    rules: DetectionRules = DEFAULT_RULES,
) -> ChemistryDetection:
    """Which shipped chemistry a spectrum's reagent ions name, if any.

    :param mz: The spectrum's peak m/z values, on the axis as acquired.
    :param intensity: Their intensities, in the same order.
    :param polarity: The spectrum's polarity, ``"+"`` or ``"-"``.
    :param instrument_type: ``"orbi"`` or ``"tof"``: the windows, and whether
        the charge-transfer profiles compete.
    :param scan_ranges: The m/z ranges the spectrum's scans recorded; None or
        empty when they are not known, which takes every anchor as recorded.
    :param rules: The thresholds.
    :return: The decision and its evidence.
    """
    mz_array = np.asarray(mz, dtype=float)
    intensity_array = np.clip(
        np.nan_to_num(np.asarray(intensity, dtype=float), nan=0.0), 0.0, None
    )
    ranges = tuple((float(low), float(high)) for low, high in scan_ranges or ())
    candidates = candidate_profiles(polarity, instrument_type)

    def answer(decision: str, *reasons: str, **fields) -> ChemistryDetection:
        return ChemistryDetection(
            decision=decision,
            reasons=tuple(reason for reason in reasons if reason),
            peaks=int(mz_array.size),
            scan_ranges=ranges,
            rules=rules,
            **fields,
        )

    if not candidates:
        return answer(
            DECISION_NONE, f"no shipped fingerprint for polarity {polarity!r}"
        )
    if mz_array.size == 0 or not np.any(intensity_array > 0):
        return answer(DECISION_NONE, "the spectrum holds no peaks")

    order = np.argsort(-intensity_array, kind="stable")
    ranks = np.empty(order.size, dtype=int)
    ranks[order] = np.arange(1, order.size + 1)
    total = float(intensity_array.sum())
    evidence = tuple(
        _read_fingerprint(
            profile,
            mz_array,
            intensity_array,
            ranks,
            total,
            ranges,
            instrument_type,
            rules,
        )
        for profile in candidates
    )

    # --- Coverage --- #
    unobservable = [item.profile for item in evidence if not item.observable]
    observable = [item for item in evidence if item.observable]
    outside = (
        "no anchor of " + ", ".join(unobservable) + " is inside the scan ranges"
        if unobservable
        else ""
    )
    if not observable:
        return answer(DECISION_UNOBSERVABLE, outside, evidence=evidence)
    if mz_array.size < rules.min_peaks:
        return answer(
            DECISION_NONE,
            f"the spectrum holds {mz_array.size} peaks, under the "
            f"{rules.min_peaks} a reagent would stand out among",
            outside,
            evidence=evidence,
        )

    # --- Label ratio --- #
    # Whichever form loses is the winner's isotopic other half, and leaves the
    # field before the margin is read.
    label_reasons: list[str] = []
    out_of_field: set[str] = set()
    for labelled, unlabelled in _label_pairs(evidence):
        ratio = _label_ratio(labelled, unlabelled)
        if ratio is None:
            continue
        if ratio >= rules.labelled_ratio:
            out_of_field.add(unlabelled.profile)
            label_reasons.append(
                f"{labelled.profile} over {unlabelled.profile}: its anchors "
                f"weigh {_shown(ratio)}x theirs"
            )
        elif ratio <= rules.natural_ratio:
            out_of_field.add(labelled.profile)
            label_reasons.append(
                f"{unlabelled.profile} over {labelled.profile}: the labelled "
                f"anchors weigh {_shown(ratio)}x theirs, near natural abundance"
            )
        elif max(labelled.score, unlabelled.score) >= rules.min_score:
            return answer(
                DECISION_AMBIGUOUS,
                f"{labelled.profile} and {unlabelled.profile} are mixed: the "
                f"labelled anchors weigh {_shown(ratio)}x the unlabelled, "
                f"between the {rules.natural_ratio:g}x and "
                f"{rules.labelled_ratio:g}x that decide",
                outside,
                score=max(labelled.score, unlabelled.score),
                evidence=evidence,
            )

    # --- Dominance --- #
    field_ = sorted(
        (item for item in observable if item.profile not in out_of_field),
        key=lambda item: item.score,
        reverse=True,
    )
    leader = field_[0]
    if leader.score < rules.min_score:
        return answer(
            DECISION_NONE,
            f"the leading fingerprint, {leader.profile}, claims "
            f"{_percent(leader.score)} of the spectrum's peak intensity, under "
            f"the {_percent(rules.min_score)} a reagent's source puts in its "
            "own ions",
            outside,
            score=leader.score,
            evidence=evidence,
        )
    if not leader.anchors_found:
        return answer(
            DECISION_NONE,
            f"{leader.profile} claims {_percent(leader.score)} of the "
            "spectrum's peak intensity but none of its anchors",
            outside,
            score=leader.score,
            evidence=evidence,
        )
    brightest = leader.brightest
    if brightest is None or brightest.rank > rules.top_rank:
        return answer(
            DECISION_NONE,
            f"{leader.profile} claims {_percent(leader.score)} of the "
            f"spectrum's peak intensity but its brightest ion ranks "
            f"{brightest.rank if brightest else 'nowhere'}, below the top "
            f"{rules.top_rank}",
            outside,
            score=leader.score,
            evidence=evidence,
        )
    dominance = (
        f"{leader.profile} claims {_percent(leader.score)} of the spectrum's "
        f"peak intensity; {brightest.label} ranks {brightest.rank}"
    )

    # --- Margin --- #
    if len(field_) > 1:
        second = field_[1]
        if second.score > 0 and leader.score < rules.margin * second.score:
            return answer(
                DECISION_AMBIGUOUS,
                dominance,
                f"{second.profile} claims {_percent(second.score)}: "
                f"{leader.score / second.score:.2g}x, under the "
                f"{rules.margin:g}x a call needs",
                *label_reasons,
                outside,
                score=leader.score,
                evidence=evidence,
            )
    return answer(
        DECISION_PROFILE,
        dominance,
        *label_reasons,
        outside,
        profile=leader.profile,
        score=leader.score,
        evidence=evidence,
    )
