"""Unit tests for the Sample view derived from the batch ledger (pure parts).

No DB: the identifiers this view mints, the shape a member row takes on its way
out, the detail built from an anchor's registry and consensus, and the snapshot
a verdict against a derived row records. See ``fold_view.py``.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.config import (
    FOLD_ENGINE,
    RESERVED_ENGINE_NAMES,
)
from mascope_backend.api.new.peak_assignments.fold_view import (
    DERIVED_READ_ONLY_CODE,
    DerivedAssignmentReadOnlyException,
    fold_assignment_id,
    fold_id_target,
    fold_run_id,
    is_fold_id,
    member_detail,
    member_row,
    verification_target,
)


# --- identifiers --------------------------------------------------------------


def test_derived_ids_round_trip_and_are_told_apart_from_stored_ones():
    assert fold_run_id("si-1") == "fold-si-1"
    assert fold_assignment_id("bp-9") == "fold-bp-9"
    assert is_fold_id(fold_run_id("si-1"))
    assert is_fold_id(fold_assignment_id("bp-9"))
    assert fold_id_target(fold_run_id("si-1")) == "si-1"
    assert fold_id_target(fold_assignment_id("bp-9")) == "bp-9"
    # Stored ids are letters and digits; none of these is ours.
    assert not is_fold_id("a1b2c3d4e5f6g7h8")
    assert not is_fold_id("")
    assert not is_fold_id(None)


def test_the_derived_engine_name_is_reserved_against_imports():
    assert FOLD_ENGINE in RESERVED_ENGINE_NAMES


def test_a_write_against_a_derived_row_is_a_coded_409():
    exc = DerivedAssignmentReadOnlyException("fold-bp-1", "Sample A", "curated")
    assert exc.status_code == 409
    assert exc.error_code == DERIVED_READ_ONLY_CODE
    assert "cannot be curated" in exc.detail
    assert "Assign the sample" in exc.detail


# --- the row -------------------------------------------------------------------

_REGISTRY = [
    {"formula": "C6H12O6", "ion_formula": "C6H13O6+", "ionization_mechanism_id": "m-h"},
    {
        "formula": "C5H12N2O4",
        "ion_formula": "C5H13N2O4+",
        "ionization_mechanism_id": "m-h",
    },
]


def _anchor(**overrides):
    base = dict(
        batch_peak_id="bp-1",
        mz=181.0707,
        candidates=list(_REGISTRY),
        alternatives=[{"formula": "C5H12N2O4", "evidence_share": 0.2, "n": 1}],
        provenance={"n_assigned": 3, "agreement": 0.67, "p_correct": 0.95},
        consensus_formula="C6H12O6",
        consensus_ion_formula="C6H13O6+",
        consensus_tier="assigned",
        support_fraction=0.67,
        n_present=3,
        is_ambiguous=0,
        best_fit_score=0.95,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _member(**overrides):
    base = dict(
        batch_peak_id="bp-1",
        sample_item_id="si-1",
        sample_peak_id="p1",
        sample_peak_mz=181.0706,
        intensity=5000.0,
        tier="assigned",
        fit_score=0.91,
        assigned_formula="C6H12O6",
        candidate=0,
        role="M0",
        owner_batch_peak_id=None,
        p_correct=0.9,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_member_row_carries_what_the_member_has_and_names_its_anchor():
    row = member_row(_member(), _anchor())
    assert row["peak_assignment_id"] == "fold-bp-1"
    assert row["peak_assignment_run_id"] == "fold-si-1"
    assert row["batch_peak_id"] == "bp-1"
    assert (row["sample_item_id"], row["sample_peak_id"]) == ("si-1", "p1")
    assert (row["sample_peak_mz"], row["sample_peak_intensity"]) == (181.0706, 5000.0)
    assert row["assigned_formula"] == "C6H12O6"
    # The ion formula and mechanism are the registry entry the member names.
    assert row["ion_formula"] == "C6H13O6+"
    assert row["ionization_mechanism_id"] == "m-h"
    assert (row["tier"], row["role"], row["fit_score"]) == ("assigned", "M0", 0.91)
    assert row["p_correct"] == 0.9
    assert row["owner_peak_assignment_id"] is None


def test_what_only_a_run_computes_is_absent_rather_than_invented():
    row = member_row(_member(), _anchor())
    for key in (
        "mz_error_ppm",
        "abundance_error",
        "isotope_label",
        "isotope_formula",
        "source",
        "engine_tier",
        "target_compound_id",
        "target_ion_id",
        "evidence",
        "p_correct_provisional",
        "corroboration_adducts",
        "sample_peak_tof",
    ):
        assert row[key] is None, key


def test_an_isotopologue_member_names_its_owner_by_the_owners_derived_id():
    row = member_row(
        _member(sample_peak_id="p2", role="iso_child", owner_batch_peak_id="bp-0"),
        _anchor(),
    )
    assert row["role"] == "iso_child"
    assert row["owner_peak_assignment_id"] == fold_assignment_id("bp-0")


def test_a_member_with_no_role_tier_or_intensity_reads_as_unassigned():
    """Rows written before members carried a role or tier, and members whose
    peak had no intensity: the defaults the record schema needs, not nulls."""
    row = member_row(
        _member(
            assigned_formula=None, candidate=None, role=None, tier=None, intensity=None
        ),
        _anchor(),
    )
    assert (row["role"], row["tier"]) == ("unassigned", "unassigned")
    assert row["sample_peak_intensity"] == 0.0
    assert row["ion_formula"] is None and row["ionization_mechanism_id"] is None


def test_a_member_pointing_past_the_registry_carries_no_ion_formula():
    row = member_row(_member(candidate=7), _anchor())
    assert row["assigned_formula"] == "C6H12O6"
    assert row["ion_formula"] is None


# --- the detail ------------------------------------------------------------------


def test_the_detail_offers_the_anchors_other_identities_as_alternatives():
    detail = member_detail(_member(), _anchor())
    assert [a["assigned_formula"] for a in detail["alternatives"]] == ["C5H12N2O4"]
    (alt,) = detail["alternatives"]
    assert alt["ion_formula"] == "C5H13N2O4+"
    assert alt["ionization_mechanism_id"] == "m-h"
    assert alt["source"] == FOLD_ENGINE
    # The consensus share that formula holds rides along where the anchor has one.
    assert (alt["evidence_share"], alt["n_members"]) == (0.2, 1)


def test_the_detail_provenance_is_the_anchors_with_this_members_probability():
    detail = member_detail(_member(p_correct=0.9), _anchor())
    provenance = detail["provenance"]
    assert provenance["n_assigned"] == 3 and provenance["agreement"] == 0.67
    assert provenance["p_correct"] == 0.9  # the member's, not the anchor's 0.95
    batch = provenance["batch_peak"]
    assert batch["batch_peak_id"] == "bp-1"
    assert batch["consensus_formula"] == "C6H12O6"
    assert batch["n_present"] == 3
    assert batch["is_ambiguous"] is False


def test_an_unassigned_member_lists_every_identity_and_no_provenance_surprises():
    detail = member_detail(
        _member(assigned_formula=None, candidate=None, tier="unassigned"),
        _anchor(provenance=None, alternatives=None),
    )
    assert [a["assigned_formula"] for a in detail["alternatives"]] == [
        "C6H12O6",
        "C5H12N2O4",
    ]
    assert detail["alternatives"][0]["evidence_share"] is None
    assert set(detail["provenance"]) == {"batch_peak", "p_correct"}


# --- the verification snapshot ----------------------------------------------------


def test_a_verdict_against_a_derived_row_snapshots_the_member_and_links_no_row():
    target = verification_target(_member(), _anchor())
    assert (target.sample_item_id, target.sample_peak_id) == ("si-1", "p1")
    assert target.assigned_formula == "C6H12O6"
    assert target.ionization_mechanism_id == "m-h"
    assert target.fit_score == 0.91
    assert target.provenance == {"p_correct": 0.9, "evidence": None}
    assert target.peak_assignment_id is None
    assert target.peak_assignment_run_id is None


@pytest.mark.parametrize("candidate", [None, 5])
def test_a_verdict_target_without_a_registry_entry_has_no_mechanism(candidate):
    target = verification_target(_member(candidate=candidate), _anchor())
    assert target.ionization_mechanism_id is None
