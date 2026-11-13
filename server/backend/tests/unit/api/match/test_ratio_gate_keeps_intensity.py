"""Tests: the isotope ratio gate takes a line's score, not its intensity (#2943).

Each matched isotope line is gated per file on its m/z error and its
abundance error. The m/z gate says whether the peak is the line at all, so a
line outside it loses both its score and its intensity. The ratio gate says
whether the line fits the ion's pattern in this file: a line outside it loses
its score, and so the category, but keeps its intensity, which the ion's
``sample_peak_intensity_sum`` and the batch timeseries count. Before, a
bromide ion whose 81Br line missed the ratio by a little read at half its
intensity in that file, and its series toggled 1x / 2x from file to file.

A miss of 100 % or more takes the intensity too (#3000): that peak is not
the line read noisily, and a batch match never stores such a line (its score
is 0), so keeping it live made the match tree count what the stored sums
leave out.

The gate lives twice: ``apply_match_params`` over a frame (the aggregation
and the peak-assignment engine) and ``_apply_match_params`` over one stored
row (the Match tab's isotope table). Both are pinned here.
"""

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.controllers.match.lib.match_aggregate import (
    aggregate_match_ions,
    reconstruct_full_isotope_frame,
)
from mascope_backend.api.controllers.match.lib.match_compute import (
    select_match_isotopes_to_persist,
)
from mascope_backend.api.new.match.params.lib import apply_match_params
from mascope_backend.api.new.match.records.isotope.service import _apply_match_params
from mascope_match.params import OrbiMatchParams


#: The Orbitrap defaults: 3 ppm, a ratio tolerance of 0.2, no intensity floor
#: below the lines here.
PARAMS = OrbiMatchParams(mz_tolerance=3, isotope_ratio_tolerance=0.2)


def _line(**overrides) -> dict:
    """One matched isotope line that passes every gate, unless overridden."""
    row = {
        "target_isotope_id": "iso",
        "match_mz_error": 0.5,
        "match_abundance_error": 0.05,
        "sample_peak_intensity": 1.0e6,
        "relative_abundance": 0.493,
        "match_score": 0.9,
        "instrument": "orbi-a",
        "instrument_type": "orbi",
        "filter_params": None,
    }
    row.update(overrides)
    return row


class TestApplyMatchParams:
    def test_a_line_outside_the_ratio_tolerance_keeps_its_intensity(self):
        gated = apply_match_params(
            pd.DataFrame([_line(match_abundance_error=0.3)]), PARAMS
        ).iloc[0]
        assert gated["match_score"] == 0
        assert gated["match_category"] == 0
        assert gated["sample_peak_intensity"] == pytest.approx(1.0e6)

    def test_an_undershoot_keeps_it_too(self):
        gated = apply_match_params(
            pd.DataFrame([_line(match_abundance_error=-0.3)]), PARAMS
        ).iloc[0]
        assert gated["match_score"] == 0
        assert gated["sample_peak_intensity"] == pytest.approx(1.0e6)

    @pytest.mark.parametrize("error", [1.0, 1.5, -1.0])
    def test_a_miss_of_100_percent_or_more_loses_both(self, error):
        # 1.5: a peak at 2.5x its expected share. -1.0: the ratio read as 0, as
        # for a minor line of an ion whose main line has no peak.
        gated = apply_match_params(
            pd.DataFrame([_line(match_abundance_error=error)]), PARAMS
        ).iloc[0]
        assert gated["match_score"] == 0
        assert gated["match_category"] == 0
        assert gated["sample_peak_intensity"] == 0

    def test_a_miss_just_under_100_percent_keeps_its_intensity(self):
        gated = apply_match_params(
            pd.DataFrame([_line(match_abundance_error=-0.99)]), PARAMS
        ).iloc[0]
        assert gated["match_score"] == 0
        assert gated["sample_peak_intensity"] == pytest.approx(1.0e6)

    def test_a_line_outside_the_mz_tolerance_loses_both(self):
        gated = apply_match_params(
            pd.DataFrame([_line(match_mz_error=4.0)]), PARAMS
        ).iloc[0]
        assert gated["match_score"] == 0
        assert gated["match_category"] == 0
        assert gated["sample_peak_intensity"] == 0

    def test_a_line_outside_both_loses_both(self):
        gated = apply_match_params(
            pd.DataFrame([_line(match_mz_error=-4.0, match_abundance_error=0.5)]),
            PARAMS,
        ).iloc[0]
        assert gated["match_score"] == 0
        assert gated["sample_peak_intensity"] == 0

    def test_a_line_under_the_intensity_floor_keeps_its_intensity(self):
        params = OrbiMatchParams(peak_min_intensity=2.0e6)
        gated = apply_match_params(pd.DataFrame([_line()]), params).iloc[0]
        assert gated["match_score"] == 0
        assert gated["sample_peak_intensity"] == pytest.approx(1.0e6)

    def test_a_line_inside_every_gate_keeps_both(self):
        gated = apply_match_params(pd.DataFrame([_line()]), PARAMS).iloc[0]
        assert gated["match_score"] == pytest.approx(0.9)
        assert gated["match_category"] == 2
        assert gated["sample_peak_intensity"] == pytest.approx(1.0e6)

    def test_an_ions_own_ratio_tolerance_decides_without_params(self):
        # Without explicit params each row reads its ion's filter_params for
        # its instrument; a tolerance of 0.5 there lets 0.3 through, the
        # default 0.2 of the second row does not, and both keep the intensity.
        own = {"orbi-a": PARAMS.model_dump() | {"isotope_ratio_tolerance": 0.5}}
        gated = apply_match_params(
            pd.DataFrame(
                [
                    _line(match_abundance_error=0.3, filter_params=own),
                    _line(match_abundance_error=0.3),
                ]
            )
        )
        assert list(gated["match_score"]) == [pytest.approx(0.9), 0]
        assert list(gated["sample_peak_intensity"]) == [
            pytest.approx(1.0e6),
            pytest.approx(1.0e6),
        ]


class TestApplyMatchParamsOneRow:
    """``_apply_match_params``: the same gate on one stored row."""

    @staticmethod
    def _gate(**overrides) -> dict:
        row = _line(**overrides)
        isotope = {
            key: row.pop(key)
            for key in (
                "relative_abundance",
                "instrument",
                "instrument_type",
                "filter_params",
            )
        }
        return _apply_match_params(isotope, row, PARAMS)

    def test_a_line_outside_the_ratio_tolerance_keeps_its_intensity(self):
        gated = self._gate(match_abundance_error=0.3)
        assert gated["match_score"] == 0
        assert gated["match_category"] == 0
        assert gated["sample_peak_intensity"] == pytest.approx(1.0e6)

    @pytest.mark.parametrize("error", [1.0, 1.5, -1.0])
    def test_a_miss_of_100_percent_or_more_loses_both(self, error):
        gated = self._gate(match_abundance_error=error)
        assert gated["match_score"] == 0
        assert gated["match_category"] == 0
        assert gated["sample_peak_intensity"] == 0

    def test_a_miss_just_under_100_percent_keeps_its_intensity(self):
        gated = self._gate(match_abundance_error=-0.99)
        assert gated["match_score"] == 0
        assert gated["sample_peak_intensity"] == pytest.approx(1.0e6)

    def test_a_line_outside_the_mz_tolerance_loses_both(self):
        gated = self._gate(match_mz_error=4.0)
        assert gated["match_score"] == 0
        assert gated["match_category"] == 0
        assert gated["sample_peak_intensity"] == 0

    def test_a_line_inside_every_gate_keeps_both(self):
        gated = self._gate()
        assert gated["match_score"] == pytest.approx(0.9)
        assert gated["match_category"] == 2
        assert gated["sample_peak_intensity"] == pytest.approx(1.0e6)


def _bromide_ion(abundance_error_81: float) -> pd.DataFrame:
    """One bromide ion's two lines, as the aggregation reads them."""
    common = {
        "sample_item_id": "s1",
        "sample_item_name": "sample 1",
        "sample_item_type": "ANALYSIS",
        "filename": "f.raw",
        "instrument": "orbi-a",
        "instrument_type": "orbi",
        "target_ion_id": "ion",
        "target_ion_formula": "CH2O2Br-",
        "ionization_mechanism": "[M+Br]-",
        "target_compound_id": "comp",
        "target_compound_formula": "CH2O2",
        "target_compound_name": "Formic acid",
        "target_collection_id": "coll",
        "target_collection_name": "coll",
        "target_collection_description": "",
        "target_collection_type": "TARGETS",
        "filter_params": None,
        "match_mz_error": 0.4,
    }
    return pd.DataFrame(
        [
            common
            | {
                "target_isotope_id": "79Br",
                "relative_abundance": 0.507,
                "match_abundance_error": 0.0,
                "sample_peak_intensity": 1.0e6,
                "match_score": 0.95,
            },
            common
            | {
                "target_isotope_id": "81Br",
                "relative_abundance": 0.493,
                "match_abundance_error": abundance_error_81,
                "sample_peak_intensity": 0.97e6 * (1 + abundance_error_81),
                "match_score": 0.95 * (1 - abs(abundance_error_81)),
            },
        ]
    )


class TestTheIonSum:
    """Through the aggregation that writes ``match_ion``."""

    @pytest.mark.asyncio
    async def test_a_ratio_miss_keeps_both_lines_in_the_sum(self):
        frame = _bromide_ion(0.3)
        _, ions = await aggregate_match_ions(apply_match_params(frame, PARAMS), PARAMS)
        ion = ions.iloc[0]
        assert ion["sample_peak_intensity_sum"] == pytest.approx(
            frame["sample_peak_intensity"].sum()
        )
        # The score still takes the miss: the 81Br line counts nothing
        assert ion["match_score"] == pytest.approx(0.507 * 0.95)
        assert ion["match_category"] == 0

    @pytest.mark.asyncio
    async def test_a_file_that_misses_and_one_that_fits_read_alike(self):
        # The series the issue saw: the same ion in two files, its 81Br line
        # a little over the tolerance in one and inside it in the other. The
        # sums now differ by the line's own excess, not by half the ion.
        sums = []
        for error in (0.25, 0.15):
            _, ions = await aggregate_match_ions(
                apply_match_params(_bromide_ion(error), PARAMS), PARAMS
            )
            sums.append(ions.iloc[0]["sample_peak_intensity_sum"])
        assert sums[0] / sums[1] == pytest.approx(
            (1.0e6 + 0.97e6 * 1.25) / (1.0e6 + 0.97e6 * 1.15)
        )
        assert sums[0] / sums[1] < 1.1

    @pytest.mark.asyncio
    async def test_a_mass_miss_still_drops_the_line_from_the_sum(self):
        frame = _bromide_ion(0.0)
        frame.loc[1, "match_mz_error"] = 5.0
        _, ions = await aggregate_match_ions(apply_match_params(frame, PARAMS), PARAMS)
        assert ions.iloc[0]["sample_peak_intensity_sum"] == pytest.approx(1.0e6)


class TestStoredIonSumMatchesLive:
    """The stored ion sum is read from the persisted rows, with the dropped
    ones rebuilt as unmatched placeholders; the live one from every computed
    row. Both go through apply_match_params, so they agree only when no row a
    batch match drops keeps an intensity on read (#3000)."""

    @staticmethod
    def _computed() -> pd.DataFrame:
        # ion1: a main line, a minor line on its mass at 2.5x its expected
        # share (abundance error 1.5) and a minor line within the tolerances.
        # ion2: a main line with no peak, so its minor line's ratio reads 0
        # (abundance error -1) though the line has a peak on its mass. Values
        # and raw scores as compute_match_isotopes gives them.
        rows = [
            ("ion1", "iA", 1.0, 100.0, 8000.0, 0.5, 0.0),
            ("ion1", "iB", 0.075, 101.0, 1500.0, 3.0, 1.5),
            ("ion1", "iC", 0.0118, 102.0, 940.0, -1.0, 0.1),
            ("ion2", "jA", 1.0, 200.0, 0.0, 0.0, -1.0),
            ("ion2", "jB", 0.3, 201.0, 700.0, 1.0, -1.0),
        ]
        records = []
        for ion, isotope, abundance, mz, intensity, mz_error, error in rows:
            records.append(
                {
                    "sample_item_id": "s1",
                    "target_isotope_id": isotope,
                    "target_ion_id": ion,
                    "relative_abundance": abundance,
                    "mz": mz,
                    "sample_peak_intensity": intensity,
                    "sample_peak_mz": mz * (1 + mz_error * 1e-6),
                    "sample_peak_tof": -1.0,
                    "match_mz_error": mz_error,
                    "match_abundance_error": error,
                    "match_score": (1.0 - min(1.0, abs(error)))
                    * max(0.0, 1.0 - 1e-2 * abs(mz_error)),
                    "signal_to_noise": np.nan,
                    "ratio_scan_count": np.nan,
                }
            )
        return pd.DataFrame(records)

    @staticmethod
    def _ion_sums(df: pd.DataFrame) -> dict[str, float]:
        gated = apply_match_params(df, OrbiMatchParams())
        return gated.groupby("target_ion_id")["sample_peak_intensity"].sum().to_dict()

    def test_the_stored_sums_equal_the_live_ones(self):
        computed = self._computed()
        persisted = select_match_isotopes_to_persist(computed)
        # Neither on-mass line with a 100 % miss is stored
        assert set(persisted["target_isotope_id"]) == {"iA", "iC", "jA"}

        targets = computed[
            ["target_isotope_id", "target_ion_id", "relative_abundance", "mz"]
        ].assign(resolution="HIGH", filter_params=None)
        samples = pd.DataFrame(
            {
                "sample_item_id": ["s1"],
                "instrument": ["orbi"],
                "filename": ["orbi_2024.01.01_10h00m00s.h5"],
            }
        )
        stored = reconstruct_full_isotope_frame(
            persisted.drop(columns=["target_ion_id", "relative_abundance", "mz"]),
            samples,
            targets,
        ).merge(
            targets[["target_isotope_id", "target_ion_id", "relative_abundance"]],
            on="target_isotope_id",
        )
        live = self._ion_sums(computed.assign(instrument="orbi", filter_params=None))

        assert live == {"ion1": pytest.approx(8940.0), "ion2": 0.0}
        assert self._ion_sums(stored) == {
            ion: pytest.approx(total) for ion, total in live.items()
        }
