"""
Known-answer tests for the match isotope persistence selection
(``api/controllers/match/lib/match_compute.py``).

``select_match_isotopes_to_persist`` decides which computed rows are stored:

- Every isotope with ``match_score > 0`` (a real match).
- One zero-score sentinel per ion whose isotopes ALL scored 0: the main
  isotope (highest ``relative_abundance``, ties broken by lowest ``mz``, then
  ``target_isotope_id``).
- Nothing else: zero-score isotopes of ions that matched are dropped (the
  ion-level evaluated marker already exists via the scoring rows).

Stored rows double as "this ion was evaluated for this sample" markers for
``fetch_sample_unmatched_target_isotopes``, so the invariant under test is:
every ion present in the input frame has at least one row in the output.
"""

import numpy as np
import pandas as pd

from mascope_backend.api.controllers.match.lib.match_compute import (
    select_match_isotopes_to_persist,
)
from mascope_backend.api.models.match.isotopes.match_isotopes_pydantic_model import (
    MatchIsotopeBase,
)


def make_isotope_row(
    target_ion_id: str,
    target_isotope_id: str,
    match_score: float,
    relative_abundance: float = 1.0,
    mz: float = 100.0,
) -> dict:
    """A minimal computed match isotope row for selection tests."""
    return {
        "target_ion_id": target_ion_id,
        "target_isotope_id": target_isotope_id,
        "match_score": match_score,
        "relative_abundance": relative_abundance,
        "mz": mz,
    }


class TestSelectMatchIsotopesToPersist:
    def test_scoring_rows_kept_and_zero_rows_of_matched_ions_dropped(self):
        # Ion with a scoring isotope needs no sentinel: its zero-score
        # isotopes are dropped, the scoring row already marks it evaluated.
        df = pd.DataFrame(
            [
                make_isotope_row("ion1", "isoA", match_score=0.9),
                make_isotope_row("ion1", "isoB", match_score=0.0),
                make_isotope_row("ion1", "isoC", match_score=0.4),
            ]
        )

        result = select_match_isotopes_to_persist(df)

        assert sorted(result["target_isotope_id"]) == ["isoA", "isoC"]

    def test_fully_unmatched_ion_keeps_main_isotope_sentinel(self):
        # All isotopes scored 0: exactly one sentinel survives - the main
        # isotope (highest relative_abundance).
        df = pd.DataFrame(
            [
                make_isotope_row(
                    "ion1", "isoA", match_score=0.0, relative_abundance=0.2
                ),
                make_isotope_row(
                    "ion1", "isoB", match_score=0.0, relative_abundance=1.0
                ),
                make_isotope_row(
                    "ion1", "isoC", match_score=0.0, relative_abundance=0.5
                ),
            ]
        )

        result = select_match_isotopes_to_persist(df)

        assert result["target_isotope_id"].tolist() == ["isoB"]
        assert result["match_score"].tolist() == [0.0]

    def test_sentinel_tie_breaks_on_mz_then_isotope_id(self):
        # Equal abundances: lowest mz wins; equal mz too: lowest isotope id.
        df = pd.DataFrame(
            [
                make_isotope_row(
                    "ion1", "isoB", match_score=0.0, relative_abundance=1.0, mz=200.0
                ),
                make_isotope_row(
                    "ion1", "isoA", match_score=0.0, relative_abundance=1.0, mz=100.0
                ),
                make_isotope_row(
                    "ion2", "isoD", match_score=0.0, relative_abundance=1.0, mz=300.0
                ),
                make_isotope_row(
                    "ion2", "isoC", match_score=0.0, relative_abundance=1.0, mz=300.0
                ),
            ]
        )

        result = select_match_isotopes_to_persist(df)

        assert sorted(result["target_isotope_id"]) == ["isoA", "isoC"]

    def test_mixed_ions_every_ion_keeps_at_least_one_row(self):
        # The evaluated-marker invariant: every input ion appears in the
        # output, matched ions via scoring rows, unmatched ions via sentinel.
        df = pd.DataFrame(
            [
                make_isotope_row("matched_ion", "isoA", match_score=0.7),
                make_isotope_row("matched_ion", "isoB", match_score=0.0),
                make_isotope_row(
                    "zero_ion", "isoC", match_score=0.0, relative_abundance=1.0
                ),
                make_isotope_row(
                    "zero_ion", "isoD", match_score=0.0, relative_abundance=0.1
                ),
            ]
        )

        result = select_match_isotopes_to_persist(df)

        assert set(result["target_ion_id"]) == set(df["target_ion_id"])
        assert sorted(result["target_isotope_id"]) == ["isoA", "isoC"]

    def test_all_ions_scoring_returns_only_scoring_rows(self):
        df = pd.DataFrame(
            [
                make_isotope_row("ion1", "isoA", match_score=0.9),
                make_isotope_row("ion2", "isoB", match_score=0.1),
            ]
        )

        result = select_match_isotopes_to_persist(df)

        pd.testing.assert_frame_equal(result, df)


class TestMatchIsotopeBaseSignalToNoise:
    """The persistence model carries signal_to_noise through the write path.

    Rows are built as ``MatchIsotopeBase(**row)`` from the compute frame's
    records, where "no SNR for this row" is NaN; the persisted representation
    of that is NULL/None (NaN is not JSON and must not reach the database).
    """

    BASE_ROW = {
        "match_isotope_id": "mi" * 16,
        "target_isotope_id": "ti" * 8,
        "sample_item_id": "si" * 8,
        "sample_peak_id": "peak-1",
        "sample_peak_mz": 181.07,
        "sample_peak_intensity": 1000.0,
        "sample_peak_intensity_relative": 1.0,
        "sample_peak_tof": 42.0,
        "match_abundance_error": 0.01,
        "match_mz_error": 0.2,
        "match_score": 0.9,
    }

    def test_real_value_survives_the_round_trip(self):
        model = MatchIsotopeBase(**{**self.BASE_ROW, "signal_to_noise": 37.5})
        assert model.model_dump()["signal_to_noise"] == 37.5

    def test_nan_is_persisted_as_none(self):
        model = MatchIsotopeBase(**{**self.BASE_ROW, "signal_to_noise": np.nan})
        assert model.model_dump()["signal_to_noise"] is None

    def test_absent_key_defaults_to_none(self):
        assert MatchIsotopeBase(**self.BASE_ROW).model_dump()["signal_to_noise"] is None
