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

The DB controller (fold-in on arrival, backfill, consensus persistence) calls into
these functions. Design: ``docs/dev/peak_assignment_batch.md``.
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
#: intensity stay on the member row, where the vote reads them).
CANDIDATE_KEYS = ("formula", "ion_formula", "ionization_mechanism_id")


def candidate_index(
    candidates: list[dict],
    formula: str,
    ion_formula: Optional[str],
    ionization_mechanism_id: Optional[str],
    source: Optional[str] = None,
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
    :return: The position the member's row should name.
    """
    for index, entry in enumerate(candidates):
        if (
            entry.get("formula") == formula
            and entry.get("ion_formula") == ion_formula
            and entry.get("ionization_mechanism_id") == ionization_mechanism_id
        ):
            return index
    entry = {
        "formula": formula,
        "ion_formula": ion_formula,
        "ionization_mechanism_id": ionization_mechanism_id,
    }
    if source is not None:
        entry["source"] = source
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


def resolve_isotopologue_of(
    members: Iterable[Any], batch_peak_id: Optional[str] = None
) -> Optional[str]:
    """The batch peak this one is an isotopologue of, or ``None``.

    Batch peaks are bare m/z anchors and carry no family link of their own, so
    the link is derived from the members' per-sample assignments: a member whose
    role is ``iso_child`` names the assignment that owns it, and the anchor that
    owning assignment folded into in the same sample is the owner anchor. The
    caller resolves that hop and hands each member an ``owner_batch_peak_id``.

    The members span samples and need not agree - one sample's isotopologue is
    another's M0, and an ownerless isotopologue names no anchor at all - so this is
    a vote. It is counted over the **assigned** members, the same population
    :func:`compute_consensus` measures agreement over, and prevalence is again
    kept out of it: an isotopologue detected in six of ten samples and assigned in
    none of the other four is still an isotopologue. A strict majority is required,
    which is also what makes the winner unique - two owners cannot each hold
    more than half of one set of votes - so no tie-break is needed.

    One hop only. An isotopologue whose owner anchor is itself an isotopologue is left
    as it was observed; flattening the chain needs the whole ledger and belongs
    to the reader that has it.

    :param members: The batch peak's occurrences, each optionally carrying
        ``role``, ``owner_batch_peak_id`` and ``assigned_formula``.
    :param batch_peak_id: This batch peak's own id, so a member that somehow
        names it cannot make it its own parent.
    :return: The owner's ``batch_peak_id``, or None.
    """
    votes: dict[str, int] = defaultdict(int)
    n_assigned = 0
    for m in members:
        if not _member(m, "assigned_formula"):
            continue
        n_assigned += 1
        if _member(m, "role") != ROLE_ISO_CHILD:
            continue
        owner = _member(m, "owner_batch_peak_id")
        if not owner or owner == batch_peak_id:
            continue
        votes[owner] += 1
    if not votes:
        return None
    owner, n_votes = max(votes.items(), key=lambda kv: kv[1])
    return owner if 2 * n_votes > n_assigned else None


def compute_consensus(
    members: Iterable[Any], batch_peak_id: Optional[str] = None
) -> Consensus:
    """Evidence-weighted consensus of a batch peak's per-sample members.

    ``members`` is the batch peak's occurrences, each carrying the member's
    per-sample assignment: ``assigned_formula``, ``ion_formula``,
    ``ionization_mechanism_id``, ``tier``, ``fit_score``, ``intensity``,
    ``p_correct``, ``role``, ``owner_batch_peak_id`` (any may be absent/None).
    Members with no ``assigned_formula`` (unassigned peaks) count toward
    prevalence and intensity only.

    Confidence (formula, tier, support) is decided over the **assigned** members;
    prevalence (``n_present``) is reported separately. Ties and blend-like
    disagreement set ``is_ambiguous`` and populate ``alternatives`` rather than
    inventing a single certain answer.

    :param members: The batch peak's occurrences.
    :param batch_peak_id: This batch peak's own id, used only to keep the
        isotopologue link from pointing at itself.
    """
    members = list(members)
    n_present = len(members)
    brightest = max_intensity(members)
    assigned = [m for m in members if _member(m, "assigned_formula")]

    if not assigned:
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
    winner, winner_weight = ranked[0]
    winner_members = members_by_formula[winner]

    winner_share = winner_weight / total_weight
    # Agreement = fraction of ASSIGNED members that back the winner (count-based).
    support_fraction = len(winner_members) / len(assigned)

    # Winning ion formula / mechanism = the mode among the winner's members
    # (they share the neutral formula; the adduct is essentially fixed by m/z).
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
    runner_share = (ranked[1][1] / total_weight) if len(ranked) > 1 else 0.0
    is_ambiguous = (
        (winner_share - runner_share) <= CONSENSUS_TIE_TOL and len(ranked) > 1
    ) or support_fraction < AMBIGUOUS_SUPPORT

    alternatives = [
        {
            "formula": f,
            "evidence_share": round(w / total_weight, 4),
            "n": len(members_by_formula[f]),
        }
        for f, w in ranked[1:4]
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
