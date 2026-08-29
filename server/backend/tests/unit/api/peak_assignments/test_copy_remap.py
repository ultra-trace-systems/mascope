"""
Unit tests for the copy service's pure remap rules.

The mapping and the drop rules are decided from frames and dicts alone - no
database, no peak file - so they are pinned here, where a case is a literal
rather than a seeded sample. The fan-out that carries them is covered by
``tests/integration/api/peak_assignments/test_assignment_copy.py``.
"""

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.copy_service import (
    build_copied_rows,
    build_placeholder_rows,
    map_peaks_by_mz,
    remap_source_rows,
)


BANDS = {"assigned": 0.8, "candidate": 0.5}


def _peaks(*rows) -> pd.DataFrame:
    """A destination peak frame from ``(peak_id, mz, intensity)`` tuples."""
    return pd.DataFrame(
        {
            "sample_peak_id": [row[0] for row in rows],
            "mz": [row[1] for row in rows],
            "intensity": [row[2] for row in rows],
        }
    )


def _source_row(peak_id, mz, **overrides) -> dict:
    """One source assignment row, in the shape ``to_dict`` returns."""
    row = {
        "peak_assignment_id": f"assignment-{peak_id}",
        "sample_peak_id": peak_id,
        "sample_peak_mz": mz,
        "sample_peak_intensity": 5000.0,
        "role": "M0",
        "assigned_formula": "C6H12O6",
        "ion_formula": "C6H13O6+",
        "ionization_mechanism_id": "mech-1",
        "isotope_label": "M0",
        "isotope_formula": "C6H12O6",
        "source": "database",
        "fit_score": 0.92,
        "mz_error_ppm": 1.2,
        "abundance_error": 0.05,
        "tier": "assigned",
        "target_compound_id": "compound-1",
        "target_ion_id": "ion-1",
        "owner_peak_assignment_id": None,
        "alternatives": [{"assigned_formula": "C7H16O5", "fit_score": 0.4}],
        "provenance": {"plausibility": 0.9},
    }
    row.update(overrides)
    return row


#: A tolerance that admits ~5 ppm either way, the shape of a real
#: resolution-adaptive window without depending on a resolution function.
def _tol(_mz: float) -> float:
    return 5.0


class TestMzMapping:
    """The mu-corrected m/z match between two samples' peaks."""

    def test_a_peak_maps_to_its_counterpart_within_tolerance(self):
        """The nearest destination peak inside the window wins."""
        mapping = map_peaks_by_mz(
            {"src-1": 181.0707},
            _peaks(("dst-1", 181.0709, 4000.0), ("dst-2", 200.0, 10.0)),
            mu_source_ppm=0.0,
            mu_destination_ppm=0.0,
            tol_fn=_tol,
        )

        assert mapping == {"src-1": "dst-1"}

    def test_a_peak_with_no_counterpart_does_not_map(self):
        """Outside the window is a miss, not a nearest-anything fallback."""
        mapping = map_peaks_by_mz(
            {"src-1": 181.0707},
            _peaks(("dst-1", 181.5, 4000.0)),
            mu_source_ppm=0.0,
            mu_destination_ppm=0.0,
            tol_fn=_tol,
        )

        assert mapping == {}

    def test_opposing_calibration_offsets_are_corrected_before_matching(self):
        """The whole point of mu: drift must not push a peak out of tolerance.

        The two samples sit 8 ppm apart on the raw axis - outside the 5 ppm
        window - purely because each is calibrated differently. Corrected by
        their own run offsets they are the same species and must map.
        """
        source_mz = 181.0707
        raw_destination_mz = source_mz * (1 + 8e-6)

        uncorrected = map_peaks_by_mz(
            {"src-1": source_mz},
            _peaks(("dst-1", raw_destination_mz, 4000.0)),
            mu_source_ppm=0.0,
            mu_destination_ppm=0.0,
            tol_fn=_tol,
        )
        corrected = map_peaks_by_mz(
            {"src-1": source_mz},
            _peaks(("dst-1", raw_destination_mz, 4000.0)),
            mu_source_ppm=-4.0,
            mu_destination_ppm=4.0,
            tol_fn=_tol,
        )

        assert uncorrected == {}
        assert corrected == {"src-1": "dst-1"}


class TestDropRules:
    """What survives the remap, and what the manifest counts."""

    def test_a_row_whose_peak_did_not_map_is_dropped(self):
        rows = [_source_row("src-1", 181.0707), _source_row("src-2", 250.0)]

        kept, _, counts = remap_source_rows(rows, {"src-1": "dst-1"})

        assert [row["sample_peak_id"] for row, _ in kept] == ["src-1"]
        assert counts["dropped_no_destination_peak"] == 1
        assert counts["mapped"] == 1

    def test_two_rows_on_one_peak_keep_the_better_score(self):
        """A merge on the destination's axis is settled by source fit."""
        rows = [
            _source_row("src-1", 181.0707, fit_score=0.42, assigned_formula="C5H8O2"),
            _source_row("src-2", 181.0709, fit_score=0.93),
        ]

        kept, _, counts = remap_source_rows(rows, {"src-1": "dst-1", "src-2": "dst-1"})

        assert len(kept) == 1
        assert kept[0][0]["sample_peak_id"] == "src-2"
        assert counts["dropped_peak_conflicts"] == 1

    def test_an_isotopologue_whose_owner_was_dropped_is_dropped(self):
        """An owner reference must name a row of the same run, so it goes."""
        owner = _source_row("src-1", 181.0707)
        child = _source_row(
            "src-2",
            182.0741,
            role="iso_child",
            isotope_label="M+1",
            owner_peak_assignment_id=owner["peak_assignment_id"],
        )

        kept, owner_destination, counts = remap_source_rows(
            [owner, child], {"src-2": "dst-2"}
        )

        assert kept == []
        assert owner_destination == {}
        assert counts["dropped_orphaned_isotopologues"] == 1

    def test_a_surviving_isotopologue_points_at_its_owners_new_peak(self):
        """The owner link is re-expressed in the destination's peak ids."""
        owner = _source_row("src-1", 181.0707)
        child = _source_row(
            "src-2",
            182.0741,
            role="iso_child",
            isotope_label="M+1",
            owner_peak_assignment_id=owner["peak_assignment_id"],
        )

        kept, owner_destination, _ = remap_source_rows(
            [owner, child], {"src-1": "dst-1", "src-2": "dst-2"}
        )

        assert len(kept) == 2
        assert owner_destination == {child["peak_assignment_id"]: "dst-1"}

    def test_an_ownerless_isotopologue_survives(self):
        """Routine engine output: an ion whose M0 peak went to someone else.

        It was never attached to a row that could be dropped, so the
        orphan rule must not take it.
        """
        child = _source_row(
            "src-2", 182.0741, role="iso_child", owner_peak_assignment_id=None
        )

        kept, owner_destination, counts = remap_source_rows([child], {"src-2": "dst-2"})

        assert len(kept) == 1
        assert owner_destination == {}
        assert counts["dropped_orphaned_isotopologues"] == 0


class TestRowAssembly:
    """What a copied row carries into the destination's ledger."""

    def _build(self, *, fit_score, rescore=True):
        row = _source_row("src-1", 181.0707)
        return build_copied_rows(
            [(row, "dst-1")],
            {},
            {"dst-1": (181.0709, 4321.0)},
            {("C6H12O6", "mech-1"): "seed-ion-1"},
            {"seed-ion-1": fit_score},
            {("seed-ion-1", "dst-1"): {"mz_error_ppm": -0.4, "abundance_error": 0.02}},
            BANDS,
            "source-sample",
            rescore,
        )[0]

    def test_the_row_takes_the_destinations_peak_identity(self):
        built = self._build(fit_score=0.91)

        assert built["sample_peak_id"] == "dst-1"
        assert built["sample_peak_mz"] == 181.0709
        assert built["sample_peak_intensity"] == 4321.0

    def test_curation_travels_verbatim(self):
        """Formula, roles, target references and alternatives are the copy."""
        built = self._build(fit_score=0.91)

        assert built["assigned_formula"] == "C6H12O6"
        assert built["ion_formula"] == "C6H13O6+"
        assert built["target_compound_id"] == "compound-1"
        assert built["target_ion_id"] == "ion-1"
        assert built["alternatives"] == [
            {"assigned_formula": "C7H16O5", "fit_score": 0.4}
        ]
        assert built["provenance"]["plausibility"] == 0.9

    def test_the_evidence_is_the_destinations_own(self):
        """B2: numbers describe the sample they were measured on."""
        built = self._build(fit_score=0.91)

        assert built["fit_score"] == 0.91
        assert built["mz_error_ppm"] == -0.4
        assert built["abundance_error"] == 0.02

    @pytest.mark.parametrize(
        ("fit_score", "expected_tier"),
        [(0.91, "assigned"), (0.62, "candidate"), (0.11, "below_assignability")],
    )
    def test_the_tier_is_recomputed_from_the_new_fit(self, fit_score, expected_tier):
        """A destination whose data supports the formula less says so.

        The source row is 'assigned' at 0.92 in every case; what the copied
        row claims follows the re-measured fit under the declared bands, which
        is what makes tier-fit coherence hold on the published run.
        """
        built = self._build(fit_score=fit_score)

        assert built["tier"] == expected_tier

    def test_a_formula_the_rescore_could_not_measure_carries_no_evidence(self):
        """No fit means no tier claim - not the source's inherited one."""
        built = self._build(fit_score=None)

        assert built["fit_score"] is None
        assert built["tier"] == "below_assignability"

    def test_provenance_records_where_the_row_came_from(self):
        built = self._build(fit_score=0.91)

        assert built["provenance"]["copied_from"] == {
            "sample_item_id": "source-sample",
            "sample_peak_id": "src-1",
            "peak_assignment_id": "assignment-src-1",
            "fit_score": 0.92,
        }

    def test_the_literal_mode_carries_the_sources_numbers(self):
        """B1, the internal degenerate mode the note keeps unexposed."""
        built = self._build(fit_score=0.11, rescore=False)

        assert built["fit_score"] == 0.92
        assert built["mz_error_ppm"] == 1.2
        assert built["tier"] == "assigned"


class TestPlaceholders:
    """Every destination peak gets a row, so the published run is complete."""

    def test_unmapped_peaks_become_unassigned_rows(self):
        placeholders = build_placeholder_rows(
            _peaks(
                ("dst-1", 181.0709, 4321.0),
                ("dst-2", 250.0, 12.0),
                ("dst-3", 300.0, 7.0),
            ),
            {"dst-1"},
        )

        assert [row["sample_peak_id"] for row in placeholders] == ["dst-2", "dst-3"]
        assert {row["tier"] for row in placeholders} == {"unassigned"}
        assert {row["role"] for row in placeholders} == {"unassigned"}
        assert all(row["assigned_formula"] is None for row in placeholders)
        assert all(row["fit_score"] is None for row in placeholders)

    def test_a_fully_mapped_sample_needs_no_placeholders(self):
        placeholders = build_placeholder_rows(
            _peaks(("dst-1", 181.0709, 4321.0)), {"dst-1"}
        )

        assert placeholders == []
