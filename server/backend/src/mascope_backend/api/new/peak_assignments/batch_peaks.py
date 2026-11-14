"""Batch-peak fold-in and consensus -- the batch level of peak-centric assignment.

This module holds the PURE logic (no DB, no I/O) behind the batch overview:

- **Fold-in** (:func:`fold_in_sample`): fold one sample's peaks into a frozen,
  append-only set of cross-sample m/z **anchors**. An arriving peak either joins
  the nearest existing anchor within its (frozen, resolution-adaptive) tolerance,
  or mints a new anchor. Existing anchors are never redrawn, so a ``batch_peak_id``
  is a stable cross-sample identity under incremental sample arrival -- the property
  the batch-overview chart needs to draw one durable trace per species.

- **Candidates** (:func:`candidate_index`, :func:`resolve_candidate`): the
  anchor's append-only registry of the assignment identities its members have
  carried - neutral formula, ion formula, ionization mechanism - which a member
  row names by index instead of repeating. What makes the batch ledger stand
  without the per-sample ledger it was folded from.

- **Consensus** (:func:`compute_consensus`): roll the members' *per-sample*
  ``PeakAssignment`` results up into the batch peak's formula/tier. The vote is
  **evidence-weighted** (a high-fit, high-signal member outweighs low-SNR flips),
  confidence is measured over the **assigned** members while prevalence (``n_present``)
  is kept separate, and genuine disagreement/ties are surfaced (``is_ambiguous`` +
  ``alternatives``) rather than hidden. Formula assignment is never done here on a
  synthetic spectrum -- only the members' real per-sample fits are aggregated.
  The same pass rolls up the two ledger aggregates that are properties of the
  members rather than of the formula: the brightest member
  (:func:`max_intensity`) and the isotopologue family link
  (:func:`resolve_isotopologue_of`), which an anchor has no way to carry itself.
  An anchor most of whose members a pre-pass claimed is read by the role they
  carry instead - the source's ion, the detector's ringing - with that ion, as
  the per-sample ledger reads such a peak. A homologous series the batch's
  series detection found for the anchor's formula travels with the consensus
  for as long as the consensus names that formula (:func:`series_record_of`),
  and so do an isotopologue stamping's records: the diagnostic isotopologues
  predicted for the anchor's reading, and on a stamped line the parent it is an
  isotopologue of (:func:`diagnostic_isotopologues_record_of`,
  :func:`stamped_isotopologue_of_record`). So do the records of a row the
  merged-isotopologue reading stripped to a parent's line and of the parent
  that took it (:func:`merged_isotopologue_of_record`,
  :func:`merged_isotopologues_record`), of a known species the batch
  decided once over its files (:func:`known_species_record_of`), and of a
  rung of a reagent ion's water ladder the batch's series passed
  (:func:`water_ladder_record_of`).

- **Continuity's gates**: the provisional constants of the ratio-stability
  pass (``continuity.py``), beside the consensus constants, and the test a
  reader marks a grade stale by (:func:`continuity_stale`).

The DB controller (fold-in on arrival, backfill, consensus persistence) calls into
these functions. Design: ``docs/dev/peak_assignment_batch_primary.md`` (section 12: the
anchors and the fold).
"""

from __future__ import annotations

import bisect
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_RANK,
    TIER_UNASSIGNED,
)


# --- tolerances ---------------------------------------------------------------

#: Extra half-window added to the resolution half-FWHM to absorb residual
#: per-sample calibration drift (ppm). Small on a well-calibrated Orbitrap;
#: matters for TOF / long runs.
DEFAULT_DRIFT_MARGIN_PPM = 2.0

# --- consensus ----------------------------------------------------------------

#: Minimum vote weight for any assigned member, so a real-but-weak fit still counts.
WEIGHT_FLOOR = 1e-3
#: Evidence-share gap below which the top-two consensus formulas are called a tie.
CONSENSUS_TIE_TOL = 0.10
#: Assigned-member agreement below which the batch peak is flagged ambiguous
#: (likely a co-eluting blend or a mass-degenerate pair).
AMBIGUOUS_SUPPORT = 0.5

#: The per-sample role a member carries when it is an isotopologue of
#: another peak in the same sample. Spelled again here rather than imported from
#: the assignment engine: this module is pure, and the engine pulls in pandas,
#: numpy and the database id helper. A unit test holds the two spellings in step.
ROLE_ISO_CHILD = "iso_child"
ROLE_M0 = "M0"
ROLE_UNASSIGNED = "unassigned"
ROLE_REAGENT = "reagent"
ROLE_ARTIFACT = "artifact"

#: The roles a pre-pass claims a peak for, in the order a tie between them is
#: settled: the source's own ion and the detector's ringing. A member carrying
#: one carries no formula, and an anchor whose members are such rows is read by
#: the role (:func:`compute_consensus`) rather than as a peak nothing explained.
SOURCE_ROLES = (ROLE_REAGENT, ROLE_ARTIFACT)


# --- continuity: the ratio-stability pass (step 4.11) ---------------------------
#
# The gates and thresholds of ``continuity.py``, the pass that reads the
# within-sample ratio of two related batch peaks across the batch. EVERY ONE IS
# PROVISIONAL: a first-principles starting point, not a measured one, until step
# 4.13 has run the pass on real time-ordered batches (the continuity design,
# section 1.4, in git history before 2026-10-22). The grade is display-only: no
# tier, reason or level reads it.

#: The two kinds of pair a ratio is read for: an M0 and one of its isotope
#: lines (the ratio is fixed by isotopic abundance), and two channels of one
#: neutral (the ratio is fixed by the source's chemistry).
PAIR_ISOTOPOLOGUE = "isotopologue"
PAIR_ADDUCT = "adduct"

#: Provisional. The fewest samples both members of a pair must count in for
#: any grade: under six a median absolute deviation is not a spread estimate.
MIN_PAIRED_SAMPLES = 6
#: Provisional. The fewest distinct acquisition times those samples must span:
#: the segments of one file share its timestamp, and six of them are one time
#: point, not six. Also what keeps a wash, which the metric knows nothing of,
#: from passing for a series.
MIN_DISTINCT_TIMES = 5
#: Provisional. The fewest samples the drift test runs on: a slope over seven
#: points is not a trend. A pair graded on fewer is never ``stable``.
MIN_DRIFT_SAMPLES = 8
#: Provisional. The drift's monotonicity leg: Spearman's |rho| between the log
#: ratio and the acquisition order.
RHO_DRIFT = 0.6
#: Provisional. The drift's effect-size leg: the end-to-end change of the log
#: ratio over the pair's time span, |Theil-Sen slope x span|. A 1.5x change in a
#: fixed isotope ratio is chemically loud; an adduct ratio legitimately moves
#: more (ln 2).
DRIFT_LOG_RED: dict[str, float] = {
    PAIR_ISOTOPOLOGUE: math.log(1.5),
    PAIR_ADDUCT: math.log(2.0),
}
#: Provisional. The spread, 1.4826 x MAD of the log ratio, at or under which a
#: pair whose drift was tested reads as ``stable``: isotope physics pins the
#: ratio tighter than source chemistry pins an adduct partition.
SPREAD_STABLE: dict[str, float] = {PAIR_ISOTOPOLOGUE: 0.15, PAIR_ADDUCT: 0.25}
#: Provisional. The spread at or above which the ratio jumps rather than
#: scatters: roughly a factor-of-1.65 typical swing.
SPREAD_RED = 0.50
#: Provisional. The pairs an anchor's detail lists, alarm first and then the
#: best supported. A display cap only: the anchor's grade reads every pair.
MAX_PAIRS_PER_ANCHOR = 4
#: Provisional. The pairs one pass grades. The rest are left ungraded and their
#: anchors marked ``partial``, so a truncated batch reads as truncated.
MAX_PAIRS_PER_BATCH = 20_000
#: Provisional. The time-ordering gate of an ANALYSIS batch: its largest gap
#: between distinct acquisition times may be at most this many times the median
#: gap. An ACQUISITION batch, made per day of continuous acquisition, is
#: time-ordered by construction.
MAX_GAP_FACTOR = 10

#: Whether a fold runs the pass after it commits. Off: the pass ships with its
#: manual endpoint and the rebuild's trigger, and step 4.13 measures its cost at
#: ceiling scale before the folds trigger it.
CONTINUITY_ON_FOLD = False

#: A pair's grade, and an anchor's, which is its most alarming pair's. ``red``:
#: the ratio drifts while nothing says the two traces co-vary, or it jumps.
#: ``drift``: it drifts and the batch's co-variation reading (step 4.2) says the
#: two traces co-vary - the source's chemistry moved and the assignment stands.
#: ``stable``: it holds. ``inconclusive``: tested, and none of these.
#: ``insufficient``: too few samples or times for any reading.
GRADE_RED = "red"
GRADE_DRIFT = "drift"
GRADE_STABLE = "stable"
GRADE_INCONCLUSIVE = "inconclusive"
GRADE_INSUFFICIENT = "insufficient"
#: Every grade, the most alarming first: an anchor takes the first its pairs
#: hold.
CONTINUITY_GRADES = (
    GRADE_RED,
    GRADE_DRIFT,
    GRADE_STABLE,
    GRADE_INCONCLUSIVE,
    GRADE_INSUFFICIENT,
)


# --- the partition ----------------------------------------------------------------

#: The instrument type whose batch peaks are in each intensity unit. A batch's
#: anchors are partitioned by ionization mode and by instrument type, and the
#: unit an anchor is stamped with when it is minted is what records the second:
#: an Orbitrap member's intensity is a height and a TOF member's an area, so
#: the two never share an anchor or a trace.
INSTRUMENT_TYPE_BY_INTENSITY_VARIABLE: dict[str, str] = {
    "sum_peak_heights": "orbi",
    "sum_peak_areas": "tof",
}


def instrument_type_of(intensity_variable: Optional[str]) -> Optional[str]:
    """The instrument type (``orbi`` or ``tof``) of the partition an anchor in
    ``intensity_variable`` belongs to; None for a unit no instrument names."""
    return INSTRUMENT_TYPE_BY_INTENSITY_VARIABLE.get(intensity_variable)


# --- the member row's codes -----------------------------------------------------

#: A member stores its tier as a ``smallint``: the tier's rank, so the code
#: compares the way the engine compares tiers (higher is more confident).
TIER_CODES: dict[str, int] = dict(TIER_RANK)
TIER_NAMES: dict[int, str] = {code: tier for tier, code in TIER_CODES.items()}

#: A member stores its per-sample role as a ``smallint`` too. Order is
#: arbitrary but fixed: a code, once written, means one role for good.
ROLE_CODES: dict[str, int] = {
    ROLE_UNASSIGNED: 0,
    ROLE_M0: 1,
    ROLE_ISO_CHILD: 2,
    ROLE_REAGENT: 3,
    ROLE_ARTIFACT: 4,
}
ROLE_NAMES: dict[int, str] = {code: role for role, code in ROLE_CODES.items()}


def tier_code(tier: Optional[str]) -> Optional[int]:
    """The stored code of a tier name; ``None`` for no tier or an unknown one."""
    return TIER_CODES.get(tier) if tier is not None else None


def tier_name(code: Optional[int]) -> Optional[str]:
    """The tier name a stored code means; ``None`` for no code or an unknown one."""
    return TIER_NAMES.get(code) if code is not None else None


def role_code(role: Optional[str]) -> Optional[int]:
    """The stored code of a role name; ``None`` for no role or an unknown one."""
    return ROLE_CODES.get(role) if role is not None else None


def role_name(code: Optional[int]) -> Optional[str]:
    """The role name a stored code means; ``None`` for no code or an unknown one."""
    return ROLE_NAMES.get(code) if code is not None else None


def mz_delta_ppm(mz: float, anchor_mz: float) -> float:
    """A member's offset from its anchor's frozen m/z, in ppm - what the member
    stores in place of its absolute m/z. Single precision suffices: an offset
    of tens of ppm carried to seven significant digits places the peak to well
    below the precision the peak file itself carries."""
    return (mz - anchor_mz) / anchor_mz * 1e6


def mz_from_delta(anchor_mz: float, delta_ppm: Optional[float]) -> float:
    """The absolute m/z a member's stored offset recovers to; the anchor's own
    m/z when the member carries none."""
    return anchor_mz * (1.0 + (delta_ppm or 0.0) / 1e6)


def resolution_adaptive_tol_ppm(
    mz: float,
    resolution: Optional[float],
    drift_margin_ppm: float = DEFAULT_DRIFT_MARGIN_PPM,
) -> float:
    """Membership half-window (ppm) for an anchor created at ``mz``.

    Half the peak FWHM at this m/z (``FWHM_ppm = 1e6 / R``) plus a calibration-drift
    margin. Falls back to the margin alone when no resolution is available.
    """
    half_fwhm_ppm = (0.5 * 1e6 / resolution) if resolution and resolution > 0 else 0.0
    return half_fwhm_ppm + drift_margin_ppm


# --- anchors ------------------------------------------------------------------


@dataclass
class Anchor:
    """A frozen cross-sample m/z anchor (one batch peak)."""

    batch_peak_id: str
    mz: float
    tol_ppm: float


def _peak_mz(peak: Any) -> float:
    if isinstance(peak, dict):
        return float(peak["mz"])
    return float(peak.mz)


class AnchorSet:
    """A sorted, append-only set of frozen anchors for one (batch, ionization mode).

    ``find`` snaps an m/z to the nearest anchor whose *own* frozen tolerance
    contains it; ``add`` inserts a new anchor keeping the set sorted. Existing
    anchors are never moved or removed here -- the append-only invariant.
    """

    def __init__(self, anchors: Iterable[Anchor] = ()) -> None:
        self._anchors: list[Anchor] = sorted(anchors, key=lambda a: a.mz)
        self._mzs: list[float] = [a.mz for a in self._anchors]

    def __len__(self) -> int:
        return len(self._anchors)

    def anchors(self) -> list[Anchor]:
        return list(self._anchors)

    def get(self, index: int) -> Anchor:
        return self._anchors[index]

    def find(self, mz: float) -> Optional[int]:
        """Index of the nearest anchor whose frozen tolerance contains ``mz``,
        or ``None`` if no anchor is within tolerance."""
        mzs = self._mzs
        if not mzs:
            return None
        j = bisect.bisect_left(mzs, mz)
        best: Optional[int] = None
        best_d: Optional[float] = None
        for k in (j - 1, j):
            if 0 <= k < len(self._anchors):
                a = self._anchors[k]
                d = abs(a.mz - mz) / a.mz * 1e6
                if d <= a.tol_ppm and (best_d is None or d < best_d):
                    best, best_d = k, d
        return best

    def add(self, anchor: Anchor) -> Anchor:
        i = bisect.bisect_left(self._mzs, anchor.mz)
        self._mzs.insert(i, anchor.mz)
        self._anchors.insert(i, anchor)
        return anchor


@dataclass
class FoldedPeak:
    """One peak folded into a batch peak during :func:`fold_in_sample`."""

    batch_peak_id: str
    peak: Any
    is_new_anchor: bool
    mz_error_ppm: float


def fold_in_sample(
    anchor_set: AnchorSet,
    peaks: Iterable[Any],
    *,
    new_id: Callable[[], str],
    tol_fn: Callable[[float], float],
) -> list[FoldedPeak]:
    """Fold one sample's peaks into ``anchor_set`` (append-only).

    Each peak joins the nearest in-tolerance existing anchor or mints a new one
    (``anchor_set`` is extended in place). Peaks are processed in ascending m/z so
    an anchor minted earlier in the sample is visible to later peaks. One member
    per anchor per sample: on a contested anchor the nearest peak wins and the
    other is dropped (two peaks within half-FWHM in one sample are effectively the
    same m/z -- a split/satellite).

    :param new_id: zero-arg factory for a fresh ``batch_peak_id``.
    :param tol_fn: ``mz -> tol_ppm`` for a NEW anchor created at that m/z.
    :returns: one :class:`FoldedPeak` per anchor that received a member this sample.
    """
    ordered = sorted(peaks, key=_peak_mz)
    claimed: dict[str, FoldedPeak] = {}
    for p in ordered:
        mz = _peak_mz(p)
        idx = anchor_set.find(mz)
        if idx is None:
            anchor = anchor_set.add(Anchor(new_id(), mz, tol_fn(mz)))
            claimed[anchor.batch_peak_id] = FoldedPeak(
                anchor.batch_peak_id, p, True, 0.0
            )
            continue
        anchor = anchor_set.get(idx)
        d = abs(anchor.mz - mz) / anchor.mz * 1e6
        prev = claimed.get(anchor.batch_peak_id)
        if prev is None or d < prev.mz_error_ppm:
            is_new = prev.is_new_anchor if prev is not None else False
            claimed[anchor.batch_peak_id] = FoldedPeak(
                anchor.batch_peak_id, p, is_new, d
            )
    return list(claimed.values())


# --- candidates ---------------------------------------------------------------

#: Keys of one entry in ``BatchPeak.candidates``: the identity of an assignment
#: a member can point at, and nothing that varies per member (fit, tier and
#: intensity stay on the member row, where the vote reads them). An entry may
#: also say where the identity came from (``source``) and what a reference
#: list calls its formula (``listing``, ``listing.reference_listing``); neither
#: is part of the match.
CANDIDATE_KEYS = ("formula", "ion_formula", "ionization_mechanism_id")


def candidate_index(
    candidates: list[dict],
    formula: str,
    ion_formula: Optional[str],
    ionization_mechanism_id: Optional[str],
    source: Optional[str] = None,
    listing: Optional[dict] = None,
    target_compound_id: Optional[str] = None,
) -> int:
    """Index of the candidate (``formula``, ``ion_formula``, mechanism) in
    ``candidates``, appending it when absent.

    The list is the anchor's registry of every assignment identity its members
    have carried, and member rows reference it by position - so it is
    **append-only**: an entry is never removed or reordered while a member may
    point at it, and a re-fold that no longer needs an entry leaves it in
    place. ``candidates`` is extended in place; the caller persists it.

    :param candidates: The anchor's registry, in stored order.
    :param formula: The member's neutral formula.
    :param ion_formula: The member's ion formula, or None.
    :param ionization_mechanism_id: The member's mechanism, or None.
    :param source: Where the identity came from (``database``, ``untargeted``,
        ``manual``), recorded on a NEW entry only - the first member to bring
        an identity names its source, and a later member with the same
        identity from elsewhere does not rewrite it. Not part of the match.
    :param listing: What a reference list calls the formula, where the member
        matched it from one (``listing.reference_listing``). Recorded on a new
        entry, and on an existing entry that has none - a member of the search
        may have brought the identity first, and a later member matching it
        from a list says the list holds it. An entry that has one keeps it, as
        it keeps its source. Such an entry is replaced by a copy rather than
        written into, so a registry copied out of a loaded row leaves the
        row's own value alone and compares unequal to it. Not part of the
        match.
    :param target_compound_id: The target compound the member was matched to,
        where it came from the target library. Recorded as ``listing`` is: on a
        new entry, and on an existing entry that has none, so the batch ledger
        can say what the consensus's target declares
        (``listing.consensus_target_id``). Not part of the match.
    :return: The position the member's row should name.
    """
    for index, entry in enumerate(candidates):
        if (
            entry.get("formula") == formula
            and entry.get("ion_formula") == ion_formula
            and entry.get("ionization_mechanism_id") == ionization_mechanism_id
        ):
            added = {}
            if listing is not None and not entry.get("listing"):
                added["listing"] = listing
            if target_compound_id is not None and not entry.get("target_compound_id"):
                added["target_compound_id"] = target_compound_id
            if added:
                candidates[index] = {**entry, **added}
            return index
    entry = {
        "formula": formula,
        "ion_formula": ion_formula,
        "ionization_mechanism_id": ionization_mechanism_id,
    }
    if source is not None:
        entry["source"] = source
    if listing is not None:
        entry["listing"] = listing
    if target_compound_id is not None:
        entry["target_compound_id"] = target_compound_id
    candidates.append(entry)
    return len(candidates) - 1


def resolve_candidate(candidates: Optional[list], index: Optional[int]) -> dict:
    """The registry entry a member's index names, or ``{}`` when it names none:
    an unassigned member, an anchor with no registry, or an index the list does
    not reach.

    :param candidates: The anchor's registry, as stored (may be None).
    :param index: The member's ``candidate`` column (may be None).
    :return: The entry, or an empty dict.
    """
    if index is None or not candidates or index < 0 or index >= len(candidates):
        return {}
    entry = candidates[index]
    return entry if isinstance(entry, dict) else {}


# --- consensus ----------------------------------------------------------------


@dataclass
class Consensus:
    """Rolled-up formula/tier for a batch peak (maps onto ``BatchPeak`` columns)."""

    consensus_formula: Optional[str] = None
    consensus_ion_formula: Optional[str] = None
    ionization_mechanism_id: Optional[str] = None
    consensus_tier: str = TIER_UNASSIGNED
    #: The role accounting for an anchor most of whose members a pre-pass
    #: claimed (``reagent``, ``artifact``), or None; the tier stays
    #: ``unassigned`` beside it.
    consensus_role: Optional[str] = None
    best_fit_score: Optional[float] = None
    support_fraction: Optional[float] = None
    n_present: int = 0
    is_ambiguous: bool = False
    #: Brightest member, in the batch peak's own intensity unit.
    max_intensity: Optional[float] = None
    #: The batch peak this one is an isotopologue of, or None.
    isotopologue_of: Optional[str] = None
    alternatives: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


def _member(m: Any, key: str, default=None):
    if isinstance(m, dict):
        return m.get(key, default)
    return getattr(m, key, default)


def _vote_weight(fit_score: Optional[float], intensity: Optional[float]) -> float:
    """Evidence weight of one assigned member: its fit (competitor-blind quality),
    scaled by log signal so a bright, well-fit member outweighs weak flips."""
    w = max(fit_score if fit_score is not None else 0.0, WEIGHT_FLOOR)
    if intensity and intensity > 0:
        w *= 1.0 + math.log1p(intensity)
    return w


def max_intensity(members: Iterable[Any]) -> Optional[float]:
    """The brightest member's intensity, or ``None`` when none carries one.

    Taken over EVERY member rather than the assigned ones: how bright a species
    gets is a property of the trace, not of what was assigned to it, and an
    unassigned batch peak has an intensity worth sorting the ledger by. A member
    with no intensity is skipped rather than read as zero, so a batch peak that
    never carried one stays ``None`` and the ledger can say so.
    """
    values = [
        _member(m, "intensity") for m in members if _member(m, "intensity") is not None
    ]
    return max(values) if values else None


def _claimed(members: list) -> list:
    """The members a pre-pass claimed: a source role, and no formula."""
    return [
        m
        for m in members
        if _member(m, "role") in SOURCE_ROLES and not _member(m, "assigned_formula")
    ]


def _reading_population(members: list) -> list:
    """The members an anchor's reading is decided over: those a pre-pass
    claimed where they outnumber the members that carry a formula, else the
    members that carry a formula.

    A member is either assigned a formula, claimed for the source or the
    instrument, or neither, and the two readings compete for the anchor on how
    many members each holds. The claim is the pre-pass's and a formula the
    stages', so they never meet in one sample's row; they meet across samples -
    a dim file where the claim missed and the stages read the reagent's ion as
    a neutral through the reagent's own adduct, or a batch folded from runs of
    two engine versions - and the side most of the batch took is the anchor's.
    A tie stays with the formula, which is how the anchor read before a claim
    could be counted.
    """
    assigned = [m for m in members if _member(m, "assigned_formula")]
    claimed = _claimed(members)
    return claimed if len(claimed) > len(assigned) else assigned


def resolve_isotopologue_of(
    members: Iterable[Any], batch_peak_id: Optional[str] = None
) -> Optional[str]:
    """The batch peak this one is an isotopologue of, or ``None``.

    Batch peaks are bare m/z anchors and carry no family link of their own, so
    the link is derived from the members' per-sample assignments: a member that
    is a line of another row's isotope pattern names the row that owns it, and
    the anchor that owning row folded into in the same sample is the owner
    anchor. The caller resolves that hop and hands each member an
    ``owner_batch_peak_id``. Two kinds of row are lines, as in the sample
    ledger: an analyte's isotopologue (``iso_child``), and a source ion's
    isotope line, which keeps the ``reagent`` role and names its ion's row.

    The members span samples and need not agree - one sample's isotopologue is
    another's M0, and an ownerless isotopologue names no anchor at all - so this is
    a vote. It is counted over the population :func:`compute_consensus` reads the
    anchor from (:func:`_reading_population`): the **assigned** members, or the
    members a pre-pass claimed where they outnumber them. Prevalence is
    again kept out of it: an isotopologue detected in six of ten samples and
    assigned in none of the other four is still an isotopologue. A strict
    majority is required, which is also what makes the winner unique - two owners
    cannot each hold more than half of one set of votes - so no tie-break is
    needed.

    One hop only. An isotopologue whose owner anchor is itself an isotopologue is left
    as it was observed; flattening the chain needs the whole ledger and belongs
    to the reader that has it.

    :param members: The batch peak's occurrences, each optionally carrying
        ``role``, ``owner_batch_peak_id`` and ``assigned_formula``.
    :param batch_peak_id: This batch peak's own id, so a member that somehow
        names it cannot make it its own parent.
    :return: The owner's ``batch_peak_id``, or None.
    """
    population = _reading_population(list(members))
    votes: dict[str, int] = defaultdict(int)
    for m in population:
        if _member(m, "role") not in (ROLE_ISO_CHILD, ROLE_REAGENT):
            continue
        owner = _member(m, "owner_batch_peak_id")
        if not owner or owner == batch_peak_id:
            continue
        votes[owner] += 1
    if not votes:
        return None
    owner, n_votes = max(votes.items(), key=lambda kv: kv[1])
    return owner if 2 * n_votes > len(population) else None


def manual_pin_of(anchor: Any) -> Optional[dict]:
    """The manual curation an anchor carries under ``provenance.manual``, or None.

    A pin names a formula; anything else under that key is not one.
    """
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    pin = provenance.get("manual")
    if isinstance(pin, dict) and pin.get("formula"):
        return pin
    return None


#: The anchor's provenance key the batch's series detection
#: (``batch_series.py``) writes its record under: the formula a homologous
#: series proposed for the anchor, the unit, the assigned anchor its chain
#: starts at, and ``has_anchor``. The same key a per-sample row carries it
#: under (``tiering.SERIES``).
SERIES_KEY = "series"


def series_record_of(anchor: Any) -> Optional[dict]:
    """The series record an anchor carries under ``provenance.series``, or None.

    A record names a formula; anything else under that key is not one.
    """
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(SERIES_KEY)
    if isinstance(record, dict) and record.get("formula"):
        return record
    return None


def standing_series_record(anchor: Any) -> Optional[dict]:
    """The series record an anchor carries, where it names the anchor's
    consensus formula; None otherwise.

    The consensus keeps a record only while it names the record's formula
    (:func:`compute_consensus`). While a detection measures its proposals,
    though, an anchor it has not measured yet carries this run's record beside
    the reading it held before. What a reader serves - the ledger, a run's
    snapshot, a sample's run - is this, so a series never vouches for a
    formula it did not propose. The consensus itself is computed from the raw
    record (:func:`series_record_of`), which it keeps or takes off.
    """
    record = series_record_of(anchor)
    if record is None or record["formula"] != _member(anchor, "consensus_formula"):
        return None
    return record


#: The anchor's provenance key the batch's co-variation reading
#: (``batch_coherence.py``) writes its record under: how the trace of the
#: anchor's reading follows the traces of the same neutral through the
#: mode's other channels, the partner that reads strongest first.
CO_VARIATION_KEY = "co_variation"


def co_variation_record_of(anchor: Any) -> Optional[dict]:
    """The co-variation record an anchor carries under
    ``provenance.co_variation``, or None.

    A record names a formula; anything else under that key is not one.
    """
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(CO_VARIATION_KEY)
    if isinstance(record, dict) and record.get("formula"):
        return record
    return None


def _reads(record: dict, formula: Any, mechanism_id: Any) -> bool:
    """Whether a record was read for this formula through this mechanism."""
    return (
        bool(formula)
        and record.get("formula") == formula
        and record.get("ionization_mechanism_id") == mechanism_id
    )


def standing_co_variation_record(anchor: Any) -> Optional[dict]:
    """The co-variation record an anchor carries, where it was read for the
    anchor's consensus formula and mechanism; None otherwise.

    The consensus keeps a record only while it names that reading
    (:func:`compute_consensus`); what a reader serves - the ledger, a run's
    snapshot, a sample's row - is this, so a record never stands beside a
    reading it was not read for.
    """
    record = co_variation_record_of(anchor)
    if record is None or not _reads(
        record,
        _member(anchor, "consensus_formula"),
        _member(anchor, "ionization_mechanism_id"),
    ):
        return None
    return record


def row_co_variation(row: dict, anchor: Any) -> Optional[dict]:
    """The co-variation record of the anchor a sample's row sits in, where the
    row reads what the record was read for; None otherwise.

    The record is the batch's reading of the anchor's formula through its
    mechanism (:func:`standing_co_variation_record`). A monoisotopic row that
    reads its peak so is that reading in one sample, and the inspector shows
    the batch's record beside it; a row that reads the peak another way, or
    an isotope line, is not what was read.

    :param row: The sample's row, with ``role``, ``assigned_formula`` and
        ``ionization_mechanism_id``.
    :param anchor: The anchor its peak sits in, or None.
    """
    if anchor is None or row.get("role") != ROLE_M0:
        return None
    record = standing_co_variation_record(anchor)
    if record is None or not _reads(
        record, row.get("assigned_formula"), row.get("ionization_mechanism_id")
    ):
        return None
    return record


def continuity_stale(anchor: Any) -> bool:
    """Whether an anchor's continuity grade is behind its ledger: the ratio
    pass (``continuity.py``) graded it at another prevalence, or for another
    formula or mechanism than its consensus names now.

    The pass runs after the folds and over the whole batch, so a sample folded
    since it ran, a curation or a re-fold that moved the consensus leaves the
    grade describing a ledger that is no longer there. It is served marked
    rather than dropped, as an anchor verdict about a claim the consensus has
    moved off is. A count against the paired samples would fire on every
    anchor: they are a subset of the present ones by construction.

    :param anchor: A ``BatchPeak`` row or a snapshot of one, with its
        ``continuity`` detail.
    :return: False where the anchor carries no detail.
    """
    detail = _member(anchor, "continuity")
    if not isinstance(detail, dict):
        return False
    return (
        detail.get("n_present_at_compute") != _member(anchor, "n_present")
        or detail.get("formula") != _member(anchor, "consensus_formula")
        or detail.get("ionization_mechanism_id")
        != _member(anchor, "ionization_mechanism_id")
    )


#: The anchor's provenance key the batch's isotopologue stamping
#: (``batch_isotopologues.py``) writes a parent's record under: the diagnostic
#: isotopologues predicted for its formula, which of them a batch peak stands
#: at, and which of those were stamped.
DIAGNOSTIC_ISOTOPOLOGUES_KEY = "diagnostic_isotopologues"

#: The key it writes a stamped line's record under: the parent the line is an
#: isotopologue of, which one, the track's reading, and the samples it was
#: stamped in with where each stamp came from (``stamps``).
STAMPED_ISOTOPOLOGUE_OF_KEY = "stamped_isotopologue_of"

#: Where a stamped member's stamp came from: predicted by the batch's
#: stamping where the sample's own run did not read the line as the parent's,
#: or found there by the run's own isotope match (the peak picker's line, read
#: as the parent's isotopologue), which the stamping confirms.
STAMP_PREDICTED = "predicted"
STAMP_PICKER = "picker"


def diagnostic_isotopologues_record_of(anchor: Any) -> Optional[dict]:
    """The diagnostic isotopologues record an anchor carries under
    ``provenance.diagnostic_isotopologues``, or None. A record names a formula;
    anything else under that key is not one."""
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(DIAGNOSTIC_ISOTOPOLOGUES_KEY)
    if isinstance(record, dict) and record.get("formula"):
        return record
    return None


def standing_diagnostic_isotopologues_record(anchor: Any) -> Optional[dict]:
    """The diagnostic isotopologues record an anchor carries, where it was
    predicted for the anchor's consensus formula and mechanism; None otherwise.

    The consensus keeps the record only while it names that reading
    (:func:`compute_consensus`), so a vote, a curation or a re-fold that moves
    it takes the record off, and with it the stamps on its lines
    (``batch_peaks_controller``).
    """
    record = diagnostic_isotopologues_record_of(anchor)
    if record is None or not diagnostic_isotopologues_record_reads(record, anchor):
        return None
    return record


def diagnostic_isotopologues_record_reads(record: dict, anchor: Any) -> bool:
    """Whether a diagnostic isotopologues record was predicted for the anchor's
    consensus formula and mechanism, the reading it stands beside."""
    return _reads(
        record,
        _member(anchor, "consensus_formula"),
        _member(anchor, "ionization_mechanism_id"),
    )


def stamped_isotopologue_of_record(anchor: Any) -> Optional[dict]:
    """The stamp record an anchor carries under
    ``provenance.stamped_isotopologue_of``, or None. A record names its parent;
    anything else under that key is not one.

    The record is about the line's height against its parent, not about what
    the line's own members read, so the line's consensus keeps it whatever it
    names; it comes off when the parent's reading moves.
    """
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(STAMPED_ISOTOPOLOGUE_OF_KEY)
    if isinstance(record, dict) and record.get("batch_peak_id"):
        return record
    return None


def stamped_isotopologue_of_summary(anchor: Any) -> Optional[dict]:
    """What the ledger serves of an anchor's stamp record: everything but the
    per-sample stamps, which the member rows carry (:func:`member_stamp`)."""
    record = stamped_isotopologue_of_record(anchor)
    if record is None:
        return None
    return {key: value for key, value in record.items() if key != "stamps"}


def member_stamp(anchor: Any, sample_item_id: Any) -> Optional[dict]:
    """The stamp an anchor's member in one sample carries, or None: the parent
    batch peak it is the isotopologue of, the isotope line, and the stamp's
    source (:data:`STAMP_PREDICTED` or :data:`STAMP_PICKER`)."""
    record = stamped_isotopologue_of_record(anchor)
    if record is None:
        return None
    stamps = record.get("stamps")
    source = stamps.get(sample_item_id) if isinstance(stamps, dict) else None
    if source is None:
        return None
    return {
        "batch_peak_id": record["batch_peak_id"],
        "label": record.get("label"),
        "stamp_source": source,
    }


def stamped_lines(record: Optional[dict]) -> set[str]:
    """The batch peaks a parent's diagnostic isotopologues record stamped."""
    if not isinstance(record, dict):
        return set()
    return {
        entry["batch_peak_id"]
        for entry in record.get("predicted") or []
        if isinstance(entry, dict)
        and entry.get("stamped")
        and entry.get("batch_peak_id")
    }


#: The anchor's provenance key the batch's merged-isotopologue reading
#: (``batch_merged_isotopologues.py``) writes a stripped row's record under: the
#: parent whose isotope line the row is, which line, the share it read over
#: the batch, the reading the row carried before, and per sample what each
#: rewritten member read (``displaced_members``).
MERGED_ISOTOPOLOGUE_OF_KEY = "merged_isotopologue_of"

#: The key it writes the parent's record under: the reading the lines were
#: read against, and each line the parent took from the ledger's rows.
MERGED_ISOTOPOLOGUES_KEY = "merged_isotopologues"


def merged_isotopologue_of_record(anchor: Any) -> Optional[dict]:
    """The stripped row's record an anchor carries under
    ``provenance.merged_isotopologue_of``, or None. A record names its parent;
    anything else under that key is not one."""
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(MERGED_ISOTOPOLOGUE_OF_KEY)
    if isinstance(record, dict) and record.get("batch_peak_id"):
        return record
    return None


def merged_isotopologue_of_reads(record: dict, isotopologue_of: Any) -> bool:
    """Whether a stripped row's record stands beside the anchor's family link:
    the members still read the row as that parent's line."""
    return bool(isotopologue_of) and record.get("batch_peak_id") == isotopologue_of


def standing_merged_isotopologue_of_record(anchor: Any) -> Optional[dict]:
    """The stripped row's record an anchor carries, where its members still
    read it as the parent's line; None otherwise.

    The strip rewrote the members, so the consensus names the parent by its
    members' own family link (:func:`resolve_isotopologue_of`). A vote, a
    curation or a re-fold that moves the members off it takes the record off
    (:func:`compute_consensus`).
    """
    record = merged_isotopologue_of_record(anchor)
    if record is None or not merged_isotopologue_of_reads(
        record, _member(anchor, "isotopologue_of")
    ):
        return None
    return record


def merged_isotopologue_of_summary(anchor: Any) -> Optional[dict]:
    """What the ledger serves of a stripped row's record: everything but what
    each member read before, which the strip's run snapshot keeps as the
    members' own state."""
    record = standing_merged_isotopologue_of_record(anchor)
    if record is None:
        return None
    return {key: value for key, value in record.items() if key != "displaced_members"}


def merged_isotopologues_record(anchor: Any) -> Optional[dict]:
    """The parent's record an anchor carries under
    ``provenance.merged_isotopologues``, or None. A record names a formula;
    anything else under that key is not one."""
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(MERGED_ISOTOPOLOGUES_KEY)
    if isinstance(record, dict) and record.get("formula"):
        return record
    return None


def standing_merged_isotopologues_record(anchor: Any) -> Optional[dict]:
    """The parent's record an anchor carries, where its lines were read
    against the anchor's consensus formula and mechanism; None otherwise."""
    record = merged_isotopologues_record(anchor)
    if record is None or not _reads(
        record,
        _member(anchor, "consensus_formula"),
        _member(anchor, "ionization_mechanism_id"),
    ):
        return None
    return record


def without_merged_lines(record: dict, released: set[str]) -> Optional[dict]:
    """The parent's record with the lines of the released rows taken off: a
    row whose own record came off is no longer this parent's line. None when
    no line is left, so the parent carries no record at all.

    :param record: The parent's record (:func:`merged_isotopologues_record`).
    :param released: The rows whose records came off.
    """
    lines = [
        entry
        for entry in record.get("lines") or []
        if not (isinstance(entry, dict) and entry.get("batch_peak_id") in released)
    ]
    if not lines:
        return None
    return {**record, "lines": lines, "n_lines": len(lines)}


#: The anchor's provenance key the batch's isotope checks (``batch_checks.py``)
#: write their facts under: the reading they were read for - its ion, formula
#: and mechanism - and each fact the batch states of it.
BATCH_CHECKS_KEY = "batch_checks"

#: The facts a batch checks record carries, each a typed reading of its own:
#: the halogen lock, the label twin and the neutral pair.
BATCH_CHECK_FACTS = ("halogen_lock", "label_twin", "neutral_pair")


def batch_checks_record_of(anchor: Any) -> Optional[dict]:
    """The batch checks record an anchor carries under
    ``provenance.batch_checks``, or None. A record names an ion; anything else
    under that key is not one."""
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(BATCH_CHECKS_KEY)
    if isinstance(record, dict) and record.get("ion_formula"):
        return record
    return None


def batch_checks_record_reads(
    record: dict, ion_formula: Any, formula: Any, mechanism_id: Any
) -> bool:
    """Whether a batch checks record was read for this ion, formula and
    mechanism. A reagent ion's reading names no formula, so the ion is what
    every record names."""
    return (
        bool(ion_formula)
        and record.get("ion_formula") == ion_formula
        and record.get("formula") == formula
        and record.get("ionization_mechanism_id") == mechanism_id
    )


def _record_run(record: Optional[dict]) -> Any:
    return record.get("batch_peak_run_id") if isinstance(record, dict) else None


def standing_batch_checks_record(anchor: Any) -> Optional[dict]:
    """The batch checks record an anchor carries, where it was read for the
    anchor's consensus reading, with only the facts whose sources still stand;
    None where none is left.

    The consensus keeps the record while it names that reading
    (:func:`compute_consensus`). A fact also reads a record of another batch
    run: a halogen lock the isotopologue stamping's (step 4.2c), a neutral pair
    the co-variation reading's (step 4.2). A later stamping or reading
    replaces those, and the fact read off the earlier one is not served
    beside it: a lock stands while the anchor's diagnostic isotopologues
    record is the one it read (``stamping_run_id``), a pair while its
    co-variation record is (``co_variation_run_id``). A label twin reads the
    members alone.
    """
    record = batch_checks_record_of(anchor)
    if record is None or not batch_checks_record_reads(
        record,
        _member(anchor, "consensus_ion_formula"),
        _member(anchor, "consensus_formula"),
        _member(anchor, "ionization_mechanism_id"),
    ):
        return None
    sources = {
        "halogen_lock": (
            "stamping_run_id",
            standing_diagnostic_isotopologues_record(anchor),
        ),
        "neutral_pair": ("co_variation_run_id", standing_co_variation_record(anchor)),
    }
    facts = {}
    for key in BATCH_CHECK_FACTS:
        fact = record.get(key)
        if not isinstance(fact, dict):
            continue
        if key in sources:
            field, source = sources[key]
            if source is None or _record_run(source) != fact.get(field):
                continue
        facts[key] = fact
    if not facts:
        return None
    return {
        **{key: value for key, value in record.items() if key not in BATCH_CHECK_FACTS},
        **facts,
    }


def row_batch_checks(row: dict, anchor: Any) -> Optional[dict]:
    """The batch checks record of the anchor a sample's row sits in, where the
    row reads what the record was read for; None otherwise.

    A monoisotopic row through the record's formula, ion and mechanism is the
    anchor's reading in one sample, and so is a reagent row naming its ion;
    the inspector shows the batch's facts beside it. An isotope line, or a row
    that reads the peak another way, is not what was read.

    :param row: The sample's row, with ``role``, ``assigned_formula``,
        ``ion_formula`` and ``ionization_mechanism_id``.
    :param anchor: The anchor its peak sits in, or None.
    """
    if anchor is None or row.get("role") not in (ROLE_M0, ROLE_REAGENT):
        return None
    record = standing_batch_checks_record(anchor)
    if record is None:
        return None
    formula = row.get("assigned_formula") if row.get("role") == ROLE_M0 else None
    if not batch_checks_record_reads(
        record,
        row.get("ion_formula"),
        formula,
        row.get("ionization_mechanism_id"),
    ):
        return None
    return record


#: The provenance key the known-species decision
#: (``batch_known_species.py``) writes an anchor's record under: the species,
#: the batch's verdict on it and the evidence pooled for it, what the claim
#: wrote and what it kept, per sample what each rewritten member read before
#: (``displaced_members``), and the reading the anchor holds since (``reads``).
KNOWN_SPECIES_KEY = "known_species"

#: The parts of an anchor's reading a known-species record is read for.
_READING_KEYS = (
    ("role", "consensus_role"),
    ("formula", "consensus_formula"),
    ("ion_formula", "consensus_ion_formula"),
    ("ionization_mechanism_id", "ionization_mechanism_id"),
)


def known_species_record_of(anchor: Any) -> Optional[dict]:
    """The known-species record an anchor carries under
    ``provenance.known_species``, or None. A record names its verdict and the
    reading it was decided for; anything else under that key is not one."""
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(KNOWN_SPECIES_KEY)
    if (
        isinstance(record, dict)
        and record.get("verdict")
        and isinstance(record.get("reads"), dict)
    ):
        return record
    return None


def known_species_reads(record: dict, anchor: Any) -> bool:
    """Whether a known-species record stands beside an anchor's reading: its
    role, formula, ion and mechanism are the ones the decision left it with.

    :param record: The record (:func:`known_species_record_of`).
    :param anchor: The anchor, or its fresh :class:`Consensus`.
    """
    reads = record.get("reads") or {}
    return all(
        reads.get(key) == _member(anchor, column) for key, column in _READING_KEYS
    )


def reading_of(anchor: Any) -> dict:
    """An anchor's reading as a known-species record names it
    (:func:`known_species_reads`)."""
    return {key: _member(anchor, column) for key, column in _READING_KEYS}


def standing_known_species_record(anchor: Any) -> Optional[dict]:
    """The known-species record an anchor carries, where the anchor still
    reads what the decision left it reading; None otherwise.

    The decision claims the species in the anchor's members, so the consensus
    reads the species by its members' own vote. A new sample's vote, a
    curation or a re-fold that moves the reading takes the record off
    (:func:`compute_consensus`); a new decision replaces every record.
    """
    record = known_species_record_of(anchor)
    if record is None or not known_species_reads(record, anchor):
        return None
    return record


def known_species_summary(anchor: Any) -> Optional[dict]:
    """What the ledger serves of a known-species record: everything but what
    each member read before, which the decision's run snapshot keeps as the
    members' own state, and the samples whose own test held, which the next
    decision reads (the record's ``test_held`` counts them)."""
    record = standing_known_species_record(anchor)
    if record is None:
        return None
    return {
        key: value
        for key, value in record.items()
        if key not in _KNOWN_SPECIES_KEPT_BACK
    }


#: What a known-species record keeps for the decision alone: the ledger and a
#: run's snapshot serve the rest (:func:`known_species_summary`).
_KNOWN_SPECIES_KEPT_BACK = frozenset({"displaced_members", "own_test_samples"})


#: The provenance key the water-ladder pass (``batch_water_ladder.py``) writes
#: an anchor's record under, on a batch peak at a rung of a reagent ion's water
#: ladder that passed in an acquisition segment: the rung, its reading in each
#: segment, what the claim wrote and what it kept, per sample what each
#: rewritten member read before (``displaced_members``), the samples whose
#: member reads the rung on the ladder's word (``claimed_samples``), and the
#: reading the anchor holds since (``reads``).
WATER_LADDER_KEY = "water_ladder"


def water_ladder_record_of(anchor: Any) -> Optional[dict]:
    """The water-ladder record an anchor carries under
    ``provenance.water_ladder``, or None. A record names its rung and the
    reading it was written for; anything else under that key is not one."""
    provenance = _member(anchor, "provenance")
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(WATER_LADDER_KEY)
    if (
        isinstance(record, dict)
        and isinstance(record.get("rung"), dict)
        and isinstance(record.get("reads"), dict)
    ):
        return record
    return None


def water_ladder_reads(record: dict, anchor: Any) -> bool:
    """Whether a water-ladder record stands beside an anchor's reading: its
    role, formula, ion and mechanism are the ones the pass left it with.

    :param record: The record (:func:`water_ladder_record_of`).
    :param anchor: The anchor, or its fresh :class:`Consensus`.
    """
    reads = record.get("reads") or {}
    return all(
        reads.get(key) == _member(anchor, column) for key, column in _READING_KEYS
    )


def standing_water_ladder_record(anchor: Any) -> Optional[dict]:
    """The water-ladder record an anchor carries, where the anchor still reads
    what the pass left it reading; None otherwise.

    The pass claims the rung in the anchor's members of the segments it passed
    in, so the consensus reads what their vote reads. A new sample's vote, a
    curation or a re-fold that moves the reading takes the record off
    (:func:`compute_consensus`); a new pass replaces every record.
    """
    record = water_ladder_record_of(anchor)
    if record is None or not water_ladder_reads(record, anchor):
        return None
    return record


def water_ladder_summary(anchor: Any) -> Optional[dict]:
    """What the ledger serves of a water-ladder record: everything but what
    each member read before, which the pass's run snapshot keeps as the
    members' own state, and the samples it claimed, which the next
    measurement and *Decide known species* read."""
    record = standing_water_ladder_record(anchor)
    if record is None:
        return None
    return {
        key: value
        for key, value in record.items()
        if key not in _WATER_LADDER_KEPT_BACK
    }


#: What a water-ladder record keeps for the passes alone: the ledger and a
#: run's snapshot serve the rest (:func:`water_ladder_summary`).
_WATER_LADDER_KEPT_BACK = frozenset({"displaced_members", "claimed_samples"})


def resolution_from_tol_ppm(
    tol_ppm: Optional[float], drift_margin_ppm: float = DEFAULT_DRIFT_MARGIN_PPM
) -> Optional[float]:
    """The resolving power an anchor's frozen tolerance was minted at, or None
    where it was minted without one: :func:`resolution_adaptive_tol_ppm`
    inverted, half the FWHM being the tolerance less the drift margin."""
    if tol_ppm is None or not tol_ppm > drift_margin_ppm + 1e-9:
        return None
    return 0.5e6 / (tol_ppm - drift_margin_ppm)


def member_state(member: Any) -> dict:
    """What a member reads - the fields a curation may overwrite and a release
    puts back. ``tier`` and ``role`` stay as the codes the row stores."""
    return {
        "sample_item_id": _member(member, "sample_item_id"),
        "candidate": _member(member, "candidate"),
        "tier": _member(member, "tier"),
        "fit_score": _member(member, "fit_score"),
        "role": _member(member, "role"),
        "owner_batch_peak_id": _member(member, "owner_batch_peak_id"),
        "p_correct": _member(member, "p_correct"),
    }


def _pinned_without_support(
    manual: dict, n_present: int, brightest: Optional[float]
) -> Consensus:
    """A pinned anchor none of whose members carry the pin: the claim stands, as
    a candidate with no measured support, and says so."""
    return Consensus(
        consensus_formula=manual["formula"],
        consensus_ion_formula=manual.get("ion_formula"),
        ionization_mechanism_id=manual.get("ionization_mechanism_id"),
        consensus_tier=TIER_CANDIDATE,
        best_fit_score=None,
        support_fraction=0.0,
        n_present=n_present,
        is_ambiguous=True,
        max_intensity=brightest,
        isotopologue_of=None,
        alternatives=[],
        provenance={
            "n_assigned": 0,
            "n_winner": 0,
            "winner_evidence_share": 0.0,
            "agreement": 0.0,
            "p_correct": None,
            "manual": manual,
            "vote_winner": None,
        },
    )


def _source_consensus(
    members: list,
    batch_peak_id: Optional[str],
    n_present: int,
    brightest: Optional[float],
) -> Consensus:
    """The consensus of an anchor most of whose members a pre-pass claimed.

    The anchor takes the role - the one most of the claimed members carry, the
    reagent on a tie - with the ion formula and the mechanism most of that
    role's members name, so the batch ledger reads the source's ion as the
    per-sample ledger does rather than as a peak nothing explained, or as the
    neutral a few samples' stages read it as. As the formula is voted over the
    members that carry one, the role is taken over the members that carry a
    role, and prevalence stays ``n_present``: a reagent ion claimed in four
    samples and below the claim in the other six is still the reagent's. The
    tier stays ``unassigned``.

    :param members: The anchor's members, at least one of them claimed.
    """
    claimed = _claimed(members)
    counts = Counter(_member(m, "role") for m in claimed)
    role = max(SOURCE_ROLES, key=lambda candidate: counts[candidate])
    holding = [m for m in claimed if _member(m, "role") == role]
    return Consensus(
        consensus_ion_formula=_mode(_member(m, "ion_formula") for m in holding),
        ionization_mechanism_id=_mode(
            _member(m, "ionization_mechanism_id") for m in holding
        ),
        consensus_role=role,
        n_present=n_present,
        max_intensity=brightest,
        isotopologue_of=resolve_isotopologue_of(members, batch_peak_id),
        provenance={"n_role": len(holding)},
    )


def compute_consensus(
    members: Iterable[Any],
    batch_peak_id: Optional[str] = None,
    manual: Optional[dict] = None,
    series: Optional[dict] = None,
    co_variation: Optional[dict] = None,
    diagnostic_isotopologues: Optional[dict] = None,
    stamped_isotopologue_of: Optional[dict] = None,
    merged_isotopologue_of: Optional[dict] = None,
    merged_isotopologues: Optional[dict] = None,
    batch_checks: Optional[dict] = None,
    known_species: Optional[dict] = None,
    water_ladder: Optional[dict] = None,
) -> Consensus:
    """:func:`vote_consensus`, with the anchor's series, co-variation,
    stamping, strip, batch checks, known-species and water-ladder records
    carried along.

    The series record says a homologous series proposed a formula for this
    anchor. It is about that formula and not about the peak, so it stays in the
    consensus's provenance for as long as the consensus names the formula, and
    comes off when a new sample's vote, a curation or a re-fold moves the
    anchor to another reading: a series never vouches for a formula it did not
    propose. The co-variation record is about the anchor's reading - its
    formula through its mechanism, read against the neutral's other channels -
    and stays for as long as the consensus names both; so does the diagnostic
    isotopologues record, the lines predicted for that reading. A stamp record
    is about the line's height against its parent, not about what the line's
    own members read, so it stays whatever the line's consensus names; the
    parent's recompute takes it off when the parent's reading moves. A row the
    merged-isotopologue reading stripped keeps its record while its members
    still name that parent (:func:`merged_isotopologue_of_reads`), and the
    parent keeps its list of lines while it names the reading they were read
    against; a row whose record comes off comes off that list too, which the
    row's recompute does (:func:`without_merged_lines`), as the parent need
    not be in it. The batch checks record states facts of the anchor's
    reading - its ion, its formula and its mechanism - and stays for as long
    as the consensus names all three (:func:`batch_checks_record_reads`). A
    known-species record stays while the anchor reads what the decision left
    it reading (:func:`known_species_reads`), and a water-ladder record while
    it reads what the pass left it reading (:func:`water_ladder_reads`).

    :param members: The batch peak's occurrences.
    :param batch_peak_id: This batch peak's own id.
    :param manual: A manual curation pinned on the anchor.
    :param series: The series record the anchor carries
        (:func:`series_record_of`), or None.
    :param co_variation: The co-variation record the anchor carries
        (:func:`co_variation_record_of`), or None.
    :param diagnostic_isotopologues: The diagnostic isotopologues record the
        anchor carries (:func:`diagnostic_isotopologues_record_of`), or None.
    :param stamped_isotopologue_of: The stamp record the anchor carries
        (:func:`stamped_isotopologue_of_record`), or None.
    :param merged_isotopologue_of: The stripped row's record the anchor
        carries (:func:`merged_isotopologue_of_record`), or None.
    :param merged_isotopologues: The parent's record the anchor carries
        (:func:`merged_isotopologues_record`), or None.
    :param batch_checks: The batch checks record the anchor carries
        (:func:`batch_checks_record_of`), or None.
    :param known_species: The known-species record the anchor carries
        (:func:`known_species_record_of`), or None.
    :param water_ladder: The water-ladder record the anchor carries
        (:func:`water_ladder_record_of`), or None.
    """
    consensus = vote_consensus(members, batch_peak_id=batch_peak_id, manual=manual)
    if (
        isinstance(series, dict)
        and consensus.consensus_formula
        and series.get("formula") == consensus.consensus_formula
    ):
        consensus.provenance[SERIES_KEY] = series
    if isinstance(co_variation, dict) and _reads(
        co_variation, consensus.consensus_formula, consensus.ionization_mechanism_id
    ):
        consensus.provenance[CO_VARIATION_KEY] = co_variation
    if isinstance(diagnostic_isotopologues, dict) and _reads(
        diagnostic_isotopologues,
        consensus.consensus_formula,
        consensus.ionization_mechanism_id,
    ):
        consensus.provenance[DIAGNOSTIC_ISOTOPOLOGUES_KEY] = diagnostic_isotopologues
    if isinstance(stamped_isotopologue_of, dict) and stamped_isotopologue_of.get(
        "batch_peak_id"
    ):
        consensus.provenance[STAMPED_ISOTOPOLOGUE_OF_KEY] = stamped_isotopologue_of
    if isinstance(merged_isotopologue_of, dict) and merged_isotopologue_of_reads(
        merged_isotopologue_of, consensus.isotopologue_of
    ):
        consensus.provenance[MERGED_ISOTOPOLOGUE_OF_KEY] = merged_isotopologue_of
    if isinstance(merged_isotopologues, dict) and _reads(
        merged_isotopologues,
        consensus.consensus_formula,
        consensus.ionization_mechanism_id,
    ):
        consensus.provenance[MERGED_ISOTOPOLOGUES_KEY] = merged_isotopologues
    if isinstance(batch_checks, dict) and batch_checks_record_reads(
        batch_checks,
        consensus.consensus_ion_formula,
        consensus.consensus_formula,
        consensus.ionization_mechanism_id,
    ):
        consensus.provenance[BATCH_CHECKS_KEY] = batch_checks
    if isinstance(known_species, dict) and known_species_reads(
        known_species, consensus
    ):
        consensus.provenance[KNOWN_SPECIES_KEY] = known_species
    if isinstance(water_ladder, dict) and water_ladder_reads(water_ladder, consensus):
        consensus.provenance[WATER_LADDER_KEY] = water_ladder
    return consensus


def vote_consensus(
    members: Iterable[Any],
    batch_peak_id: Optional[str] = None,
    manual: Optional[dict] = None,
) -> Consensus:
    """Evidence-weighted consensus of a batch peak's per-sample members.

    ``members`` is the batch peak's occurrences, each carrying the member's
    per-sample assignment: ``assigned_formula``, ``ion_formula``,
    ``ionization_mechanism_id``, ``tier``, ``fit_score``, ``intensity``,
    ``p_correct``, ``role``, ``owner_batch_peak_id`` (any may be absent/None).
    Members with no ``assigned_formula`` (unassigned peaks) count toward
    prevalence and intensity only. Members a pre-pass claimed (a reagent's ion,
    an artifact) compete with the assigned ones for the anchor: where they
    outnumber them, the anchor is read by their role, with their ion
    (:func:`_source_consensus`), unless a pin names a formula.

    Confidence (formula, tier, support) is decided over the **assigned** members;
    prevalence (``n_present``) is reported separately. Ties and blend-like
    disagreement set ``is_ambiguous`` and populate ``alternatives`` rather than
    inventing a single certain answer.

    :param members: The batch peak's occurrences.
    :param batch_peak_id: This batch peak's own id, used only to keep the
        isotopologue link from pointing at itself.
    :param manual: A manual curation pinned on the anchor (``manual_pin_of``).
        The consensus then claims the pinned formula, ion and mechanism
        whatever the vote says: its tier is rolled up over the members that
        carry the pin (``candidate`` when none do), its support is their
        share of the assigned members, the vote's own winner is kept under
        ``provenance.vote_winner`` and a disagreement reads as ambiguous. The
        pin itself travels in the provenance, so a recompute never drops it.
    """
    members = list(members)
    n_present = len(members)
    brightest = max_intensity(members)
    assigned = [m for m in members if _member(m, "assigned_formula")]

    pin = manual if isinstance(manual, dict) and manual.get("formula") else None
    if pin is None and len(_claimed(members)) > len(assigned):
        return _source_consensus(members, batch_peak_id, n_present, brightest)
    if not assigned:
        if pin is not None:
            return _pinned_without_support(pin, n_present, brightest)
        return Consensus(n_present=n_present, max_intensity=brightest)

    # Evidence-weighted vote per neutral formula.
    weight_by_formula: dict[str, float] = defaultdict(float)
    members_by_formula: dict[str, list] = defaultdict(list)
    for m in assigned:
        f = _member(m, "assigned_formula")
        weight_by_formula[f] += _vote_weight(
            _member(m, "fit_score"), _member(m, "intensity")
        )
        members_by_formula[f].append(m)

    total_weight = sum(weight_by_formula.values()) or 1.0
    ranked = sorted(weight_by_formula.items(), key=lambda kv: kv[1], reverse=True)
    vote_winner = ranked[0][0]
    winner = pin["formula"] if pin is not None else vote_winner
    winner_weight = weight_by_formula.get(winner, 0.0)
    winner_members = members_by_formula.get(winner, [])

    winner_share = winner_weight / total_weight
    # Agreement = fraction of ASSIGNED members that back the winner (count-based).
    support_fraction = len(winner_members) / len(assigned)

    # Winning ion formula / mechanism = the mode among the winner's members
    # (they share the neutral formula; the adduct is essentially fixed by m/z).
    if pin is not None:
        # The pin names the identity; the members that carry it decide how well
        # it is measured, and none carrying it is a candidate claim, not an
        # assigned one.
        ion_formula = pin.get("ion_formula")
        mechanism = pin.get("ionization_mechanism_id")
        consensus_tier = (
            _rollup_tier(winner_members) if winner_members else TIER_CANDIDATE
        )
    else:
        ion_formula = _mode(_member(m, "ion_formula") for m in winner_members)
        mechanism = _mode(_member(m, "ionization_mechanism_id") for m in winner_members)
        consensus_tier = _rollup_tier(winner_members)
    best_fit = max(
        (
            _member(m, "fit_score")
            for m in winner_members
            if _member(m, "fit_score") is not None
        ),
        default=None,
    )

    # Tie / blend honesty.
    others = [(f, w) for f, w in ranked if f != winner]
    runner_share = (others[0][1] / total_weight) if others else 0.0
    is_ambiguous = (
        ((winner_share - runner_share) <= CONSENSUS_TIE_TOL and bool(others))
        or support_fraction < AMBIGUOUS_SUPPORT
        or (pin is not None and vote_winner != winner)
    )

    alternatives = [
        {
            "formula": f,
            "evidence_share": round(w / total_weight, 4),
            "n": len(members_by_formula[f]),
        }
        for f, w in others[:3]
    ]

    p_values = [
        _member(m, "p_correct")
        for m in winner_members
        if _member(m, "p_correct") is not None
    ]
    provenance = {
        "n_assigned": len(assigned),
        "n_winner": len(winner_members),
        "winner_evidence_share": round(winner_share, 4),
        "agreement": round(support_fraction, 4),
        # Conservative for now: the best calibrated per-sample probability. A capped
        # multi-sample corroboration lift is a later increment (see design doc).
        "p_correct": (max(p_values) if p_values else None),
    }

    if pin is not None:
        provenance["manual"] = pin
        provenance["vote_winner"] = vote_winner
    return Consensus(
        consensus_formula=winner,
        consensus_ion_formula=ion_formula,
        ionization_mechanism_id=mechanism,
        consensus_tier=consensus_tier,
        best_fit_score=best_fit,
        support_fraction=round(support_fraction, 4),
        n_present=n_present,
        is_ambiguous=bool(is_ambiguous),
        max_intensity=brightest,
        isotopologue_of=resolve_isotopologue_of(members, batch_peak_id),
        alternatives=alternatives,
        provenance=provenance,
    )


def _mode(values: Iterable[Any]) -> Optional[Any]:
    counts = Counter(v for v in values if v is not None)
    return counts.most_common(1)[0][0] if counts else None


def _rollup_tier(winner_members: list) -> str:
    """Batch tier from the winner members' per-sample tiers, evidence-weighted:
    ``assigned`` only when a weighted majority reach it, else ``candidate`` when a
    weighted majority are candidate-or-better, else ``below_assignability``.
    """
    weighted: dict[str, float] = defaultdict(float)
    total = 0.0
    for m in winner_members:
        w = _vote_weight(_member(m, "fit_score"), _member(m, "intensity"))
        weighted[_member(m, "tier") or TIER_UNASSIGNED] += w
        total += w
    if total <= 0:
        return TIER_BELOW_ASSIGNABILITY
    id_frac = weighted.get(TIER_ASSIGNED, 0.0) / total
    cand_or_better = id_frac + weighted.get(TIER_CANDIDATE, 0.0) / total
    if id_frac >= 0.5:
        return TIER_ASSIGNED
    if cand_or_better >= 0.5:
        return TIER_CANDIDATE
    return TIER_BELOW_ASSIGNABILITY
