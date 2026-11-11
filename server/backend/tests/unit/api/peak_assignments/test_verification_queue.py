"""The verification queue's ranking (MAS-329, guardrail 3 of the verification loop).

Pure: rows, bands, a label pool and the live verdicts in, entries out. The
service that gathers them is covered by
``integration/api/peak_assignments/test_verification_queue_api.py``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from mascope_backend.api.new.peak_assignments.verification_queue import (
    EDGE_WINDOW,
    MIN_SPOT_CHECKS,
    REASON_BAND_EDGE,
    REASON_SPOT_CHECK,
    REASON_STALE,
    SPOT_CHECK_EVERY,
    STALE_REASSIGNED,
    STALE_RESCORED,
    edge_priority,
    labels_near,
    nearest_edge,
    queueable,
    rank_queue,
)


BANDS = {"assigned": 0.75, "candidate": 0.45}
RUN = "run-now"
RUN_CREATED = datetime(2026, 11, 1, tzinfo=timezone.utc)
BEFORE = RUN_CREATED - timedelta(days=1)
AFTER = RUN_CREATED + timedelta(hours=1)


def _row(peak, evidence, formula="C6H12O6", mech="m1", role="M0"):
    return {
        "peak_assignment_id": f"pa-{peak}",
        "sample_peak_id": peak,
        "assigned_formula": formula,
        "ionization_mechanism_id": mech,
        "role": role,
        "evidence": evidence,
    }


def _verdict(peak, *, formula="C6H12O6", mech="m1", evidence=None, utc=BEFORE):
    return {
        "assignment_verification_id": f"v-{peak}-{formula}",
        "sample_peak_id": peak,
        "assigned_formula": formula,
        "ionization_mechanism_id": mech,
        "verdict": "confirmed",
        "evidence": evidence,
        "verified_utc": utc,
    }


def _rank(rows, *, labels=(), verdicts=(), bands=BANDS, seed=RUN, **kwargs):
    return rank_queue(
        rows,
        bands=bands,
        label_evidence=labels,
        verdicts=verdicts,
        run_created=RUN_CREATED,
        seed=seed,
        **kwargs,
    )


# --- the pieces ---------------------------------------------------------------


def test_only_an_analytes_formula_with_evidence_is_queueable():
    assert queueable(_row("p", 0.5))
    for role in ("iso_child", "reagent", "artifact", "unassigned"):
        assert not queueable(_row("p", 0.5, role=role))
    assert not queueable(_row("p", 0.5, formula=None))
    assert not queueable(_row("p", None))
    assert not queueable(_row("p", float("nan")))


def test_nearest_edge_names_the_band_and_the_distance():
    assert nearest_edge(0.47, BANDS) == ("candidate", pytest.approx(0.02))
    assert nearest_edge(0.70, BANDS) == ("assigned", pytest.approx(0.05))
    # halfway between (in binary exactly): the higher band
    assert nearest_edge(0.625, {"candidate": 0.5, "assigned": 0.75})[0] == "assigned"
    assert nearest_edge(0.5, None) is None
    assert nearest_edge(0.5, {"assigned": None}) is None


def test_labels_near_counts_the_window_both_sides():
    pool = sorted([0.40, 0.44, 0.46, 0.50, 0.90])
    assert labels_near(0.45, pool, 0.05) == 4
    assert labels_near(0.45, pool, 0.01) == 2
    assert labels_near(0.70, pool, 0.05) == 0


def test_edge_priority_rises_near_the_edge_and_falls_with_labels():
    assert edge_priority(0.0, 0) == 1.0
    assert edge_priority(0.01, 0) > edge_priority(0.05, 0)
    assert edge_priority(0.02, 0) > edge_priority(0.02, 5)
    assert edge_priority(EDGE_WINDOW, 0) == 0.0


# --- the ranking --------------------------------------------------------------


def test_band_edge_rows_are_the_ones_within_the_window_closest_first():
    rows = [
        _row("far-low", 0.10),
        _row("edge-2", 0.49),
        _row("edge-1", 0.451),
        _row("edge-3", 0.68),
        _row("far-high", 0.99),
    ]
    queue = _rank(rows, min_spot_checks=0)
    edge = [e for e in queue if e.reason == REASON_BAND_EDGE]
    assert [e.sample_peak_id for e in edge] == ["edge-1", "edge-2", "edge-3"]
    assert [e.near_band for e in edge] == ["candidate", "candidate", "assigned"]


def test_a_region_the_labels_hold_already_ranks_below_an_empty_one():
    """A row nearer the edge, in a region the labels hold, is asked after one a
    little further out where none is held yet: the rank weighs both."""
    rows = [_row("a-held", 0.455), _row("b-empty", 0.74)]
    labels = [0.45, 0.46, 0.46, 0.47, 0.48] * 4
    queue = _rank(rows, labels=labels, min_spot_checks=0)
    assert [e.sample_peak_id for e in queue] == ["b-empty", "a-held"]
    assert queue[0].labels_near == 0
    assert queue[1].labels_near == 20


def test_every_fifth_place_is_a_spot_check_from_outside_the_window():
    edge_rows = [_row(f"e{i:02d}", 0.45 + i * 0.001) for i in range(20)]
    outside = [_row(f"o{i:02d}", 0.05 + i * 0.01) for i in range(20)]
    queue = _rank(edge_rows + outside)
    reasons = [e.reason for e in queue]
    for place, reason in enumerate(reasons[:25], start=1):
        assert reason == (
            REASON_SPOT_CHECK if place % SPOT_CHECK_EVERY == 0 else REASON_BAND_EDGE
        ), place
    spots = [e for e in queue if e.reason == REASON_SPOT_CHECK]
    assert len(spots) == 5
    assert all(e.sample_peak_id.startswith("o") for e in spots)


def test_the_draw_is_seeded_by_the_run():
    rows = [_row(f"o{i:02d}", 0.05 + i * 0.002) for i in range(100)]
    first = [e.sample_peak_id for e in _rank(rows)]
    again = [e.sample_peak_id for e in _rank(list(reversed(rows)))]
    other = [e.sample_peak_id for e in _rank(rows, seed="another-run")]
    assert first == again
    assert first != other


def test_a_verdict_on_a_spot_check_leaves_the_others_in_place():
    edge_rows = [_row(f"e{i:02d}", 0.45 + i * 0.001) for i in range(40)]
    outside = [_row(f"o{i:03d}", 0.05 + i * 0.001) for i in range(160)]
    rows = edge_rows + outside

    def spots(queue):
        return [e.sample_peak_id for e in queue if e.reason == REASON_SPOT_CHECK]

    before = spots(_rank(rows))
    judged = before[0]
    after = spots(_rank(rows, verdicts=[_verdict(judged, utc=AFTER)]))
    # The judged row leaves; every other one keeps its turn, and the draw
    # still holds as many.
    assert judged not in after
    assert after[: len(before) - 1] == before[1:]
    assert len(after) == len(before)


def test_fewer_edge_rows_shorten_the_draw_from_its_end():
    edge_rows = [_row(f"e{i:02d}", 0.45 + i * 0.001) for i in range(40)]
    outside = [_row(f"o{i:03d}", 0.05 + i * 0.001) for i in range(160)]

    def spots(queue):
        return [e.sample_peak_id for e in queue if e.reason == REASON_SPOT_CHECK]

    full = spots(_rank(edge_rows + outside))
    fewer = spots(_rank(edge_rows[:12] + outside))
    assert len(fewer) < len(full)
    assert fewer == full[: len(fewer)]


def test_a_run_with_few_edge_rows_still_draws_spot_checks():
    rows = [_row("e", 0.46)] + [_row(f"o{i}", 0.1) for i in range(10)]
    queue = _rank(rows)
    assert sum(e.reason == REASON_SPOT_CHECK for e in queue) == MIN_SPOT_CHECKS
    # with the edge row exhausted, the spot checks follow it
    assert queue[0].sample_peak_id == "e"


def test_a_run_without_bands_offers_spot_checks_alone():
    rows = [_row(f"o{i}", 0.5) for i in range(10)]
    queue = _rank(rows, bands=None)
    assert [e.reason for e in queue] == [REASON_SPOT_CHECK] * MIN_SPOT_CHECKS
    assert all(e.near_band is None for e in queue)


def test_rows_that_take_no_verdict_never_enter():
    rows = [
        _row("iso", 0.46, role="iso_child"),
        _row("reagent", 0.46, role="reagent"),
        _row("none", 0.46, formula=None),
    ]
    assert _rank(rows) == []


# --- verdicts: judged rows leave, stale ones come first -----------------------


def test_a_row_judged_as_the_run_reads_it_leaves_the_queue():
    rows = [_row("on-run", 0.46), _row("carried", 0.48)]
    verdicts = [
        # recorded on one of the run's rows, so since the run was made
        _verdict("on-run", evidence=0.46, utc=AFTER),
        # made before the run, on the same identity, at the same evidence: it
        # carries over to this run unchanged
        _verdict("carried", evidence=0.48),
    ]
    assert _rank(rows, verdicts=verdicts, min_spot_checks=0) == []


def test_a_verdict_whose_snapshot_has_no_evidence_is_not_called_rescored():
    rows = [_row("p", 0.46)]
    assert _rank(rows, verdicts=[_verdict("p", evidence=None)]) == []


def test_a_verdict_the_run_rescored_is_stale_and_first():
    rows = [_row("edge", 0.451), _row("judged", 0.60)]
    verdicts = [_verdict("judged", evidence=0.52)]
    queue = _rank(rows, verdicts=verdicts, min_spot_checks=0)
    assert [e.reason for e in queue] == [REASON_STALE, REASON_BAND_EDGE]
    stale = queue[0]
    assert stale.sample_peak_id == "judged"
    assert stale.stale["kind"] == STALE_RESCORED
    assert stale.stale["verdict"] == "confirmed"
    assert stale.stale["assignment_verification_id"] == "v-judged-C6H12O6"
    assert "evidence" not in stale.stale


def test_stale_rows_come_nearest_an_edge_first():
    rows = [_row("mid", 0.88), _row("far", 0.99), _row("near", 0.66)]
    verdicts = [
        _verdict("mid", evidence=0.8),
        _verdict("far", evidence=0.9),
        _verdict("near", evidence=0.5),
    ]
    queue = _rank(rows, verdicts=verdicts, min_spot_checks=0)
    assert [e.sample_peak_id for e in queue] == ["near", "mid", "far"]


def test_a_verdict_on_another_runs_reading_made_since_this_run_is_not_stale():
    """Judged after this run was made, on an older run's reading: the run did
    not change it since, and the verdict stands."""
    rows = [_row("p", 0.46)]
    verdicts = [_verdict("p", evidence=0.7, utc=AFTER)]
    assert _rank(rows, verdicts=verdicts) == []


def test_a_rescored_verdict_outranks_a_newer_one_on_a_formula_left_behind():
    """The verdict on the row's own identity is the one to judge again, even
    when a verdict on a formula the run no longer commits is newer."""
    rows = [_row("p", 0.60)]
    verdicts = [
        _verdict("p", evidence=0.52, utc=BEFORE - timedelta(days=2)),
        _verdict("p", formula="C7H8", evidence=0.9, utc=BEFORE),
    ]
    queue = _rank(rows, verdicts=verdicts, min_spot_checks=0)
    assert queue[0].stale["kind"] == STALE_RESCORED
    assert queue[0].stale["assigned_formula"] == "C6H12O6"


def test_a_verdict_on_a_formula_the_run_no_longer_commits_is_stale():
    rows = [_row("p", 0.95, formula="C7H8")]
    verdicts = [_verdict("p", formula="C6H12O6", evidence=0.9)]
    queue = _rank(rows, verdicts=verdicts, min_spot_checks=0)
    assert [e.reason for e in queue] == [REASON_STALE]
    assert queue[0].stale["kind"] == STALE_REASSIGNED
    assert queue[0].stale["assigned_formula"] == "C6H12O6"


def test_a_verdict_on_another_formula_made_since_the_run_is_not_stale():
    """The run did not change what that verdict judged: it was made after."""
    rows = [_row("p", 0.95, formula="C7H8")]
    verdicts = [_verdict("p", formula="C6H12O6", utc=AFTER)]
    queue = _rank(rows, verdicts=verdicts, min_spot_checks=0)
    assert queue == []


def test_a_row_judged_again_on_this_run_is_not_stale():
    rows = [_row("p", 0.95, formula="C7H8")]
    verdicts = [
        _verdict("p", formula="C6H12O6", evidence=0.9),
        _verdict("p", formula="C7H8", evidence=0.95, utc=AFTER),
    ]
    assert _rank(rows, verdicts=verdicts) == []


def test_entries_carry_no_probability_and_no_evidence():
    queue = _rank([_row("e", 0.46), _row("o", 0.1)])
    for entry in queue:
        record = entry.to_dict()
        assert "p_correct" not in record
        assert "evidence" not in record
