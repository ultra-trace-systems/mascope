"""Unit tests for the pure batch-peak fold-in + consensus engine.

No DB / I/O -- exercises the append-only anchor invariant (existing batch peaks
never move when a sample arrives), the resolution-adaptive membership, the
evidence-weighted consensus (confidence over assigned members, prevalence kept
separate, ties surfaced), and the two ledger aggregates the consensus pass rolls
up alongside it: the brightest member and the isotopologue family link. See
docs/dev/peak_assignment_batch_primary.md (section 12).
"""

import itertools

import pytest

from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_ISO_CHILD,
    SERIES_KEY,
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
    series_record_of,
    standing_series_record,
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
    assert batch_peaks.ROLE_REAGENT == engine.ROLE_REAGENT
    assert batch_peaks.ROLE_ARTIFACT == engine.ROLE_ARTIFACT


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


# --- a formula-less anchor the source accounts for -----------------------------
#
# Step 3.3c's follow-up, built in 3.4d: a per-sample row the reagent or artifact
# pre-pass claims carries a role and no formula, so an anchor of such rows had
# nothing to vote on and read as unassigned. It reads by the role its members
# carry, with their ion, and a source ion's isotope lines fold under it.


def _source(role="reagent", ion="Br-", mechanism=None, owner=None, **extra):
    """A member a pre-pass claimed: a role and an ion, no formula."""
    return {
        "assigned_formula": None,
        "ion_formula": ion,
        "ionization_mechanism_id": mechanism,
        "role": role,
        "tier": "unassigned",
        "owner_batch_peak_id": owner,
        **extra,
    }


def _bare():
    return {"assigned_formula": None, "role": "unassigned", "tier": "unassigned"}


def test_an_anchor_of_reagent_rows_reads_as_the_reagents_ion():
    c = compute_consensus([_source(intensity=5e4), _source(intensity=4e4)])
    assert (c.consensus_role, c.consensus_ion_formula) == ("reagent", "Br-")
    # No compound was assigned: the tier says so, the role says what it is.
    assert c.consensus_formula is None
    assert c.consensus_tier == "unassigned"
    assert c.n_present == 2
    assert c.max_intensity == pytest.approx(5e4)
    assert c.provenance == {"n_role": 2}


def test_the_role_is_taken_over_the_members_that_carry_one():
    """As the formula is voted over the members that carry one: a reagent ion
    claimed in two samples and below the claim in five is still the reagent's,
    and prevalence stays the whole count."""
    c = compute_consensus([_source(), _source(), *[_bare() for _ in range(5)]])
    assert c.consensus_role == "reagent"
    assert c.n_present == 7


def test_the_ion_and_mechanism_are_those_most_of_the_role_names():
    c = compute_consensus(
        [
            _source(ion="C16H10+", mechanism="m-e"),
            _source(ion="C16H10+", mechanism="m-e"),
            _source(ion="C15H12+", mechanism="m-e"),
        ]
    )
    assert (c.consensus_ion_formula, c.ionization_mechanism_id) == (
        "C16H10+",
        "m-e",
    )


def test_an_artifact_anchor_reads_as_one_with_no_ion():
    c = compute_consensus([_source("artifact", ion=None), _bare()])
    assert (c.consensus_role, c.consensus_ion_formula) == ("artifact", None)


def test_a_tie_between_the_roles_goes_to_the_reagent():
    for members in (
        [_source("reagent"), _source("artifact", ion=None)],
        [_source("artifact", ion=None), _source("reagent")],
    ):
        assert compute_consensus(members).consensus_role == "reagent"


def test_a_few_samples_reading_the_ion_as_a_neutral_do_not_outvote_the_claim():
    """A dim file the claim missed, whose stages read bromine monoxide's ion as
    a neutral through the bromide adduct: the anchor is still the reagent's
    where most of its members were claimed."""
    c = compute_consensus(
        [
            _source(ion="BrO-"),
            _source(ion="BrO-"),
            _m0("O", tier="candidate", fit_score=0.4, ion_formula="BrO-"),
        ]
    )
    assert (c.consensus_role, c.consensus_ion_formula) == ("reagent", "BrO-")
    assert c.consensus_formula is None


def test_an_anchor_most_of_whose_members_carry_a_formula_keeps_it():
    """A batch folded mostly from runs of an engine that read the ion as a
    neutral: the formula has the anchor, and no role."""
    c = compute_consensus(
        [_source(), _m0("Br", fit_score=0.8), _m0("Br", fit_score=0.7)]
    )
    assert c.consensus_formula == "Br"
    assert c.consensus_role is None


def test_a_tie_between_a_claim_and_a_formula_stays_with_the_formula():
    c = compute_consensus([_source(), _m0("Br", fit_score=0.8)])
    assert c.consensus_formula == "Br"
    assert c.consensus_role is None


def test_an_anchor_nothing_accounts_for_is_unassigned_as_before():
    c = compute_consensus([_bare(), _bare()])
    assert (c.consensus_role, c.consensus_ion_formula) == (None, None)
    assert c.provenance == {}


def test_a_pin_outranks_the_role():
    c = compute_consensus([_source()], manual={"formula": "CH2Br2"})
    assert c.consensus_formula == "CH2Br2"
    assert c.consensus_role is None


def test_a_source_ions_lines_fold_under_its_anchor():
    """A reagent row with an owner is a line of its ion's envelope (step 3.3c),
    and the anchor it folds into follows its ion's as an isotopologue does."""
    members = [_source(owner="bp-ion"), _source(owner="bp-ion"), _bare()]
    assert resolve_isotopologue_of(members) == "bp-ion"
    assert compute_consensus(members, batch_peak_id="bp-81br").isotopologue_of == (
        "bp-ion"
    )


def test_the_line_vote_is_a_majority_of_the_claimed_members():
    # A line in one sample, the ion's own monoisotopic row in two others: no
    # majority, so the anchor stands on its own.
    members = [_source(owner="bp-ion"), _source(), _source()]
    assert resolve_isotopologue_of(members) is None


def test_an_artifact_is_never_a_line():
    members = [_source("artifact", ion=None, owner="bp-parent")]
    assert resolve_isotopologue_of(members) is None


def test_a_reagent_line_does_not_vote_where_the_formula_has_the_anchor():
    """Where most members carry a formula, the vote is theirs, as before."""
    members = [_source(owner="bp-ion"), _m0(), _m0()]
    assert resolve_isotopologue_of(members) is None


def test_the_lines_vote_where_the_claim_has_the_anchor():
    members = [_source(owner="bp-ion"), _source(owner="bp-ion"), _m0()]
    assert resolve_isotopologue_of(members) == "bp-ion"


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


def test_candidate_index_records_a_source_on_a_new_entry_only():
    registry: list = []
    assert candidate_index(registry, "A", "A+", "m", source="database") == 0
    # The same identity from elsewhere neither duplicates nor rewrites the entry.
    assert candidate_index(registry, "A", "A+", "m", source="untargeted") == 0
    assert registry == [
        {
            "formula": "A",
            "ion_formula": "A+",
            "ionization_mechanism_id": "m",
            "source": "database",
        }
    ]
    assert candidate_index(registry, "B", "B+", "m") == 1
    assert "source" not in registry[1]


# --- the series record --------------------------------------------------------
#
# The series detection (``batch_series.py``) records on an anchor the series that
# proposed its formula. The record is about that formula, not about the peak, so
# the consensus keeps it while it names the formula and drops it when it moves.

_SERIES = {"formula": "A", "unit": "CH2", "has_anchor": True}


def _fit(formula, fit=0.9, intensity=1000.0, tier="assigned"):
    return _m0(formula, fit_score=fit, intensity=intensity, tier=tier)


def test_the_consensus_carries_a_series_on_the_formula_it_proposed():
    c = compute_consensus([_fit("A"), _fit("A")], series=_SERIES)
    assert c.provenance[SERIES_KEY] == _SERIES


def test_a_consensus_that_moved_to_another_formula_drops_it():
    c = compute_consensus([_fit("B"), _fit("B")], series=_SERIES)
    assert c.consensus_formula == "B"
    assert SERIES_KEY not in c.provenance


def test_an_anchor_nothing_reads_drops_it():
    c = compute_consensus([_bare()], series=_SERIES)
    assert SERIES_KEY not in c.provenance


def test_no_series_leaves_the_provenance_as_the_vote_wrote_it():
    assert SERIES_KEY not in compute_consensus([_fit("A")]).provenance


def test_a_pin_on_the_proposed_formula_keeps_it_beside_the_pin():
    c = compute_consensus([_fit("A")], manual={"formula": "A"}, series=_SERIES)
    assert c.provenance["manual"] == {"formula": "A"}
    assert c.provenance[SERIES_KEY] == _SERIES


@pytest.mark.parametrize(
    "provenance, record",
    [
        ({SERIES_KEY: _SERIES}, _SERIES),
        ({SERIES_KEY: {"unit": "CH2"}}, None),
        ({SERIES_KEY: "CH2"}, None),
        ({"manual": {"formula": "A"}}, None),
        (None, None),
    ],
)
def test_a_series_record_names_a_formula(provenance, record):
    assert series_record_of({"provenance": provenance}) == record


@pytest.mark.parametrize(
    "consensus_formula, record",
    [("A", _SERIES), ("B", None), (None, None)],
)
def test_a_series_record_stands_beside_the_formula_it_names(consensus_formula, record):
    # A detection writes its records before it measures them: an anchor it has
    # not reached yet carries one beside the reading it held before.
    anchor = {
        "provenance": {SERIES_KEY: _SERIES},
        "consensus_formula": consensus_formula,
    }
    assert standing_series_record(anchor) == record


def test_a_malformed_record_stands_nowhere():
    anchor = {"provenance": {SERIES_KEY: {"unit": "CH2"}}, "consensus_formula": "A"}
    assert standing_series_record(anchor) is None


# --- persistence: the batch search's second admission (step 4.2b) ---------------
#
# The batch search records on an anchor that it took the anchor's peak because
# the peak recurs across the batch rather than for its height, and the reading
# it elected there is held at candidate until a second observation lifts it.

_ADMITTED = {
    "share": 0.9167,
    "n_present": 11,
    "n_samples": 12,
    "split": 0.4583,
    "formula": "C5H8O2",
    "ion_formula": "C5H9O2+",
    "ionization_mechanism_id": "mech-h",
    "batch_peak_run_id": "run-1",
}


def _elected(formula="C5H8O2", mechanism="mech-h", fit=0.9, tier="assigned"):
    return _m0(
        formula,
        fit_score=fit,
        intensity=50.0,
        tier=tier,
        ion_formula=f"{formula}+",
        ionization_mechanism_id=mechanism,
    )


def _persistent(*members, **records):
    return compute_consensus(list(members), persistence=_ADMITTED, **records)


def test_the_persistence_rules_names_are_the_tiering_passes():
    from mascope_backend.api.new.peak_assignments import batch_peaks, tiering

    assert batch_peaks.REASON_PERSISTENCE_ONLY == tiering.REASON_PERSISTENCE_ONLY
    assert batch_peaks.REASON_SECOND_LINE == tiering.REASON_SECOND_LINE
    assert batch_peaks.REASON_CORROBORATED == tiering.REASON_CORROBORATED
    assert batch_peaks.REASON_SERIES_ANCHOR == tiering.REASON_SERIES_ANCHOR


def test_the_record_stays_whatever_the_anchor_reads():
    from mascope_backend.api.new.peak_assignments.batch_peaks import PERSISTENCE_KEY

    for members in ([_elected()], [_elected("C9H9N")], [_bare()]):
        c = compute_consensus(members, persistence=_ADMITTED)
        assert c.provenance[PERSISTENCE_KEY] == _ADMITTED
    assert PERSISTENCE_KEY not in compute_consensus([_elected()]).provenance


def test_an_uncorroborated_reading_elected_on_persistence_is_held_at_candidate():
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        PersistenceFacts,
        hold_persistence_only,
        tier_reasons_of,
    )

    c = _persistent(_elected(), _elected())
    assert c.consensus_tier == "assigned"
    assert hold_persistence_only(c, PersistenceFacts()) is True
    assert c.consensus_tier == "candidate"
    (reason,) = tier_reasons_of({"provenance": c.provenance})
    assert reason["rule"] == "persistence_only"
    assert reason["caps"] is True
    # The reason names the share the admission rested on, and the split.
    assert "11 of its partition's 12 spectra (92 %" in reason["detail"]
    assert "split at 46 %" in reason["detail"]


def test_the_fit_alone_never_lifts_it():
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        PersistenceFacts,
        hold_persistence_only,
    )

    c = _persistent(_elected(fit=0.999), _elected(fit=0.999))
    hold_persistence_only(c, PersistenceFacts())
    assert c.consensus_tier == "candidate"


@pytest.mark.parametrize(
    "facts, rule",
    [
        ({"line_samples": 1}, "second_line"),
        ({"channels": ("mech-na",)}, "corroborated"),
    ],
    ids=["a 13C line", "another channel"],
)
def test_a_second_observation_lifts_it(facts, rule):
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        PersistenceFacts,
        hold_persistence_only,
        tier_reasons_of,
    )

    c = _persistent(_elected(), _elected())
    assert hold_persistence_only(c, PersistenceFacts(**facts)) is False
    assert c.consensus_tier == "assigned"
    (reason,) = tier_reasons_of({"provenance": c.provenance})
    assert reason["rule"] == rule
    assert reason["caps"] is False


def test_a_series_anchoring_the_reading_lifts_it():
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        PersistenceFacts,
        hold_persistence_only,
        tier_reasons_of,
    )

    series = {
        "formula": "C5H8O2",
        "ionization_mechanism_id": "mech-h",
        "unit": "CH2",
        "has_anchor": True,
    }
    c = _persistent(_elected(), _elected(), series=series)
    hold_persistence_only(c, PersistenceFacts())
    assert c.consensus_tier == "assigned"
    assert [r["rule"] for r in tier_reasons_of({"provenance": c.provenance})] == [
        "series_anchor"
    ]
    # A series through another mechanism, or one with no assigned anchor,
    # vouches for nothing here.
    for record in (
        {**series, "ionization_mechanism_id": "mech-na"},
        {**series, "has_anchor": False},
    ):
        c = _persistent(_elected(), _elected(), series=record)
        hold_persistence_only(c, PersistenceFacts())
        assert c.consensus_tier == "candidate"


def test_every_second_observation_is_named():
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        PersistenceFacts,
        hold_persistence_only,
        tier_reasons_of,
    )

    c = _persistent(_elected(), _elected())
    hold_persistence_only(c, PersistenceFacts(line_samples=3, channels=("a", "b")))
    reasons = tier_reasons_of({"provenance": c.provenance})
    assert [r["rule"] for r in reasons] == ["second_line", "corroborated"]
    assert "in 3 samples" in reasons[0]["detail"]
    assert "2 other channels" in reasons[1]["detail"]


@pytest.mark.parametrize("tier", ["candidate", "below_assignability"])
def test_it_never_promotes_and_still_says_what_it_would_take(tier):
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        PersistenceFacts,
        hold_persistence_only,
        tier_reasons_of,
    )

    c = _persistent(_elected(tier=tier), _elected(tier=tier))
    assert c.consensus_tier == tier
    # The rule holds the reading; there is no top tier to take.
    assert hold_persistence_only(c, PersistenceFacts()) is True
    assert c.consensus_tier == tier
    assert tier_reasons_of({"provenance": c.provenance})[0]["caps"] is True


@pytest.mark.parametrize(
    "members, records",
    [
        # Another reading than the search elected: a later vote's, not the
        # admission's.
        ([_m0("C9H9N", ionization_mechanism_id="mech-h", tier="assigned")], {}),
        ([_m0("C5H8O2", ionization_mechanism_id="mech-na", tier="assigned")], {}),
        # A person's pin.
        (
            [_m0("C5H8O2", ionization_mechanism_id="mech-h", tier="assigned")],
            {"manual": {"formula": "C5H8O2", "ionization_mechanism_id": "mech-h"}},
        ),
        # Another row's line.
        (
            [
                _m0(
                    "C5H8O2",
                    ionization_mechanism_id="mech-h",
                    tier="assigned",
                    role="iso_child",
                    owner_batch_peak_id="bp-owner",
                )
            ]
            * 2,
            {},
        ),
    ],
    ids=["another formula", "another mechanism", "pinned", "a line"],
)
def test_a_reading_not_elected_on_persistence_alone_is_not_asked(members, records):
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        PersistenceFacts,
        hold_persistence_only,
        persistence_asks,
        tier_reasons_of,
    )

    c = _persistent(*members, **records)
    tier = c.consensus_tier
    assert persistence_asks(c) is None
    assert hold_persistence_only(c, PersistenceFacts()) is False
    assert c.consensus_tier == tier
    assert tier_reasons_of({"provenance": c.provenance}) is None


def test_an_admission_that_elected_nothing_asks_nothing():
    from mascope_backend.api.new.peak_assignments.batch_peaks import persistence_asks

    c = compute_consensus(
        [_elected(), _elected()],
        persistence={**_ADMITTED, "formula": None, "ion_formula": None},
    )
    assert persistence_asks(c) is None


def test_a_row_is_served_the_record_and_the_reasons_of_its_reading():
    from mascope_backend.api.new.peak_assignments.batch_peaks import row_persistence

    reasons = [{"rule": "persistence_only", "detail": "...", "caps": True}]
    anchor = {
        "provenance": {"persistence": _ADMITTED, "tier_reasons": reasons},
        "consensus_tier": "below_assignability",
    }
    reading = {
        "role": "M0",
        "assigned_formula": "C5H8O2",
        "ionization_mechanism_id": "mech-h",
    }
    # The batch peak's tier rides with its reasons: it says what they did to
    # the reading, which the row's own tier - its run's - need not.
    assert row_persistence(reading, anchor) == {
        **_ADMITTED,
        "tier_reasons": reasons,
        "consensus_tier": "below_assignability",
    }
    # Another reading of the same peak, or a line on it: the admission, not
    # the reasons, which are about the elected reading.
    for row in (
        {**reading, "assigned_formula": "C9H9N"},
        {**reading, "role": "iso_child"},
        {"role": "unassigned"},
    ):
        assert row_persistence(row, anchor) == {
            **_ADMITTED,
            "tier_reasons": None,
            "consensus_tier": None,
        }
    assert row_persistence(reading, {"provenance": {}}) is None
    assert row_persistence(reading, None) is None


def _member_of(sample, tier, fit=0.9, formula="C5H8O2", mechanism="mech-h", role="M0"):
    return {
        "sample_item_id": sample,
        "assigned_formula": formula,
        "ionization_mechanism_id": mechanism,
        "role": role,
        "tier": tier,
        "fit_score": fit,
    }


def test_the_rule_stores_the_elected_readings_assigned_members_at_candidate():
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        held_member_tiers,
        measured_members,
    )

    members = measured_members(
        [
            _member_of("s1", "assigned", 0.9),
            _member_of("s2", "candidate", 0.6),
            # Another reading of the peak, a line, and another mechanism.
            _member_of("s3", "assigned", 0.95, formula="C9H9N"),
            _member_of("s4", "assigned", 0.9, role="iso_child"),
            _member_of("s5", "assigned", 0.9, mechanism="mech-na"),
        ],
        _ADMITTED,
    )
    moves, held = held_member_tiers(members, _ADMITTED, holds=True)
    assert moves == {"s1": "candidate"}
    assert held == {"s1": 0.9}
    # Let go, nothing moves and nothing is held.
    assert held_member_tiers(members, _ADMITTED, holds=False) == ({}, {})


def test_a_held_member_votes_and_is_put_back_at_its_measured_tier():
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        held_member_tiers,
        measured_members,
    )

    record = {**_ADMITTED, "held_members": {"s1": 0.9, "s2": 0.8, "s4": 0.8}}
    stored = [
        _member_of("s1", "candidate", 0.9),
        # A fold replaced these since: another fit, or another tier.
        _member_of("s2", "candidate", 0.7),
        _member_of("s3", "assigned", 0.9),
        _member_of("s4", "below_assignability", 0.8),
    ]
    members = measured_members(stored, record)
    assert [m["tier"] for m in members] == [
        "assigned",
        "candidate",
        "assigned",
        "below_assignability",
    ]
    assert [m["stored_tier"] for m in members] == [m["tier"] for m in stored]
    # Corroborated: the held one goes back; the replaced ones are their own.
    assert held_member_tiers(members, record, holds=False) == ({"s1": "assigned"}, {})
    # Still held: s1 stays where it is stored, s3 joins it.
    assert held_member_tiers(members, record, holds=True) == (
        {"s3": "candidate"},
        {"s1": 0.9, "s3": 0.9},
    )
    # No record of held members: every member votes as stored.
    assert [m["tier"] for m in measured_members(stored, _ADMITTED)] == [
        m["tier"] for m in stored
    ]


def test_a_reader_is_not_served_the_held_members():
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        persistence_summary,
        row_persistence,
    )

    anchor = {"provenance": {"persistence": {**_ADMITTED, "held_members": {"s1": 0.9}}}}
    assert persistence_summary(anchor) == _ADMITTED
    assert row_persistence({"role": "M0"}, anchor) == {
        **_ADMITTED,
        "tier_reasons": None,
        "consensus_tier": None,
    }
    assert persistence_summary({"provenance": {}}) is None


@pytest.mark.parametrize(
    "over",
    [
        {"assigned_formula": "C9H9N"},
        {"ionization_mechanism_id": "mech-na"},
        {"role": "iso_child"},
    ],
    ids=["another formula", "another mechanism", "a line"],
)
def test_a_member_re_measured_at_the_held_fit_is_its_own(over):
    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        held_member_tiers,
        measured_members,
    )

    # A pin or a series re-pointed s1 at another reading, which it measured
    # at candidate at the very fit the rule held it at: it is not the member
    # the rule held, so it votes, and stays, at candidate.
    record = {**_ADMITTED, "held_members": {"s1": 0.9}}
    (member,) = measured_members(
        [{**_member_of("s1", "candidate", 0.9), **over}], record
    )
    assert member["tier"] == "candidate"
    assert held_member_tiers([member], record, holds=False) == ({}, {})


def test_a_pin_archives_a_held_member_at_its_measured_tier():
    from types import SimpleNamespace

    from mascope_backend.api.new.peak_assignments.batch_peaks import (
        measured_state,
        member_state,
        role_code,
        tier_code,
    )

    anchor = {
        "candidates": [
            {
                "formula": "C5H8O2",
                "ion_formula": "C5H9O2+",
                "ionization_mechanism_id": "mech-h",
            },
            {
                "formula": "C9H9N",
                "ion_formula": "C9H10N+",
                "ionization_mechanism_id": "mech-h",
            },
        ],
        "provenance": {
            "persistence": {**_ADMITTED, "held_members": {"s1": 0.9, "s2": 0.9}}
        },
    }

    def member(sample, candidate=0, tier="candidate", fit=0.9, role="M0"):
        return SimpleNamespace(
            sample_item_id=sample,
            candidate=candidate,
            tier=tier_code(tier),
            fit_score=fit,
            role=role_code(role),
            owner_batch_peak_id=None,
            p_correct=None,
        )

    held = member("s1")
    # Held: the measurement is archived, the rest as the member reads.
    assert measured_state(held, anchor) == {
        **member_state(held),
        "tier": tier_code("assigned"),
    }
    # Not held - another fit, another reading, a line, another tier, a sample
    # the record does not name, no record at all: archived as stored.
    for other, on in (
        (member("s2", fit=0.7), anchor),
        (member("s2", candidate=1), anchor),
        (member("s2", role="iso_child"), anchor),
        (member("s2", tier="below_assignability"), anchor),
        (member("s3"), anchor),
        (held, {**anchor, "provenance": {"persistence": _ADMITTED}}),
        (held, {**anchor, "provenance": {}}),
    ):
        assert measured_state(other, on) == member_state(other)
