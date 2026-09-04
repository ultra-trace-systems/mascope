"""The batch import's matcher and its outcome, offline: nearest within
tolerance, polarity lanes, unmeasurable rows, contested anchors, and what the
summary says."""

from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments.batch_import import (
    REASON_COLLISION,
    REASON_NO_ANCHOR,
    REASON_NO_MECHANISM,
    AnchorRef,
    import_outcome,
    match_rows_to_anchors,
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


def test_an_empty_ledger_matches_nothing():
    matched, unmatched = match_rows_to_anchors([row(181.0707)], [], 5.0, POLARITY)
    assert matched == {}
    assert unmatched == {0: REASON_NO_ANCHOR}


def test_the_outcome_says_what_landed_and_why_the_rest_did_not():
    counts = {
        "rows": 3,
        "anchors_matched": 2,
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
    assert outcome["data"] is counts

    nothing = import_outcome({**counts, "anchors_matched": 0}, "sb-1", "peaky")
    assert nothing["status"] == "partial"
    assert "No row of the peaky import landed" in nothing["message"]
