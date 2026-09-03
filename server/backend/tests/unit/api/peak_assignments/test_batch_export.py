"""Unit tests for the ledger export's flat row. No DB."""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.batch_export import (
    COLUMNS,
    member_export_row,
)


def _anchor(**over):
    base = dict(
        sample_batch_id="sb-1",
        batch_peak_id="bp-1",
        mz=181.0707,
        consensus_formula="C6H12O6",
        consensus_ion_formula="C6H13O6+",
        ionization_mechanism_id="mH",
        consensus_tier="assigned",
        support_fraction=1.0,
        n_present=2,
        is_ambiguous=0,
        max_intensity=5000.0,
        isotopologue_of=None,
        candidates=[
            {
                "formula": "C6H12O6",
                "ion_formula": "C6H13O6+",
                "ionization_mechanism_id": "mH",
                "source": "database",
            }
        ],
        provenance={},
    )
    base.update(over)
    return SimpleNamespace(**base)


def _member(**over):
    base = dict(
        batch_peak_id="bp-1",
        sample_item_id="s1",
        sample_peak_id="p1",
        mz_delta_ppm=0.5,
        intensity=4000.0,
        candidate=0,
        tier=3,
        role=1,
        fit_score=0.95,
        p_correct=0.9,
        owner_batch_peak_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_a_row_carries_the_consensus_beside_the_members_own_reading():
    row = member_export_row(_member(), _anchor(), "Sample 1")
    assert list(row) == list(COLUMNS)
    assert row["consensus_formula"] == "C6H12O6"
    assert row["consensus_tier"] == "assigned"
    assert row["curated"] is False
    assert row["sample_item_name"] == "Sample 1"
    assert row["assigned_formula"] == "C6H12O6"
    assert row["source"] == "database"
    assert row["tier"] == "assigned"
    assert row["role"] == "M0"
    assert row["mz"] == pytest.approx(181.0707 * (1 + 0.5e-6))
    assert row["fit_score"] == pytest.approx(0.95)


def test_an_unassigned_member_reads_as_unassigned():
    row = member_export_row(
        _member(candidate=None, tier=None, role=None, fit_score=None, p_correct=None),
        _anchor(),
        "Sample 1",
    )
    assert row["assigned_formula"] is None
    assert row["source"] is None
    assert row["tier"] == "unassigned"
    assert row["role"] == "unassigned"


def test_a_pinned_anchor_reads_as_curated():
    anchor = _anchor(provenance={"manual": {"formula": "C6H12O6", "candidate": 0}})
    assert member_export_row(_member(), anchor, None)["curated"] is True
