"""Unit tests for the batch runs' pure parts: the snapshot arrays and rows, the
series and ledger shapes read back off them, the run record, and which runs a
completion prunes. No DB. See ``batch_runs.py``."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.new.peak_assignments.batch_peaks import compute_consensus
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    BATCH_RUN_KEEP,
    MEMBER_FIELDS,
    anchor_snapshot_row,
    consensus_counts,
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
        target_compound_id=None,
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
        consensus_role=None,
        best_fit_score=0.95,
        support_fraction=1.0,
        n_present=2,
        is_ambiguous=0,
        intensity_variable="height",
        max_intensity=5000.0,
        isotopologue_of=None,
        candidates=[{"formula": "C6H12O6"}],
        provenance={},
        engine_tier=None,
        engine_claim=None,
        engine_level=None,
        engine_evidence=None,
        engine_lift=None,
        seeded=False,
        continuity_grade=None,
        continuity=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_member_arrays_are_parallel_and_in_sample_order():
    arrays = member_arrays(
        [
            _member("s2", "p9", intensity=20.0, target_compound_id="tc-2"),
            _member("s1", "p1", intensity=10.0),
        ]
    )
    assert tuple(arrays) == MEMBER_FIELDS
    assert arrays["sample_item_ids"] == ["s1", "s2"]
    assert arrays["sample_peak_ids"] == ["p1", "p9"]
    assert arrays["intensities"] == [10.0, 20.0]
    # Each member's own target compound, as the batch ledger names one off it.
    assert arrays["target_compound_ids"] == [None, "tc-2"]
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
    # A snapshot keeps no batch level (step 5.8c): the live ledger's fields,
    # each empty.
    assert {key: meta[key] for key in ("level", "level_evidence", "level_lift")} == {
        "level": None,
        "level_evidence": None,
        "level_lift": None,
    }
    assert meta["identification_level"] is None


def test_the_ledger_names_the_instrument_partition_live_and_off_a_snapshot():
    # A batch of both instrument types has a partition of each: the row says
    # which, by the instrument type its unit belongs to.
    for unit, instrument_type in (
        ("sum_peak_heights", "orbi"),
        ("sum_peak_areas", "tof"),
        (None, None),
    ):
        anchor = _anchor(sample_batch_id="sb-1", intensity_variable=unit)
        row = SimpleNamespace(**anchor_snapshot_row("run-1", anchor, []))
        assert _batch_peak_meta(anchor)["instrument_type"] == instrument_type
        assert snapshot_anchor_meta(row, "sb-1")["instrument_type"] == instrument_type


def test_a_snapshot_keeps_the_importing_engines_verdict():
    # The run selector shows an import's verdicts as that run left them.
    anchor = _anchor(engine_tier="candidate", engine_claim="level 3")
    row = anchor_snapshot_row("run-1", anchor, [])
    assert (row["engine_tier"], row["engine_claim"]) == ("candidate", "level 3")
    meta = snapshot_anchor_meta(SimpleNamespace(**row), "sb-1")
    assert (meta["engine_tier"], meta["engine_claim"]) == ("candidate", "level 3")


def test_a_snapshot_row_serves_every_field_the_live_ledger_does():
    anchor = _anchor(sample_batch_id="sb-1")
    live = _batch_peak_meta(anchor)
    snap = snapshot_anchor_meta(
        SimpleNamespace(**anchor_snapshot_row("run-1", anchor, [])), "sb-1"
    )
    assert set(snap) - {"batch_peak_run_id"} == set(live)


def _counts(meta):
    return meta["n_assigned"], meta["n_winner"]


def _voter(sample, formula, fit, candidate):
    """A member as the vote reads it, beside its registry index as a
    snapshot keeps it."""
    return {
        "sample_item_id": sample,
        "sample_peak_id": f"p-{sample}",
        "assigned_formula": formula,
        "ion_formula": None,
        "ionization_mechanism_id": None,
        "fit_score": fit,
        "intensity": 1000.0,
        "tier": "assigned" if formula else "unassigned",
        "role": "M0" if formula else None,
        "p_correct": None,
        "candidate": candidate,
    }


def _ledger_rows(voters, manual=None):
    """The live ledger's row and an earlier run's, for an anchor whose
    consensus the vote takes over ``voters``."""
    consensus = compute_consensus(voters, "bp-1", manual=manual)
    anchor = _anchor(
        sample_batch_id="sb-1",
        consensus_formula=consensus.consensus_formula,
        consensus_role=consensus.consensus_role,
        support_fraction=consensus.support_fraction,
        n_present=consensus.n_present,
        candidates=[{"formula": "C6H12O6"}, {"formula": "C7H14O6"}],
        provenance=consensus.provenance,
    )
    snapshot = SimpleNamespace(**anchor_snapshot_row("run-1", anchor, voters))
    return _batch_peak_meta(anchor), snapshot_anchor_meta(snapshot, "sb-1")


def test_the_ledger_counts_the_members_that_read_a_formula_and_agree():
    # Three members: two read a formula, one of them the consensus. The share
    # is over the two, so the row serves both counts beside it.
    live, snap = _ledger_rows(
        [
            _voter("s1", "C6H12O6", 0.95, 0),
            _voter("s2", "C7H14O6", 0.2, 1),
            _voter("s3", None, None, None),
        ]
    )
    assert live["consensus_formula"] == "C6H12O6"
    assert live["n_present"] == 3
    assert live["support_fraction"] == 0.5
    assert _counts(live) == (2, 1)
    # An earlier run's ledger counts them off its member arrays and registry.
    assert _counts(snap) == (2, 1)


def test_a_pin_no_member_carries_counts_no_member_reading_a_formula():
    # Its 0 % agreement is over no member at all, which the counts say.
    live, snap = _ledger_rows(
        [_voter("s1", None, None, None), _voter("s2", None, None, None)],
        manual={"formula": "C6H12O6"},
    )
    assert live["consensus_formula"] == "C6H12O6"
    assert live["support_fraction"] == 0.0
    assert _counts(live) == (0, 0)
    assert _counts(snap) == (0, 0)


def test_the_counts_are_null_where_the_consensus_records_none():
    # No member reads a formula and no pin names one: no consensus to count.
    live, snap = _ledger_rows([_voter("s1", None, None, None)])
    assert live["consensus_formula"] is None
    assert _counts(live) == (None, None)
    assert _counts(snap) == (None, None)
    # A source's role accounts for the anchor: its provenance counts no
    # formulas, and a snapshot of it counts none either.
    role = SimpleNamespace(
        **anchor_snapshot_row(
            "run-1",
            _anchor(consensus_role="reagent", provenance={"n_role": 2}),
            [_member("s1", "p1")],
        )
    )
    assert _counts(
        _batch_peak_meta(_anchor(sample_batch_id="sb-1", provenance={"n_role": 2}))
    ) == (
        None,
        None,
    )
    assert _counts(snapshot_anchor_meta(role, "sb-1")) == (None, None)
    # A count the provenance does not hold as an integer is none.
    assert consensus_counts({"n_assigned": True, "n_winner": "2"}) == {
        "n_assigned": None,
        "n_winner": None,
    }
    assert consensus_counts(None) == {"n_assigned": None, "n_winner": None}


@pytest.mark.parametrize("reads, served", [("C6H12O6", True), ("C7H14O6", False)])
def test_the_ledger_and_a_snapshot_serve_a_series_only_beside_its_formula(
    reads, served
):
    # A detection that has not measured an anchor yet has already put its
    # record on it: the consensus there is still the reading it held before.
    record = {"formula": "C6H12O6", "unit": "CH2", "has_anchor": True}
    anchor = _anchor(
        sample_batch_id="sb-1",
        consensus_formula=reads,
        provenance={"series": record},
    )
    expected = record if served else None
    assert _batch_peak_meta(anchor)["series"] == expected
    assert anchor_snapshot_row("run-1", anchor, [])["series"] == expected


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


def test_a_snapshot_keeps_the_persistence_admission_and_the_batch_s_reasons():
    # The run selector shows an earlier search's admissions and caps beside
    # the tiers they explain.
    record = {"share": 0.9167, "n_present": 11, "n_samples": 12, "split": 0.4583}
    reasons = [{"rule": "persistence_only", "detail": "...", "caps": True}]
    anchor = _anchor(
        sample_batch_id="sb-1",
        consensus_tier="candidate",
        provenance={"persistence": record, "tier_reasons": reasons},
    )
    row = anchor_snapshot_row("run-1", anchor, [])
    assert (row["persistence"], row["tier_reasons"]) == (record, reasons)
    meta = snapshot_anchor_meta(SimpleNamespace(**row), "sb-1")
    assert (meta["persistence"], meta["tier_reasons"]) == (record, reasons)
    live = _batch_peak_meta(anchor)
    assert (live["persistence"], live["tier_reasons"]) == (record, reasons)
    bare = _batch_peak_meta(_anchor(sample_batch_id="sb-1"))
    assert (bare["persistence"], bare["tier_reasons"]) == (None, None)


# --- a step inside a run, or as one ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("raised", [RuntimeError("gone"), asyncio.CancelledError()])
async def test_a_step_run_on_its_own_closes_its_run_however_it_stops(raised):
    """A step opens a run of its own outside a pass, and closes it as failed
    whatever stopped it, a cancelled task included: a run left running holds
    the ledger until the startup reaper runs."""
    from mascope_backend.api.new.peak_assignments import batch_runs

    async def work(run_id):
        raise raised

    with (
        patch.object(batch_runs, "start_run", AsyncMock(return_value="run-1")),
        patch.object(batch_runs, "complete_run", AsyncMock()) as complete,
        patch.object(batch_runs, "fail_run", AsyncMock()) as fail,
    ):
        with pytest.raises(type(raised)):
            await batch_runs.in_batch_run("b", "detect_series", work)
    fail.assert_awaited_once()
    assert fail.await_args.args[:2] == ("b", "run-1")
    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_step_inside_a_pass_writes_into_the_passs_run():
    from mascope_backend.api.new.peak_assignments import batch_runs

    seen = []

    async def work(run_id):
        seen.append(run_id)
        return {"n": 1}

    with patch.object(batch_runs, "start_run", AsyncMock()) as start:
        answer = await batch_runs.in_batch_run(
            "b", "detect_series", work, batch_peak_run_id="pass-1"
        )
    assert answer == {"n": 1}
    assert seen == ["pass-1"]
    start.assert_not_awaited()
