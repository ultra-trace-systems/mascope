"""The batch import's matcher and its outcome, offline: nearest within
tolerance, polarity lanes, instrument partitions, unmeasurable rows, contested
anchors, and what the summary says."""

from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments.batch_import import (
    REASON_COLLISION,
    REASON_NO_ANCHOR,
    REASON_NO_MECHANISM,
    AnchorRef,
    EngineVerdict,
    import_outcome,
    match_rows_to_anchors,
    verdict_on_anchor,
)


def row(mz, mechanism="mech-h"):
    return SimpleNamespace(
        mz=mz, ionization_mechanism_id=mechanism, formula="C6H12O6", ion_formula=None
    )


# A positive M0 with its M+1, the same m/z measured in negative mode, and an
# anchor with no ionization mode at all.
ANCHORS = [
    AnchorRef("a181", 181.0707, "+"),
    AnchorRef("a182", 182.0741, "+"),
    AnchorRef("n181", 181.0710, "-"),
    AnchorRef("free", 250.1, None),
]
POLARITY = {"mech-h": "+", "mech-neg": "-"}


def test_lands_on_the_nearest_anchor_within_tolerance():
    matched, unmatched = match_rows_to_anchors([row(181.0709)], ANCHORS, 5.0, POLARITY)
    assert matched == {"a181": 0}
    assert unmatched == {}


def test_refuses_a_row_outside_the_tolerance():
    # 4.3 mDa at 181 is 24 ppm: nowhere near under a 5 ppm window.
    matched, unmatched = match_rows_to_anchors([row(181.0750)], ANCHORS, 5.0, POLARITY)
    assert matched == {}
    assert unmatched == {0: REASON_NO_ANCHOR}


def test_keeps_to_the_polarity_the_mechanism_implies():
    matched, _ = match_rows_to_anchors(
        [row(181.0710, "mech-neg")], ANCHORS, 5.0, POLARITY
    )
    assert matched == {"n181": 0}


def test_an_anchor_without_a_mode_takes_a_row_of_either_polarity():
    matched, unmatched = match_rows_to_anchors(
        [row(250.1, "mech-neg"), row(250.1001)], ANCHORS, 5.0, POLARITY
    )
    assert matched == {"free": 0}
    assert unmatched == {1: REASON_COLLISION}


def test_a_row_without_a_mechanism_is_not_matched_because_it_cannot_be_measured():
    matched, unmatched = match_rows_to_anchors(
        [row(181.0707, None)], ANCHORS, 5.0, POLARITY
    )
    assert matched == {}
    assert unmatched == {0: REASON_NO_MECHANISM}


def test_the_closer_of_two_rows_takes_a_contested_anchor_whichever_comes_first():
    matched, unmatched = match_rows_to_anchors(
        [row(181.0712), row(181.0707)], ANCHORS, 5.0, POLARITY
    )
    assert matched == {"a181": 1}
    assert unmatched == {0: REASON_COLLISION}

    matched, unmatched = match_rows_to_anchors(
        [row(181.0707), row(181.0712)], ANCHORS, 5.0, POLARITY
    )
    assert matched == {"a181": 0}
    assert unmatched == {1: REASON_COLLISION}


def test_of_two_rows_equally_near_an_anchor_the_earlier_takes_it():
    rows = [row(181.0708), row(181.0708)]
    matched, unmatched = match_rows_to_anchors(rows, ANCHORS[:1], 5.0, POLARITY)
    assert matched == {"a181": 0}
    assert unmatched == {1: REASON_COLLISION}


def test_an_empty_ledger_matches_nothing():
    matched, unmatched = match_rows_to_anchors([row(181.0707)], [], 5.0, POLARITY)
    assert matched == {}
    assert unmatched == {0: REASON_NO_ANCHOR}


def test_the_outcome_says_what_landed_and_why_the_rest_did_not():
    counts = {
        "rows": 3,
        "anchors_matched": 2,
        "rows_landed": 2,
        "anchors_matched_by_instrument_type": {"orbi": 2},
        "members_measured": 5,
        "samples_rescored": 2,
        "rows_skipped_by_reason": {"no_anchor_within_tolerance": 1},
        "mz_tolerance_ppm": 5.0,
    }
    outcome = import_outcome(counts, "sb-1", "peaky")
    assert outcome["status"] == "success"
    assert "2 of 3 rows landed on a batch peak" in outcome["message"]
    assert "5 member peaks across 2 samples" in outcome["message"]
    assert "Skipped: 1 no anchor within tolerance." in outcome["message"]
    # One instrument type: nothing to break down.
    assert "per instrument type" not in outcome["message"]
    assert outcome["data"] is counts

    nothing = import_outcome({**counts, "anchors_matched": 0}, "sb-1", "peaky")
    assert nothing["status"] == "partial"
    assert "No row of the peaky import landed" in nothing["message"]


def test_the_outcome_names_the_samples_it_could_not_measure():
    """A sample that raised is reported, not left to the log: its members carry
    none of the imported identities, which the counts alone would not say."""
    counts = {
        "rows": 3,
        "anchors_matched": 2,
        "rows_landed": 2,
        "members_measured": 5,
        "samples_rescored": 1,
        "samples_failed": 1,
        "rows_skipped_by_reason": {},
        "mz_tolerance_ppm": 5.0,
    }
    outcome = import_outcome(counts, "sb-1", "peaky")
    assert outcome["status"] == "partial"
    assert outcome["message"].endswith(
        "1 sample could not be measured and was skipped."
    )

    two = import_outcome({**counts, "samples_failed": 2}, "sb-1", "peaky")
    assert two["status"] == "partial"
    assert two["message"].endswith("2 samples could not be measured and were skipped.")


# A mixed batch: glucose has an anchor in each instrument partition, 0.4 ppm
# apart, and the TOF partition alone has a peak at 300.
MIXED = [
    AnchorRef("o181", 181.0707, "+", "orbi"),
    AnchorRef("t181", 181.07077, "+", "tof"),
    AnchorRef("t300", 300.2, "+", "tof"),
]


def test_a_row_lands_on_the_nearest_anchor_of_each_partition():
    matched, unmatched = match_rows_to_anchors([row(181.0709)], MIXED, 5.0, POLARITY)
    assert matched == {"o181": 0, "t181": 0}
    assert unmatched == {}


def test_a_closer_row_takes_one_partitions_anchor_and_the_other_still_lands():
    # Row 1 sits on the Orbitrap anchor; row 0 is nearer the TOF one. Each
    # anchor goes to its closer row, so both rows land.
    rows = [row(181.07080), row(181.07070)]
    matched, unmatched = match_rows_to_anchors(rows, MIXED, 5.0, POLARITY)
    assert matched == {"o181": 1, "t181": 0}
    assert unmatched == {}


def test_a_row_that_loses_one_partitions_anchor_still_lands_in_the_other():
    # The TOF anchor sits 2.8 ppm above the Orbitrap one, and both rows are
    # within the tolerance of both. Row 0 is the closer to the Orbitrap
    # anchor and takes it; row 1 loses it but is the closer to the TOF anchor.
    anchors = [
        AnchorRef("o181", 181.0707, "+", "orbi"),
        AnchorRef("t181", 181.0712, "+", "tof"),
    ]
    rows = [row(181.0707), row(181.0708)]
    matched, unmatched = match_rows_to_anchors(rows, anchors, 5.0, POLARITY)
    assert matched == {"o181": 0, "t181": 1}
    assert unmatched == {}

    # With the TOF anchor out of both rows' tolerance, the loser lands nowhere.
    matched, unmatched = match_rows_to_anchors(rows, anchors, 2.0, POLARITY)
    assert matched == {"o181": 0}
    assert unmatched == {1: REASON_COLLISION}


def test_a_row_within_the_tolerance_of_one_partition_only_lands_once():
    matched, unmatched = match_rows_to_anchors([row(300.2001)], MIXED, 5.0, POLARITY)
    assert matched == {"t300": 0}
    assert unmatched == {}


def test_an_anchor_with_no_instrument_type_is_a_partition_of_its_own():
    anchors = [
        AnchorRef("u181", 181.0707, "+", None),
        AnchorRef("o181", 181.0708, "+", "orbi"),
    ]
    matched, _ = match_rows_to_anchors([row(181.0707)], anchors, 5.0, POLARITY)
    assert matched == {"u181": 0, "o181": 0}


def test_a_modeless_anchor_still_takes_either_polarity_within_its_partition():
    anchors = [
        AnchorRef("o181", 181.0707, None, "orbi"),
        AnchorRef("t181", 181.0707, "-", "tof"),
    ]
    matched, unmatched = match_rows_to_anchors(
        [row(181.0707, "mech-neg")], anchors, 5.0, POLARITY
    )
    assert matched == {"o181": 0, "t181": 0}
    # A positive row: the TOF anchor is negative, the Orbitrap one takes it.
    matched, _ = match_rows_to_anchors([row(181.0707)], anchors, 5.0, POLARITY)
    assert matched == {"o181": 0}


def test_the_outcome_of_a_mixed_batch_says_what_each_instrument_type_took():
    counts = {
        "rows": 2,
        "anchors_matched": 3,
        "rows_landed": 2,
        "anchors_matched_by_instrument_type": {"orbi": 1, "tof": 2},
        "members_measured": 3,
        "samples_rescored": 3,
        "rows_skipped_by_reason": {},
        "mz_tolerance_ppm": 5.0,
    }
    outcome = import_outcome(counts, "sb-1", "peaky")
    assert "2 of 2 rows landed on a batch peak" in outcome["message"]
    assert "Batch peaks per instrument type: orbi 1, tof 2." in outcome["message"]


VERDICT = EngineVerdict(
    formula="C6H12O6",
    ionization_mechanism_id="mech-h",
    engine_tier="candidate",
    engine_claim="level 3",
    engine_level="3c",
    engine_evidence="a context list names it",
    engine_lift="MS/MS",
)


def recomputed(formula="C6H12O6", mechanism="mech-h"):
    return SimpleNamespace(consensus_formula=formula, ionization_mechanism_id=mechanism)


def test_the_verdict_goes_on_an_anchor_whose_consensus_names_its_identity():
    assert verdict_on_anchor(recomputed(), VERDICT) == (
        "candidate",
        "level 3",
        "3c",
        "a context list names it",
        "MS/MS",
    )


def test_no_verdict_beside_a_formula_the_engine_did_not_give_it_for():
    # The measurement did not carry the members over: the consensus is another
    # formula, or none.
    assert verdict_on_anchor(recomputed(formula="C7H14O7"), VERDICT) == (None,) * 5
    assert verdict_on_anchor(recomputed(formula=None), VERDICT) == (None,) * 5


def test_no_verdict_beside_the_formula_through_another_channel():
    assert verdict_on_anchor(recomputed(mechanism="mech-na"), VERDICT) == (None,) * 5


def test_an_anchor_no_verdict_was_sent_for_carries_none():
    assert verdict_on_anchor(recomputed(), None) == (None,) * 5
