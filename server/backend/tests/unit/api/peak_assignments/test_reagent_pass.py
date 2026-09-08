"""What the reagent pre-pass writes into the ledger.

The library half is tested in ``libraries/tools/tests/test_reagent_library.py``;
what matters here is the shape of the row it produces, because that shape is
what decides whether a reagent peak stays out of the analyte ledger or quietly
re-enters it as an assignment nobody made.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_REAGENT,
    SOURCE_REAGENT,
)
from mascope_backend.api.new.peak_assignments.import_validation import (
    coherent_tiers,
    owner_link_errors,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    build_reagent_assignments,
    claim_reagent_peaks,
    reagent_library_for,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_tools.composition.heuristic_filter import predict_isotopes


def _peaks(*ions: tuple[str, int, float]) -> pd.DataFrame:
    """A peak frame holding each ion's predicted envelope at a given height."""
    rows = []
    for formula, charge, height in ions:
        predicted_mz, predicted_intensity, _ = predict_isotopes(formula, charge)
        base = max(predicted_intensity)
        for one_mz, one_intensity in zip(predicted_mz, predicted_intensity):
            rows.append(
                {"mz": float(one_mz), "intensity": height * float(one_intensity) / base}
            )
    frame = pd.DataFrame(sorted(rows, key=lambda row: row["mz"]))
    frame.insert(0, "sample_peak_id", [f"peak{index}" for index in range(len(frame))])
    return frame


def _rows(profile: str = "BR", *ions: tuple[str, int, float]) -> list[dict]:
    peaks = _peaks(*(ions or (("Br", -1, 1e6),)))
    hits = claim_reagent_peaks(peaks, reagent_library_for(profile))
    return build_reagent_assignments(hits, peaks, "sample1", "run1")


class TestTheReagentRow:
    def test_it_names_the_ion_and_no_analyte(self):
        """The composition is known exactly, and it is the source's, not the
        sample's. An `assigned_formula` here would put a reagent cluster into
        every cross-sample formula vote it touches."""
        row = _rows()[0]

        assert row["role"] == ROLE_REAGENT
        assert row["source"] == SOURCE_REAGENT
        assert row["ion_formula"] == "Br"
        assert row["assigned_formula"] is None
        assert row["ionization_mechanism_id"] is None

    def test_its_tier_says_no_analyte_was_assigned(self):
        """And it is the tier the import path's own coherence rule requires of
        a row that names no formula, so the engine writes what it would accept.
        """
        row = _rows()[0]

        assert row["tier"] == TIER_UNASSIGNED
        assert row["tier"] in coherent_tiers(None, 0.6, 0.3)

    def test_it_records_how_far_off_the_peak_sat(self):
        """On a peak placed at the ion's own mass the error is ~0.013 ppm, the
        residual between IsoSpec's masses and the composition arithmetic this
        library computes its targets with. Recorded on every row, so a claim
        that matched far off its mass says so rather than passing silently."""
        row = _rows()[0]

        assert row["mz_error_ppm"] == pytest.approx(0.0, abs=0.1)

    def test_it_says_which_reagent_ion_claimed_the_peak(self):
        provenance = _rows()[0]["provenance"]["reagent"]

        assert provenance["ion"] == "[Br]-"
        assert provenance["kind"] == "cluster"
        assert provenance["mz"] == pytest.approx(78.9189, abs=5e-4)

    def test_every_row_lands_on_a_real_peak_of_the_sample(self):
        peaks = _peaks(("Br", -1, 1e6), ("Br2", -1, 3e5))
        hits = claim_reagent_peaks(peaks, reagent_library_for("BR"))
        rows = build_reagent_assignments(hits, peaks, "sample1", "run1")

        assert {row["sample_peak_id"] for row in rows} <= set(peaks["sample_peak_id"])
        assert len({row["peak_assignment_id"] for row in rows}) == len(rows)


class TestTheSatelliteRows:
    def test_a_satellite_names_its_isotopologue_and_its_predicted_share(self):
        satellites = [row for row in _rows() if row["isotope_label"]]

        assert [row["isotope_label"] for row in satellites] == ["81Br"]
        assert satellites[0]["provenance"]["reagent"]["predicted_relative"] > 0.9

    def test_a_satellite_carries_the_reagent_role_too(self):
        """The peak is the source's chemistry as much as its parent is, and G4
        counts it: the reference engine labels these reagent as well."""
        assert {row["role"] for row in _rows()} == {ROLE_REAGENT}

    def test_no_reagent_row_names_an_owner(self):
        """Owner linkage models one thing in this ledger - an isotopologue
        naming the M0 analyte it belongs to - and the import path enforces it,
        so the engine must not write a shape it would then refuse. The parent is
        recorded as provenance instead.
        """
        rows = _rows("BR", ("Br", -1, 1e6), ("Br2", -1, 3e5))

        assert all(row["owner_peak_assignment_id"] is None for row in rows)
        assert (
            owner_link_errors(
                [
                    SimpleNamespace(
                        sample_peak_id=row["sample_peak_id"],
                        owner_sample_peak_id=None,
                        role=row["role"],
                    )
                    for row in rows
                ]
            )
            == []
        )


class TestWhenThereIsNothingToClaim:
    def test_a_profile_with_no_reagent_writes_no_rows(self):
        assert _rows("ESI_POS") == []

    def test_an_empty_peak_frame_writes_no_rows(self):
        empty = pd.DataFrame({"sample_peak_id": [], "mz": [], "intensity": []})

        assert claim_reagent_peaks(empty, reagent_library_for("BR")) == []

    def test_a_spectrum_of_analytes_only_writes_no_rows(self):
        peaks = pd.DataFrame(
            {
                "sample_peak_id": ["p1", "p2"],
                "mz": np.array([200.12345, 301.54321]),
                "intensity": np.array([1e6, 5e5]),
            }
        )

        assert claim_reagent_peaks(peaks, reagent_library_for("BR")) == []
