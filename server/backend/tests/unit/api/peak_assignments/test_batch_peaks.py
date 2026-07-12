"""Unit tests for the pure batch-peak fold-in + consensus engine.

No DB / I/O -- exercises the append-only anchor invariant (existing batch peaks
never move when a sample arrives), the resolution-adaptive membership, and the
evidence-weighted consensus (confidence over assigned members, prevalence kept
separate, ties surfaced). See docs/dev/peak_assignment_batch.md.
"""

import itertools

import pytest

from mascope_backend.api.new.peak_assignments.batch_peaks import (
    AMBIGUOUS_SUPPORT,
    Anchor,
    AnchorSet,
    compute_consensus,
    fold_in_sample,
    resolution_adaptive_tol_ppm,
)


def _ids():
    counter = itertools.count()
    return lambda: f"bp{next(counter)}"


# --- tolerance ---------------------------------------------------------------


def test_resolution_adaptive_tol_is_half_fwhm_plus_margin():
    # FWHM_ppm = 1e6 / R = 10 ppm at R=1e5; half = 5 ppm; + margin 2 = 7.
    assert resolution_adaptive_tol_ppm(200.0, 100_000, drift_margin_ppm=2.0) == pytest.approx(7.0)
    # No resolution -> margin only.
    assert resolution_adaptive_tol_ppm(200.0, None, drift_margin_ppm=2.0) == pytest.approx(2.0)


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
    folded = fold_in_sample(aset, [{"mz": 100.0}, {"mz": 200.0}], new_id=_ids(), tol_fn=tol)
    assert len(aset) == 2
    assert all(f.is_new_anchor for f in folded)

    # A second sample: one peak within tolerance of 100, one brand new at 300.
    ids2 = _ids()
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
    fold_in_sample(a1, [{"mz": 100.0}, {"mz": 150.0}, {"mz": 200.0}], new_id=_ids(), tol_fn=tol)
    a2 = AnchorSet()
    fold_in_sample(a2, [{"mz": 200.0}, {"mz": 100.0}, {"mz": 150.0}], new_id=_ids(), tol_fn=tol)
    assert [round(a.mz, 6) for a in a1.anchors()] == [round(a.mz, 6) for a in a2.anchors()]


def test_same_sample_collision_keeps_one_member_nearest_wins():
    aset = AnchorSet([Anchor("a", 100.0, 10.0)])
    # Two peaks in one sample both within tolerance of anchor a; nearest wins.
    folded = fold_in_sample(
        aset, [{"mz": 100.0008, "tag": "far"}, {"mz": 100.0002, "tag": "near"}],
        new_id=lambda: "NEW", tol_fn=lambda mz: 10.0,
    )
    assert len(folded) == 1
    assert folded[0].batch_peak_id == "a"
    assert folded[0].peak["tag"] == "near"
    assert len(aset) == 1  # no spurious new anchor


# --- consensus ---------------------------------------------------------------


def test_consensus_evidence_weighted_winner_beats_low_fit_flips():
    members = [
        {"assigned_formula": "C6H12O6", "ion_formula": "C6H11O6-", "ionization_mechanism_id": "mH",
         "tier": "identified", "fit_score": 0.95, "intensity": 1e5, "p_correct": 0.9},
        {"assigned_formula": "C6H12O6", "ion_formula": "C6H11O6-", "ionization_mechanism_id": "mH",
         "tier": "identified", "fit_score": 0.90, "intensity": 9e4, "p_correct": 0.88},
        {"assigned_formula": "C5H8O", "ion_formula": "C5H7O-", "ionization_mechanism_id": "mH",
         "tier": "candidate", "fit_score": 0.50, "intensity": 1e3, "p_correct": None},
    ]
    c = compute_consensus(members)
    assert c.consensus_formula == "C6H12O6"
    assert c.consensus_ion_formula == "C6H11O6-"
    assert c.consensus_tier == "identified"
    assert c.n_present == 3
    assert c.support_fraction == pytest.approx(2 / 3, rel=1e-3)
    assert c.best_fit_score == pytest.approx(0.95)
    assert not c.is_ambiguous
    assert c.provenance["p_correct"] == pytest.approx(0.9)


def test_consensus_prevalence_separate_from_confidence():
    # Assigned in 3 samples, present-but-unassigned in 2 more.
    members = [
        {"assigned_formula": "A", "tier": "identified", "fit_score": 0.9, "intensity": 1e4}
        for _ in range(3)
    ] + [{"assigned_formula": None, "tier": "unassigned", "fit_score": None, "intensity": 5e2}
         for _ in range(2)]
    c = compute_consensus(members)
    assert c.consensus_formula == "A"
    assert c.n_present == 5              # prevalence counts all detected members
    assert c.support_fraction == pytest.approx(1.0)  # agreement among ASSIGNED only
    assert c.consensus_tier == "identified"


def test_consensus_tie_is_flagged_ambiguous_with_alternatives():
    members = [
        {"assigned_formula": "A", "tier": "candidate", "fit_score": 0.6, "intensity": 1e3},
        {"assigned_formula": "B", "tier": "candidate", "fit_score": 0.6, "intensity": 1e3},
    ]
    c = compute_consensus(members)
    assert c.is_ambiguous
    assert {alt["formula"] for alt in c.alternatives} == {"B"} or {"A"}
    assert c.support_fraction == pytest.approx(0.5)


def test_consensus_all_unassigned_is_a_valid_drawable_peak():
    members = [
        {"assigned_formula": None, "tier": "unassigned", "fit_score": None, "intensity": 1e3},
        {"assigned_formula": None, "tier": "unassigned", "fit_score": None, "intensity": 2e3},
    ]
    c = compute_consensus(members)
    assert c.consensus_formula is None
    assert c.consensus_tier == "unassigned"
    assert c.n_present == 2  # still a trace, just unlabelled


def test_consensus_tier_downgrades_when_members_are_candidates():
    members = [
        {"assigned_formula": "A", "tier": "candidate", "fit_score": 0.7, "intensity": 1e4},
        {"assigned_formula": "A", "tier": "candidate", "fit_score": 0.7, "intensity": 1e4},
        {"assigned_formula": "A", "tier": "identified", "fit_score": 0.6, "intensity": 1e2},
    ]
    c = compute_consensus(members)
    assert c.consensus_formula == "A"
    # Weighted majority are candidate (the identified member is weak) -> candidate.
    assert c.consensus_tier == "candidate"
