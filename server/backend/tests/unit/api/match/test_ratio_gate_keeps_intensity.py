"""Tests: the isotope ratio gate takes a line's score, not its intensity (#2943).

Each matched isotope line is gated per file on its m/z error and its
abundance error. The m/z gate says whether the peak is the line at all, so a
line outside it loses both its score and its intensity. The ratio gate says
whether the line fits the ion's pattern in this file: a line outside it loses
its score, and so the category, but keeps its intensity, which the ion's
``sample_peak_intensity_sum`` and the batch timeseries count. Before, a
bromide ion whose 81Br line missed the ratio by a little read at half its
intensity in that file, and its series toggled 1x / 2x from file to file.

The gate lives twice: ``apply_match_params`` over a frame (the aggregation
and the peak-assignment engine) and ``_apply_match_params`` over one stored
row (the Match tab's isotope table). Both are pinned here.
"""

import pandas as pd
import pytest

from mascope_backend.api.controllers.match.lib.match_aggregate import (
    aggregate_match_ions,
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
