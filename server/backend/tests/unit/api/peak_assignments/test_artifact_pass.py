"""What the artifact pre-pass claims, and what it writes into the ledger.

The sidelobe rule itself is ``mascope_tools.alignment.utils.flag_satellite_peaks``
and is tested there. What matters here is when the pass is allowed to run at all
and the shape of the row it produces: an artifact row has to stay out of the
analyte ledger as firmly as a reagent row does, and it has to say less than one,
because a sidelobe is not an ion.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd

from mascope_backend.api.new.peak_assignments.artifact_pass import (
    ARTIFACT_KIND_SIDELOBE,
    build_artifact_assignments,
    claim_artifact_peaks,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ARTIFACT,
    SOURCE_ARTIFACT,
)
from mascope_backend.api.new.peak_assignments.import_validation import (
    coherent_tiers,
    owner_link_errors,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED


def _ringing_spectrum() -> pd.DataFrame:
    """One very intense centroid with a mirror pair of sidelobes around it.

    Mirror symmetry is what the flag needs beyond a few ppm from the base, and
    it is what a real FT sidelobe pair has: matched offsets, a small fraction of
    the base peak's height each.
    """
    base_mz, base_intensity = 400.0, 1e8
    offsets_ppm = [-30.0, 30.0, -55.0, 55.0]
    rows = [{"mz": base_mz, "intensity": base_intensity}]
    for index, offset in enumerate(offsets_ppm):
        rows.append(
            {
                "mz": base_mz * (1 + offset * 1e-6),
                "intensity": base_intensity * (2e-3 if index % 2 == 0 else 1.5e-3),
            }
        )
    # A well-separated ordinary peak, to be left alone.
    rows.append({"mz": 500.0, "intensity": 4e5})
    frame = pd.DataFrame(sorted(rows, key=lambda row: row["mz"]))
    frame.insert(0, "sample_peak_id", [f"peak{index}" for index in range(len(frame))])
    return frame


class TestWhenThePassRuns:
    def test_it_claims_the_ringing_around_an_intense_centroid(self):
        claimed = claim_artifact_peaks(_ringing_spectrum(), "orbi")
        assert len(claimed) == 4
        assert 400.0 not in set(claimed["mz"])
        assert 500.0 not in set(claimed["mz"])

    def test_a_tof_sample_claims_nothing(self):
        # Sidelobes are a property of the Fourier transform. The peak detector's
        # TOF path declines to flag them for that reason, and running an FT
        # heuristic over TOF peaks would claim peaks on a mechanism that is not
        # there - so the two paths make the same call rather than disagreeing.
        assert claim_artifact_peaks(_ringing_spectrum(), "tof").empty

    def test_an_unknown_instrument_claims_nothing(self):
        # A sample whose class the filename does not say, on the ingest fold.
        # Standing down is the conservative reading: a pass that cannot say what
        # made the spectrum cannot say a peak is an artifact of how it was made.
        assert claim_artifact_peaks(_ringing_spectrum(), None).empty

    def test_an_empty_peak_list_claims_nothing(self):
        empty = pd.DataFrame({"sample_peak_id": [], "mz": [], "intensity": []})
        assert claim_artifact_peaks(empty, "orbi").empty

    def test_an_ordinary_spectrum_claims_nothing(self):
        # Nothing intense enough to ring, and no mirror pairs: the flag has to
        # stay silent, or every crowded spectrum would lose peaks to it.
        rng = np.random.default_rng(7)
        frame = pd.DataFrame(
            {
                "sample_peak_id": [f"p{index}" for index in range(60)],
                "mz": np.linspace(200.0, 800.0, 60),
                "intensity": rng.uniform(1e4, 5e5, 60),
            }
        )
        assert claim_artifact_peaks(frame, "orbi").empty


class TestTheArtifactRow:
    def _rows(self) -> list[dict]:
        peaks = _ringing_spectrum()
        return build_artifact_assignments(
            claim_artifact_peaks(peaks, "orbi"), "sample1", "run1"
        )

    def test_it_carries_the_artifact_role_and_source(self):
        rows = self._rows()
        assert rows
        assert {row["role"] for row in rows} == {ROLE_ARTIFACT}
        assert {row["source"] for row in rows} == {SOURCE_ARTIFACT}

    def test_it_names_no_analyte(self):
        # The whole point of the role: a row with no assigned formula votes on
        # no cross-sample consensus and weighs on no tier.
        assert all(row["assigned_formula"] is None for row in self._rows())

    def test_it_names_no_ion_either(self):
        # Where it differs from a reagent row. A reagent cluster is an ion whose
        # formula is known exactly; a sidelobe is not an ion at all, and naming
        # one would invent a species to explain a detector's response.
        assert all(row["ion_formula"] is None for row in self._rows())

    def test_the_tier_says_nothing_was_assigned(self):
        rows = self._rows()
        assert {row["tier"] for row in rows} == {TIER_UNASSIGNED}
        # ...and the import validator agrees that this is what a formula-less
        # row may carry, so an exported run of these can be published back.
        for row in rows:
            assert row["tier"] in coherent_tiers(None, 0.8, 0.7)

    def test_no_artifact_row_names_an_owner(self):
        # Owner linkage models one thing: an isotopologue naming the M0 it
        # belongs to. Checked through the validator itself rather than by
        # asserting the field, so the row is held to the rule the import path
        # enforces.
        rows = self._rows()
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

    def test_the_row_says_which_rule_claimed_it(self):
        assert all(
            row["provenance"]["artifact"]["kind"] == ARTIFACT_KIND_SIDELOBE
            for row in self._rows()
        )

    def test_the_row_carries_the_peak_it_claimed(self):
        peaks = _ringing_spectrum()
        claimed = claim_artifact_peaks(peaks, "orbi")
        rows = build_artifact_assignments(claimed, "sample1", "run1")
        by_id = dict(zip(peaks["sample_peak_id"], peaks["mz"]))
        for row in rows:
            assert row["sample_peak_mz"] == by_id[row["sample_peak_id"]]
