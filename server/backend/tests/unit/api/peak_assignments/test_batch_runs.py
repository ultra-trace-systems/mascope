"""Unit tests for the batch runs' pure parts: the snapshot arrays and rows, the
series and ledger shapes read back off them, the run record, and which runs a
completion prunes. No DB. See ``batch_runs.py``."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments.batch_runs import (
    BATCH_RUN_KEEP,
    MEMBER_FIELDS,
    anchor_snapshot_row,
    member_arrays,
    run_record,
    runs_to_prune,
    snapshot_anchor_meta,
    snapshot_series,
)


def _member(sample, peak, **over):
    base = dict(
        sample_item_id=sample,
        sample_peak_id=peak,
        mz_delta_ppm=0.5,
        intensity=1000.0,
        candidate=0,
        tier=3,
        role=1,
        fit_score=0.9,
        p_correct=None,
        owner_batch_peak_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _anchor(**over):
    base = dict(
        batch_peak_id="bp-1",
        mz=181.0707,
        ionization_mode_id="mode-1",
        consensus_formula="C6H12O6",
        consensus_ion_formula="C6H13O6+",
        ionization_mechanism_id="mH",
        consensus_tier="assigned",
        best_fit_score=0.95,
        support_fraction=1.0,
        n_present=2,
        is_ambiguous=0,
        intensity_variable="height",
        max_intensity=5000.0,
        isotopologue_of=None,
        candidates=[{"formula": "C6H12O6"}],
        provenance={},
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_member_arrays_are_parallel_and_in_sample_order():
    arrays = member_arrays(
        [_member("s2", "p9", intensity=20.0), _member("s1", "p1", intensity=10.0)]
    )
    assert tuple(arrays) == MEMBER_FIELDS
    assert arrays["sample_item_ids"] == ["s1", "s2"]
    assert arrays["sample_peak_ids"] == ["p1", "p9"]
    assert arrays["intensities"] == [10.0, 20.0]
    assert {len(v) for v in arrays.values()} == {2}


def test_the_snapshot_row_carries_the_consensus_the_registry_and_the_members():
    anchor = _anchor(provenance={"manual": {"formula": "C6H12O6", "candidate": 0}})
    row = anchor_snapshot_row("run-1", anchor, [_member("s1", "p1")])
    assert row["batch_peak_run_id"] == "run-1"
    assert row["batch_peak_id"] == "bp-1"
    assert row["consensus_formula"] == "C6H12O6"
    assert row["curated"] == 1
    assert row["candidates"] == [{"formula": "C6H12O6"}]
    assert row["members"]["sample_item_ids"] == ["s1"]
    # The registry is copied, not shared: the live one keeps growing.
    anchor.candidates.append({"formula": "X"})
    assert row["candidates"] == [{"formula": "C6H12O6"}]


def test_the_ledger_shape_off_a_snapshot_matches_the_live_ones():
    anchor = _anchor()
    row = SimpleNamespace(**anchor_snapshot_row("run-1", anchor, []))
    meta = snapshot_anchor_meta(row, "sb-1")
    assert meta["sample_batch_id"] == "sb-1"
    assert meta["batch_peak_run_id"] == "run-1"
    assert meta["is_ambiguous"] is False
    assert meta["curated"] is False
    assert meta["consensus_tier"] == "assigned"
    assert meta["max_intensity"] == 5000.0


def test_the_series_off_a_snapshot_names_tiers_and_narrows_to_samples():
    row = SimpleNamespace(
        **anchor_snapshot_row(
            "run-1",
            _anchor(),
            [
                _member("s1", "p1", tier=3),
                _member("s2", "p2", tier=None, candidate=None),
            ],
        )
    )
    series = snapshot_series(row)
    assert series["sample_item_ids"] == ["s1", "s2"]
    assert series["tiers"] == ["assigned", None]
    narrowed = snapshot_series(row, ["s2"])
    assert narrowed["sample_peak_ids"] == ["p2"]
    assert snapshot_series(SimpleNamespace(members=None))["sample_item_ids"] == []


def test_the_run_record_flags_the_current_one():
    run = SimpleNamespace(
        is_current=1, to_dict=lambda: {"batch_peak_run_id": "r1", "action": "rebuild"}
    )
    record = run_record(run)
    assert record["current"] is True
    assert record["action"] == "rebuild"
    assert (
        run_record(SimpleNamespace(is_current=0, to_dict=lambda: {}))["current"]
        is False
    )


def test_completion_prunes_the_oldest_non_current_runs_beyond_the_keep():
    t0 = datetime(2026, 9, 4, tzinfo=timezone.utc)
    runs = [
        SimpleNamespace(
            batch_peak_run_id=f"r{i}",
            is_current=int(i == 9),
            batch_peak_run_utc_created=t0 + timedelta(hours=i),
        )
        for i in range(10)
    ]
    pruned = runs_to_prune(runs, keep=BATCH_RUN_KEEP)
    # Nine non-current runs, the five newest kept: r8..r4 stay, r3..r0 go.
    assert pruned == ["r3", "r2", "r1", "r0"]
    # The current run is never pruned, however old.
    old_current = SimpleNamespace(
        batch_peak_run_id="cur",
        is_current=1,
        batch_peak_run_utc_created=t0 - timedelta(days=30),
    )
    assert "cur" not in runs_to_prune(runs + [old_current], keep=1)
    assert runs_to_prune(runs[:3], keep=5) == []
