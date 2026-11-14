"""The probable-blend test of a batch peak's consensus (step 4.8, MAS-320).

Synthetic batches: two formulas separated by intensity are flagged; separated
by acquisition time are flagged; interleaved on both are not; a single formula
never is. Around those, the bars that keep a large batch's slight shift from
reading as a blend, and the members the time test leaves out.
"""

import itertools
import json
import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from mascope_backend.api.new.peak_assignments import batch_blend
from mascope_backend.api.new.peak_assignments.batch_blend import (
    AXIS_INTENSITY,
    AXIS_TIME,
    BLEND_KEY,
    blend_of,
    blend_record_of,
    intensity_reading,
    runs_p_value,
    time_reading,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    compute_consensus,
    vote_consensus,
)


T0 = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
A, B = "C6H12O6", "C7H16O5"


def _member(formula, i, intensity=1000.0, *, acquired=True, t0=None, fit=0.9):
    return {
        "assigned_formula": formula,
        "ion_formula": f"{formula}H+",
        "tier": "assigned",
        "fit_score": fit,
        "intensity": intensity,
        "sample_item_id": f"s{i:04d}",
        "acquired": T0 + timedelta(minutes=i) if acquired else None,
        "t0": t0,
    }


def _batch(labels, intensities):
    """Members in acquisition order: ``labels[i]`` names the formula of the
    member acquired i-th, at ``intensities[i]``."""
    return [
        _member(formula, i, intensity)
        for i, (formula, intensity) in enumerate(zip(labels, intensities))
    ]


def _interleaved_intensities(n):
    # Every other member bright: neither formula of an alternating batch is
    # the brighter, nor of a two-block one.
    return [1000.0 + 37.0 * ((i * 7) % n) for i in range(n)]


# --- the four cases of the brief ------------------------------------------------


def test_two_formulas_separated_by_intensity_are_flagged():
    # Alternating in time, so only the intensity separates them.
    labels = [A, B] * 8
    intensities = [
        5000.0 + 100 * i if f == A else 200.0 + 10 * i for i, f in enumerate(labels)
    ]
    consensus = vote_consensus(_batch(labels, intensities))
    blend = consensus.provenance[BLEND_KEY]
    assert blend["axis"] == AXIS_INTENSITY
    assert blend["formulas"] == [
        consensus.consensus_formula,
        B if consensus.consensus_formula == A else A,
    ]
    assert blend["intensity"]["brighter"] == A
    assert blend["intensity"]["separates"] is True
    assert blend["time"]["separates"] is False
    assert blend["n"] == [8, 8]


def test_two_formulas_separated_by_time_are_flagged():
    labels = [A] * 8 + [B] * 8
    consensus = vote_consensus(_batch(labels, _interleaved_intensities(16)))
    blend = consensus.provenance[BLEND_KEY]
    assert blend["axis"] == AXIS_TIME
    assert blend["time"]["runs"] == 2
    assert blend["time"]["earlier"] == A
    assert blend["intensity"]["separates"] is False


def test_the_later_formula_is_named_as_such():
    labels = [B] * 8 + [A] * 8
    blend = vote_consensus(_batch(labels, _interleaved_intensities(16))).provenance[
        BLEND_KEY
    ]
    assert blend["time"]["earlier"] == B


def test_interleaved_formulas_are_not_flagged():
    labels = [A, B] * 8
    consensus = vote_consensus(_batch(labels, _interleaved_intensities(16)))
    assert consensus.is_ambiguous  # they do disagree...
    assert BLEND_KEY not in consensus.provenance  # ...but not along an axis


def test_a_shuffled_batch_is_not_flagged():
    rng = random.Random(7)
    labels = [A] * 20 + [B] * 20
    rng.shuffle(labels)
    intensities = [rng.uniform(100.0, 10_000.0) for _ in labels]
    assert BLEND_KEY not in vote_consensus(_batch(labels, intensities)).provenance


def test_a_single_formula_is_never_flagged():
    # Bright early, dim late: as separated as anything gets, and one formula.
    members = _batch([A] * 16, [10_000.0 - 500 * i for i in range(16)])
    consensus = vote_consensus(members)
    assert BLEND_KEY not in consensus.provenance
    assert compute_consensus(members).provenance.get(BLEND_KEY) is None


# --- the bars ---------------------------------------------------------------------


def test_fewer_than_the_floor_on_either_side_is_not_tested():
    labels = [A] * 20 + [B] * 2
    assert time_reading(*_split(_batch(labels, [1.0] * 22))) is None
    assert intensity_reading(*_split(_batch(labels, [1.0] * 22))) is None
    assert BLEND_KEY not in vote_consensus(_batch(labels, [1.0] * 22)).provenance


def test_three_a_side_is_tested_but_cannot_reach_significance():
    labels = [A] * 3 + [B] * 3
    reading = time_reading(*_split(_batch(labels, [1.0] * 6)))
    assert reading is not None and reading["runs"] == 2
    assert reading["separates"] is False  # p = 2/20


def test_a_clean_intensity_parting_too_small_to_be_significant_is_not_a_blend():
    # Three a side, every member of one brighter than every member of the
    # other: the cleanest parting there is, and p = 0.1.
    first = [_member(A, i, 1000.0 + i) for i in range(3)]
    second = [_member(B, 10 + i, 10.0 + i) for i in range(3)]
    reading = intensity_reading(first, second)
    assert reading["separation"] == 1.0
    assert reading["p"] > batch_blend.BLEND_ALPHA
    assert reading["separates"] is False


def test_a_winner_on_the_dim_side_parts_as_cleanly():
    dim = [_member(A, i, 10.0 + i) for i in range(8)]
    bright = [_member(B, 10 + i, 1000.0 + i) for i in range(8)]
    reading = intensity_reading(dim, bright)
    assert reading["separation"] == 0.0
    assert reading["separates"] is True
    assert blend_of(A, dim, B, bright)["intensity"]["brighter"] == B


def test_a_slight_intensity_shift_in_a_large_batch_is_not_a_blend():
    # Significant (thousands of members), but the two populations overlap.
    rng = random.Random(3)
    first = [_member(A, i, rng.gauss(1.15, 0.5)) for i in range(2000)]
    second = [_member(B, i, rng.gauss(1.0, 0.5)) for i in range(2000)]
    reading = intensity_reading(first, second)
    assert reading["p"] < batch_blend.BLEND_ALPHA
    assert reading["separation"] < batch_blend.BLEND_MIN_SEPARATION
    assert reading["separates"] is False


def test_a_slight_clustering_in_time_in_a_large_batch_is_not_a_blend():
    # Blocks of three: two thirds of the runs an interleaving gives -
    # significant over 400 members, and not a few blocks.
    labels = ([A] * 3 + [B] * 3) * 67
    reading = time_reading(*_split(_batch(labels, [1.0] * len(labels))))
    assert reading["p"] < batch_blend.BLEND_ALPHA
    assert reading["runs"] > batch_blend.BLEND_MAX_RUNS_SHARE * reading["expected_runs"]
    assert reading["separates"] is False


def test_both_axes_separating_name_the_more_significant():
    # Early and bright vs late and dim: both separate, intensity the more
    # strongly (its p falls faster with the counts than two runs' does here).
    labels = [A] * 10 + [B] * 10
    intensities = [5000.0 + i for i in range(10)] + [100.0 + i for i in range(10)]
    first, second = _split(_batch(labels, intensities))
    blend = blend_of(A, first, B, second)
    assert blend["intensity"]["separates"] and blend["time"]["separates"]
    expected = min(
        (blend["intensity"]["p"], AXIS_INTENSITY), (blend["time"]["p"], AXIS_TIME)
    )[1]
    assert blend["axis"] == expected


# --- what is not two chemistries -----------------------------------------------------


def test_two_formulas_reading_one_ion_are_not_a_blend():
    # One ion through two mechanisms, switched in time: one ion on the trace.
    labels = [A] * 8 + [B] * 8
    members = _batch(labels, _interleaved_intensities(16))
    for m in members:
        m["ion_formula"] = "C7H12NO+"
    assert BLEND_KEY not in vote_consensus(members).provenance
    # The same members naming two ions are one.
    for m in members[8:]:
        m["ion_formula"] = "C7H13NO+"
    ions = vote_consensus(members).provenance[BLEND_KEY]["ions"]
    assert set(ions) == {"C7H12NO+", "C7H13NO+"}


def test_the_ion_compared_is_the_one_most_of_a_side_reads():
    labels = [A] * 8 + [B] * 8
    members = _batch(labels, _interleaved_intensities(16))
    for m in members:
        m["ion_formula"] = "X+"
    # One of A's members reads another ion: A still reads X+ mostly.
    members[0]["ion_formula"] = "Y+"
    assert BLEND_KEY not in vote_consensus(members).provenance


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize(
    "tier,flagged",
    [("below_assignability", False), ("candidate", True), ("assigned", True)],
)
def test_a_side_counts_only_its_members_at_candidate_or_better(side, tier, flagged):
    labels = [A] * 8 + [B] * 8
    members = _batch(labels, _interleaved_intensities(16))
    for m in members[8 * side : 8 * side + 8]:
        m["tier"] = tier
    first, second = _split(members)
    assert (blend_of(A, first, B, second) is not None) is flagged


def test_the_sides_are_counted_after_the_tier_filter():
    labels = [A] * 8 + [B] * 8
    members = _batch(labels, _interleaved_intensities(16))
    for m in members[8:14]:
        m["tier"] = "below_assignability"
    first, second = _split(members)
    # Two of B's members commit to it: under the floor, so nothing is tested.
    assert blend_of(A, first, B, second) is None
    for m in members[8:11]:
        m["tier"] = "candidate"
    blend = blend_of(A, first, B, second)
    assert blend is not None and blend["n"] == [8, 5]


# --- the time axis ----------------------------------------------------------------


def test_a_member_with_no_acquisition_time_is_left_out_of_the_time_test():
    labels = [A] * 6 + [B] * 6
    members = _batch(labels, _interleaved_intensities(12))
    # Undated, and placed where it would break the second block in two.
    members.append(_member(A, 9, acquired=False))
    reading = time_reading(*_split(members))
    assert reading["runs"] == 2
    assert reading["separates"] is True


def test_the_segments_of_one_file_are_ordered_by_where_they_start():
    # Twelve segments sharing one acquisition time: t0 orders them, and the
    # sample ids, which interleave the two formulas, must not.
    members = []
    for i in range(12):
        formula = A if i < 6 else B
        m = _member(formula, 0, t0=float(i))
        m["sample_item_id"] = f"s{(i * 7) % 12:04d}"
        members.append(m)
    reading = time_reading(*_split(members))
    assert reading["runs"] == 2


def test_samples_sharing_a_time_and_a_start_are_ordered_by_id():
    members = []
    for i in range(12):
        m = _member(A if i < 6 else B, 0)
        m["sample_item_id"] = f"s{i:04d}"
        members.append(m)
    # Handed over alternating A, B, A, B; the ids put them in two blocks.
    members = [x for pair in zip(members[:6], members[6:]) for x in pair]
    assert [m["assigned_formula"] for m in members[:4]] == [A, B, A, B]
    assert time_reading(*_split(members))["runs"] == 2


def test_acquisition_order_not_member_order_decides():
    labels = [A] * 8 + [B] * 8
    members = _batch(labels, _interleaved_intensities(16))
    random.Random(11).shuffle(members)
    assert time_reading(*_split(members))["runs"] == 2


@pytest.mark.parametrize("n1,n2", [(1, 1), (2, 3), (3, 3), (4, 2), (5, 4)])
def test_the_runs_distribution_is_exact(n1, n2):
    arrangements = set(itertools.permutations([0] * n1 + [1] * n2))
    total = len(arrangements)
    for r in range(1, n1 + n2 + 1):
        fewer = sum(
            1
            for seq in arrangements
            if 1 + sum(1 for x, y in zip(seq, seq[1:]) if x != y) <= r
        )
        assert runs_p_value(n1, n2, r) == pytest.approx(fewer / total)


def test_the_runs_p_value_holds_for_a_large_batch():
    # C(1200, 600) overflows a float; the count must not.
    assert 0.0 <= runs_p_value(600, 600, 2) < 1e-300
    assert runs_p_value(600, 600, 1200) == pytest.approx(1.0)


# --- which two formulas, and the record ---------------------------------------------


def test_the_vote_winner_and_its_runner_up_are_tested():
    # A third, minor formula rides along without entering the test.
    labels = [A] * 8 + [B] * 8
    members = _batch(labels, _interleaved_intensities(16))
    members += [_member("C5H10O5", 100 + i) for i in range(3)]
    consensus = vote_consensus(members)
    assert set(consensus.provenance[BLEND_KEY]["formulas"]) == {A, B}
    assert consensus.provenance[BLEND_KEY]["formulas"][0] == consensus.consensus_formula


def test_a_pin_is_tested_against_the_vote_leader():
    labels = [A] * 8 + [B] * 8 + ["C5H10O5"] * 3
    members = _batch(labels, _interleaved_intensities(19))
    # Weight A over B, then pin B: the test reads B (the pin) against A.
    for m in members[:8]:
        m["fit_score"] = 1.0
    for m in members[8:16]:
        m["fit_score"] = 0.5
    consensus = vote_consensus(members, manual={"formula": B})
    assert consensus.provenance[BLEND_KEY]["formulas"] == [B, A]


def test_an_anchor_read_by_its_source_role_is_not_tested():
    claimed = [
        {"role": "reagent", "ion_formula": "Br-", "intensity": 1.0, "acquired": T0}
        for _ in range(20)
    ]
    labels = [A] * 4 + [B] * 4
    consensus = vote_consensus(claimed + _batch(labels, [1.0] * 8))
    assert consensus.consensus_role == "reagent"
    assert BLEND_KEY not in consensus.provenance


def test_the_record_is_read_off_the_anchors_provenance():
    record = {"formulas": [A, B], "axis": AXIS_TIME}
    assert blend_record_of({"provenance": {BLEND_KEY: record}}) == record
    assert blend_record_of({"provenance": {}}) is None
    assert blend_record_of({"provenance": None}) is None
    assert blend_record_of({"provenance": {BLEND_KEY: "garbage"}}) is None


def test_a_recompute_over_the_same_members_writes_the_same_record():
    labels = [A] * 8 + [B] * 8
    members = _batch(labels, _interleaved_intensities(16))
    assert vote_consensus(members).provenance == vote_consensus(members).provenance


def test_the_record_keeps_a_p_value_to_three_significant_digits():
    # 2 / C(16, 8) = 0.000155400...: a float with digits to spare.
    blend = vote_consensus(
        _batch([A] * 8 + [B] * 8, _interleaved_intensities(16))
    ).provenance[BLEND_KEY]
    assert blend["time"]["p"] == 0.000155
    assert runs_p_value(8, 8, 2) == pytest.approx(2 / 12870)


def test_all_tied_intensities_leave_the_axis_untested():
    # The rank test answers NaN where every intensity ties; the record is
    # JSON in Postgres, which refuses NaN.
    tied = _batch([A] * 5 + [B] * 5, [1.0] * 10)
    assert intensity_reading(*_split(tied)) is None


def test_a_time_blend_with_tied_intensities_writes_a_strict_json_record():
    labels = [A] * 30 + [B] * 5
    blend = vote_consensus(_batch(labels, [1.0] * 35)).provenance[BLEND_KEY]
    assert blend["axis"] == AXIS_TIME
    assert blend["intensity"] is None
    json.dumps(blend, allow_nan=False)


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_a_member_with_a_non_finite_intensity_is_left_out_of_the_rank_test(bad):
    labels = [A, B] * 8
    intensities = [5000.0 + 100 * i if f == A else 200.0 for i, f in enumerate(labels)]
    members = _batch(labels, intensities)
    members[0]["intensity"] = bad
    members[1]["intensity"] = bad
    reading = intensity_reading(*_split(members))
    assert reading["separates"] is True
    assert reading["separation"] == 1.0
    json.dumps(reading, allow_nan=False)


def _split(members):
    return (
        [m for m in members if m["assigned_formula"] == A],
        [m for m in members if m["assigned_formula"] == B],
    )
