"""Unit tests for the pure batch-peak fold-in + consensus engine.

No DB / I/O -- exercises the append-only anchor invariant (existing batch peaks
never move when a sample arrives), the resolution-adaptive membership, the
evidence-weighted consensus (confidence over assigned members, prevalence kept
separate, ties surfaced), and the two ledger aggregates the consensus pass rolls
up alongside it: the brightest member and the isotopologue family link. See
docs/dev/peak_assignment_batch.md.
"""

import itertools

import pytest

from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_ISO_CHILD,
    Anchor,
    AnchorSet,
    candidate_index,
    compute_consensus,
    fold_in_sample,
    max_intensity,
    mz_delta_ppm,
    mz_from_delta,
    resolution_adaptive_tol_ppm,
    resolve_candidate,
    resolve_isotopologue_of,
)


def _ids():
    counter = itertools.count()
    return lambda: f"bp{next(counter)}"


# --- tolerance ---------------------------------------------------------------


def test_resolution_adaptive_tol_is_half_fwhm_plus_margin():
    # FWHM_ppm = 1e6 / R = 10 ppm at R=1e5; half = 5 ppm; + margin 2 = 7.
    assert resolution_adaptive_tol_ppm(
        200.0, 100_000, drift_margin_ppm=2.0
    ) == pytest.approx(7.0)
    # No resolution -> margin only.
    assert resolution_adaptive_tol_ppm(
        200.0, None, drift_margin_ppm=2.0
    ) == pytest.approx(2.0)


# --- anchor snapping ---------------------------------------------------------


def test_find_returns_nearest_in_tolerance_or_none():
    aset = AnchorSet([Anchor("a", 100.0, 5.0), Anchor("b", 100.001, 5.0)])
    # 100.0006: 6 ppm from a (out), ~4 ppm from b (in) -> b.
    assert aset.get(aset.find(100.0006)).batch_peak_id == "b"
    # 100.05: far from both -> None.
    assert aset.find(100.05) is None


# --- fold-in: append-only stability -----------------------------------------


def test_fold_in_creates_and_snaps():
    aset = AnchorSet()
    tol = lambda mz: 5.0  # noqa: E731
    folded = fold_in_sample(
        aset, [{"mz": 100.0}, {"mz": 200.0}], new_id=_ids(), tol_fn=tol
    )
    assert len(aset) == 2
    assert all(f.is_new_anchor for f in folded)

    # A second sample: one peak within tolerance of 100, one brand new at 300.
    before = {a.batch_peak_id: a.mz for a in aset.anchors()}
    folded2 = fold_in_sample(
        aset, [{"mz": 100.0004}, {"mz": 300.0}], new_id=lambda: "NEW", tol_fn=tol
    )
    after = {a.batch_peak_id: a.mz for a in aset.anchors()}

    # Existing anchors are FROZEN: same ids, same m/z (the streaming-stability core).
    for bid, mz in before.items():
        assert after[bid] == mz
    # The 100.0004 peak joined the existing 100.0 anchor (not a new one).
    joined = [f for f in folded2 if not f.is_new_anchor]
    assert len(joined) == 1
    assert aset.get(aset.find(100.0)).batch_peak_id == joined[0].batch_peak_id
    # The 300 peak minted exactly one new anchor.
    assert sum(f.is_new_anchor for f in folded2) == 1
    assert len(aset) == 3


def test_fold_in_is_order_independent():
    tol = lambda mz: 5.0  # noqa: E731
    a1 = AnchorSet()
    fold_in_sample(
        a1, [{"mz": 100.0}, {"mz": 150.0}, {"mz": 200.0}], new_id=_ids(), tol_fn=tol
    )
    a2 = AnchorSet()
    fold_in_sample(
        a2, [{"mz": 200.0}, {"mz": 100.0}, {"mz": 150.0}], new_id=_ids(), tol_fn=tol
    )
    assert [round(a.mz, 6) for a in a1.anchors()] == [
        round(a.mz, 6) for a in a2.anchors()
    ]


def test_same_sample_collision_keeps_one_member_nearest_wins():
    aset = AnchorSet([Anchor("a", 100.0, 10.0)])
    # Two peaks in one sample both within tolerance of anchor a; nearest wins.
    folded = fold_in_sample(
        aset,
        [{"mz": 100.0008, "tag": "far"}, {"mz": 100.0002, "tag": "near"}],
        new_id=lambda: "NEW",
        tol_fn=lambda mz: 10.0,
    )
    assert len(folded) == 1
    assert folded[0].batch_peak_id == "a"
    assert folded[0].peak["tag"] == "near"
    assert len(aset) == 1  # no spurious new anchor


# --- consensus ---------------------------------------------------------------


def test_consensus_evidence_weighted_winner_beats_low_fit_flips():
    members = [
        {
            "assigned_formula": "C6H12O6",
            "ion_formula": "C6H11O6-",
            "ionization_mechanism_id": "mH",
            "tier": "assigned",
            "fit_score": 0.95,
            "intensity": 1e5,
            "p_correct": 0.9,
        },
        {
            "assigned_formula": "C6H12O6",
            "ion_formula": "C6H11O6-",
            "ionization_mechanism_id": "mH",
            "tier": "assigned",
            "fit_score": 0.90,
            "intensity": 9e4,
            "p_correct": 0.88,
        },
        {
            "assigned_formula": "C5H8O",
            "ion_formula": "C5H7O-",
            "ionization_mechanism_id": "mH",
            "tier": "candidate",
            "fit_score": 0.50,
            "intensity": 1e3,
            "p_correct": None,
        },
    ]
    c = compute_consensus(members)
    assert c.consensus_formula == "C6H12O6"
    assert c.consensus_ion_formula == "C6H11O6-"
    assert c.consensus_tier == "assigned"
    assert c.n_present == 3
    assert c.support_fraction == pytest.approx(2 / 3, rel=1e-3)
    assert c.best_fit_score == pytest.approx(0.95)
    assert not c.is_ambiguous
    assert c.provenance["p_correct"] == pytest.approx(0.9)


def test_consensus_prevalence_separate_from_confidence():
    # Assigned in 3 samples, present-but-unassigned in 2 more.
    members = [
        {
            "assigned_formula": "A",
            "tier": "assigned",
            "fit_score": 0.9,
            "intensity": 1e4,
        }
        for _ in range(3)
    ] + [
        {
            "assigned_formula": None,
            "tier": "unassigned",
            "fit_score": None,
            "intensity": 5e2,
        }
        for _ in range(2)
    ]
    c = compute_consensus(members)
    assert c.consensus_formula == "A"
    assert c.n_present == 5  # prevalence counts all detected members
    assert c.support_fraction == pytest.approx(1.0)  # agreement among ASSIGNED only
    assert c.consensus_tier == "assigned"


def test_consensus_tie_is_flagged_ambiguous_with_alternatives():
    members = [
        {
            "assigned_formula": "A",
            "tier": "candidate",
            "fit_score": 0.6,
            "intensity": 1e3,
        },
        {
            "assigned_formula": "B",
            "tier": "candidate",
            "fit_score": 0.6,
            "intensity": 1e3,
        },
    ]
    c = compute_consensus(members)
    assert c.is_ambiguous
    assert {alt["formula"] for alt in c.alternatives} == {"B"} or {"A"}
    assert c.support_fraction == pytest.approx(0.5)


def test_consensus_all_unassigned_is_a_valid_drawable_peak():
    members = [
        {
            "assigned_formula": None,
            "tier": "unassigned",
            "fit_score": None,
            "intensity": 1e3,
        },
        {
            "assigned_formula": None,
            "tier": "unassigned",
            "fit_score": None,
            "intensity": 2e3,
        },
    ]
    c = compute_consensus(members)
    assert c.consensus_formula is None
    assert c.consensus_tier == "unassigned"
    assert c.n_present == 2  # still a trace, just unlabelled


def test_consensus_tier_downgrades_when_members_are_candidates():
    members = [
        {
            "assigned_formula": "A",
            "tier": "candidate",
            "fit_score": 0.7,
            "intensity": 1e4,
        },
        {
            "assigned_formula": "A",
            "tier": "candidate",
            "fit_score": 0.7,
            "intensity": 1e4,
        },
        {
            "assigned_formula": "A",
            "tier": "assigned",
            "fit_score": 0.6,
            "intensity": 1e2,
        },
    ]
    c = compute_consensus(members)
    assert c.consensus_formula == "A"
    # Weighted majority are candidate (the assigned member is weak) -> candidate.
    assert c.consensus_tier == "candidate"


# --- ledger aggregates: brightest member --------------------------------------


def test_max_intensity_is_taken_over_every_member_not_the_assigned_ones():
    # How bright a species gets is a property of the trace, so the unassigned
    # sample that happens to be the brightest is the answer.
    members = [
        {"assigned_formula": "A", "intensity": 1e4},
        {"assigned_formula": None, "intensity": 5e4},
    ]
    assert max_intensity(members) == pytest.approx(5e4)
    assert compute_consensus(members).max_intensity == pytest.approx(5e4)


def test_max_intensity_skips_a_member_carrying_none_rather_than_reading_zero():
    assert max_intensity(
        [{"intensity": None}, {"intensity": 12.0}, {"intensity": None}]
    ) == pytest.approx(12.0)
    # Nothing to report is None, not 0 -- the ledger shows a dash for it.
    assert max_intensity([{"intensity": None}]) is None
    assert max_intensity([]) is None


def test_an_all_unassigned_batch_peak_still_reports_its_brightest_member():
    # The early return for a peak nothing was assigned to takes a different path
    # through compute_consensus, and the intensity has to survive it: an
    # unassigned trace is exactly the kind a user sorts the ledger to find.
    c = compute_consensus(
        [
            {"assigned_formula": None, "tier": "unassigned", "intensity": 1e3},
            {"assigned_formula": None, "tier": "unassigned", "intensity": 7e3},
        ]
    )
    assert c.consensus_formula is None
    assert c.max_intensity == pytest.approx(7e3)
    assert c.isotopologue_of is None


# --- ledger aggregates: the isotopologue family link --------------------------
#
# A batch peak is a bare m/z anchor and carries no family link of its own, so the
# link is a vote of its members' per-sample roles. The rule is a strict majority
# of the ASSIGNED members, the same population the consensus measures agreement
# over -- which is what these cover: prevalence must not dilute it, disagreement
# must not resolve it, and an isotopologue that names no owner must not vote.


def _child(owner, formula="A", **extra):
    """An iso_child member pointing at ``owner``'s anchor."""
    return {
        "assigned_formula": formula,
        "role": ROLE_ISO_CHILD,
        "owner_batch_peak_id": owner,
        **extra,
    }


def _m0(formula="A", **extra):
    return {"assigned_formula": formula, "role": "M0", **extra}


def test_role_constant_matches_the_assignment_engine():
    # batch_peaks is pure and cannot import the engine (pandas, numpy, the id
    # helper), so the roles it compares against are spelled twice. This is the
    # tripwire that keeps the spellings one value each.
    from mascope_backend.api.new.peak_assignments import batch_peaks, engine

    assert ROLE_ISO_CHILD == engine.ROLE_ISO_CHILD
    assert batch_peaks.ROLE_M0 == engine.ROLE_M0
    assert batch_peaks.ROLE_UNASSIGNED == engine.ROLE_UNASSIGNED


def test_the_tier_codes_are_the_tier_ranks():
    from mascope_backend.api.new.peak_assignments.batch_peaks import TIER_CODES
    from mascope_backend.api.new.peak_assignments.tiers import TIER_RANK

    assert TIER_CODES == TIER_RANK


def test_the_mz_offset_round_trips_within_single_precision():
    import struct

    anchor, mz = 500.123456, 500.1256
    delta = mz_delta_ppm(mz, anchor)
    assert delta == pytest.approx((mz - anchor) / anchor * 1e6)
    # Stored as a REAL: round the offset to float32 and recover the m/z.
    stored = struct.unpack("f", struct.pack("f", delta))[0]
    assert mz_from_delta(anchor, stored) == pytest.approx(mz, abs=1e-7)
    assert mz_from_delta(anchor, None) == anchor


def test_a_majority_of_assigned_members_makes_it_an_isotopologue():
    assert resolve_isotopologue_of([_child("bp-m0"), _child("bp-m0"), _m0()]) == "bp-m0"


def test_no_majority_leaves_the_anchor_standing_on_its_own():
    # Assigned in its own right more often than it is seen as an isotopologue: it is
    # a peak, not a member of someone's family.
    members = [_child("bp-m0"), _child("bp-m0"), _child("bp-m0"), *[_m0()] * 4]
    assert resolve_isotopologue_of(members) is None


def test_the_majority_is_over_assigned_members_not_prevalence():
    # An isotopologue is often only assignable in the brightest samples. Counting the
    # samples it was merely PRESENT in would leave a real isotopologue unfolded, and
    # prevalence is kept out of confidence everywhere else in this module.
    members = [
        *[_child("bp-m0") for _ in range(3)],
        *[{"assigned_formula": None, "role": "unassigned"} for _ in range(7)],
    ]
    assert resolve_isotopologue_of(members) == "bp-m0"


def test_two_owners_splitting_the_vote_resolve_to_neither():
    # Genuinely ambiguous membership: nothing here holds more than half, and the
    # strict majority is what makes the winner unique without a tie-break.
    assert resolve_isotopologue_of([_child("bp-x"), _child("bp-y")]) is None
    assert (
        resolve_isotopologue_of([_child("bp-x"), _child("bp-x"), _child("bp-y"), _m0()])
        is None
    )


def test_an_ownerless_isotopologue_abstains_but_still_counts_against_the_majority():
    # The engine leaves owner_peak_assignment_id NULL when the family's M0 was
    # not won by the same ion in that run, and the fold leaves the anchor
    # unresolved when the owning peak was dropped from it. Either way the member
    # names no anchor to vote for -- and it is still an assigned member, so it
    # belongs in the denominator.
    assert resolve_isotopologue_of([_child("bp-m0"), _child(None)]) is None
    assert resolve_isotopologue_of(
        [_child("bp-m0"), _child("bp-m0"), _child(None)]
    ) == ("bp-m0")


def test_an_unassigned_member_never_votes_and_never_counts():
    assert resolve_isotopologue_of(
        [{"assigned_formula": None, "role": ROLE_ISO_CHILD}]
    ) is (None)


def test_a_batch_peak_cannot_become_its_own_parent():
    assert resolve_isotopologue_of(
        [_child("bp-self"), _child("bp-self")], "bp-self"
    ) is (None)


def test_members_from_a_reader_that_knows_no_roles_are_simply_not_isotopologues():
    # Every existing caller passes members without a role; they must come through
    # as ordinary anchors rather than raising.
    assert resolve_isotopologue_of([{"assigned_formula": "A"}]) is None


def test_consensus_carries_the_family_link_alongside_the_formula():
    members = [
        _child("bp-m0", tier="assigned", fit_score=0.9, intensity=1e4),
        _child("bp-m0", tier="assigned", fit_score=0.8, intensity=9e3),
    ]
    c = compute_consensus(members, batch_peak_id="bp-iso")
    assert c.isotopologue_of == "bp-m0"
    # The isotopologue is the same species measured at another isotope, so it
    # carries the family's formula -- which is exactly why the ledger reads as a
    # duplicate row until it is folded.
    assert c.consensus_formula == "A"
    assert c.max_intensity == pytest.approx(1e4)


# --- candidates ---------------------------------------------------------------


def test_candidate_index_appends_a_new_identity_and_finds_it_again():
    registry: list = []
    assert candidate_index(registry, "C6H12O6", "C6H13O6+", "mech-1") == 0
    assert candidate_index(registry, "C6H12O6", "C6H13O6+", "mech-1") == 0
    assert candidate_index(registry, "C6H12O6", "C6H12O6Na+", "mech-2") == 1
    assert registry == [
        {
            "formula": "C6H12O6",
            "ion_formula": "C6H13O6+",
            "ionization_mechanism_id": "mech-1",
        },
        {
            "formula": "C6H12O6",
            "ion_formula": "C6H12O6Na+",
            "ionization_mechanism_id": "mech-2",
        },
    ]


def test_candidate_index_is_append_only():
    """Members name entries by position, so an entry once handed out keeps it
    whatever arrives after it."""
    registry = [{"formula": "A", "ion_formula": "A+", "ionization_mechanism_id": None}]
    assert candidate_index(registry, "B", "B+", None) == 1
    assert candidate_index(registry, "A", "A+", None) == 0
    assert [c["formula"] for c in registry] == ["A", "B"]


def test_candidate_index_tells_a_missing_ion_formula_from_a_known_one():
    """A dead-linked member's formula, with no ion formula behind it, is an
    identity of its own rather than a match for the fully known one - the
    consensus must not borrow an ion formula the member never carried."""
    registry: list = []
    assert candidate_index(registry, "A", "A+", "mech-1") == 0
    assert candidate_index(registry, "A", None, None) == 1


def test_resolve_candidate_returns_the_entry_or_nothing():
    registry = [{"formula": "A", "ion_formula": "A+", "ionization_mechanism_id": "m"}]
    assert resolve_candidate(registry, 0) == registry[0]
    assert resolve_candidate(registry, None) == {}
    assert resolve_candidate(registry, 1) == {}
    assert resolve_candidate(registry, -1) == {}
    assert resolve_candidate(None, 0) == {}
    assert resolve_candidate([], 0) == {}
    assert resolve_candidate(["not-a-dict"], 0) == {}
