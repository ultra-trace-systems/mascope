"""Which reading of a sample peak the calibration keeps, and how it is scored.

Several ions of a calibration collection can match one peak: the main line of
one calibrant is often a minor isotope line of another. The peak gives the fit
one point and is judged against the abundance floor by the reading kept, so
that reading has to be the one that makes the peak a main line - and it must
not turn on the ion ids, which every database generates for itself.

The collection used here is a labelled reagent beside its unlabelled compound:
each ion's main line is the other's minor one.

The database and the sample file are not involved: the peaks the handler would
load are handed to it.
"""

from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from calibration_test_support import get_test_calibration_handler

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    BaseCalibrationHandler,
)


UNLABELLED_MZ = 61.98837
LABELLED_MZ = 62.98540

#: Ion ids in both orders: whichever ion a database happens to number first.
ID_ORDERS = [("ion-a", "ion-z"), ("ion-z", "ion-a")]


def _labelled_pair(labelled_ion_id: str, unlabelled_ion_id: str) -> pd.DataFrame:
    """Isotopes of a labelled reagent ion and of its unlabelled counterpart.

    Each ion carries both lines: its own main line, and the other's as the
    share of the other nitrogen isotope it holds.
    """
    return pd.DataFrame(
        [
            (labelled_ion_id, "[15N]O3-", LABELLED_MZ, 0.973),
            (labelled_ion_id, "NO3-", UNLABELLED_MZ, 0.020),
            (unlabelled_ion_id, "NO3-", UNLABELLED_MZ, 0.989),
            (unlabelled_ion_id, "[15N]O3-", LABELLED_MZ, 0.0036),
        ],
        columns=["target_ion_id", "target_isotope_formula", "mz", "relative_abundance"],
    )


def _peaks(mz_and_height: list[tuple[float, float]]) -> xr.DataArray:
    """Peak heights over three scans, as ``_load_and_filter_peaks`` returns them."""
    mzs = np.array([mz for mz, _ in mz_and_height])
    heights = np.array([height for _, height in mz_and_height])
    return xr.DataArray(
        np.repeat(heights[:, np.newaxis], 3, axis=1),
        dims=("mz", "time"),
        coords={"mz": mzs, "tof": ("mz", np.zeros(mzs.size)), "time": np.arange(3.0)},
    )


def _handler(peaks: xr.DataArray):
    handler = get_test_calibration_handler("orbitrap", "-")
    handler._load_and_filter_peaks = AsyncMock(return_value=peaks)
    return handler


def _reading(**values) -> dict:
    """One reading of a peak, with what ``_one_reading_per_peak`` sorts on."""
    return {
        "sample_peak_mz": 100.0,
        "target_ion_id": "ion-a",
        "target_isotope_formula": "C6H13O6+",
        "mz": 100.0,
        "relative_abundance": 1.0,
        "match_mz_error": 0.0,
        **values,
    }


class TestOneReadingPerPeak:
    """``_one_reading_per_peak`` keeps the reading that makes a peak a main line."""

    @pytest.mark.parametrize("main_line_ion, minor_line_ion", ID_ORDERS)
    def test_the_main_line_reading_is_kept_whichever_id_sorts_first(
        self, main_line_ion, minor_line_ion
    ):
        readings = pd.DataFrame(
            [
                _reading(target_ion_id=minor_line_ion, relative_abundance=0.0036),
                _reading(target_ion_id=main_line_ion, relative_abundance=0.973),
            ]
        )

        kept = BaseCalibrationHandler._one_reading_per_peak(readings)

        assert kept["target_ion_id"].tolist() == [main_line_ion]
        assert kept["relative_abundance"].tolist() == [0.973]

    @pytest.mark.parametrize("near_ion, far_ion", ID_ORDERS)
    def test_readings_of_equal_abundance_keep_the_nearer_target(
        self, near_ion, far_ion
    ):
        readings = pd.DataFrame(
            [
                _reading(
                    target_ion_id=far_ion,
                    target_isotope_formula="C5H9O7+",
                    match_mz_error=-30.0,
                ),
                _reading(
                    target_ion_id=near_ion,
                    target_isotope_formula="C6H13O6+",
                    match_mz_error=2.0,
                ),
            ]
        )

        kept = BaseCalibrationHandler._one_reading_per_peak(readings)

        assert kept["target_ion_id"].tolist() == [near_ion]

    @pytest.mark.parametrize("first_ion, second_ion", ID_ORDERS)
    def test_readings_level_in_abundance_and_error_are_told_apart_by_formula(
        self, first_ion, second_ion
    ):
        """Two targets as far from the peak on either side: not by the id."""
        readings = pd.DataFrame(
            [
                _reading(
                    target_ion_id=first_ion,
                    target_isotope_formula="C6H13O6+",
                    mz=100.0003,
                    match_mz_error=-3.0,
                ),
                _reading(
                    target_ion_id=second_ion,
                    target_isotope_formula="C5H9O7+",
                    mz=99.9997,
                    match_mz_error=3.0,
                ),
            ]
        )

        kept = BaseCalibrationHandler._one_reading_per_peak(readings)

        assert kept["target_isotope_formula"].tolist() == ["C5H9O7+"]

    @pytest.mark.parametrize("first_ion, second_ion", ID_ORDERS)
    def test_the_same_line_through_two_ions_gives_the_fit_one_point(
        self, first_ion, second_ion
    ):
        """An ion reached through two mechanisms: the same point either way."""
        readings = pd.DataFrame(
            [_reading(target_ion_id=first_ion), _reading(target_ion_id=second_ion)]
        )

        kept = BaseCalibrationHandler._one_reading_per_peak(readings)

        assert len(kept) == 1
        assert kept.loc[0, ["mz", "sample_peak_mz", "relative_abundance"]].tolist() == [
            100.0,
            100.0,
            1.0,
        ]

    def test_every_peak_keeps_one_row_in_mz_order(self):
        readings = pd.DataFrame(
            [
                _reading(sample_peak_mz=300.0, mz=300.0),
                _reading(sample_peak_mz=100.0, relative_abundance=0.2),
                _reading(sample_peak_mz=200.0, mz=200.0),
                _reading(sample_peak_mz=100.0, relative_abundance=0.9),
            ]
        )

        kept = BaseCalibrationHandler._one_reading_per_peak(readings)

        assert kept["sample_peak_mz"].tolist() == [100.0, 200.0, 300.0]
        assert kept["relative_abundance"].tolist() == [0.9, 1.0, 1.0]
        assert list(kept.columns) == list(readings.columns)
        assert kept.index.tolist() == [0, 1, 2]


class TestMatchCalibrationCompounds:
    """The matching step, from the collection's isotopes to the calibrants."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("labelled_ion, unlabelled_ion", ID_ORDERS)
    async def test_both_main_lines_are_calibrants_whichever_id_sorts_first(
        self, labelled_ion, unlabelled_ion
    ):
        """Neither main line is lost for being the other ion's minor line."""
        handler = _handler(_peaks([(UNLABELLED_MZ, 1.5e5), (LABELLED_MZ, 1.2e7)]))

        match_df, good_matches_df = await handler._match_calibration_compounds(
            _labelled_pair(labelled_ion, unlabelled_ion)
        )

        assert match_df["sample_peak_mz"].tolist() == [UNLABELLED_MZ, LABELLED_MZ]
        assert good_matches_df["target_isotope_formula"].tolist() == [
            "NO3-",
            "[15N]O3-",
        ]
        assert good_matches_df["target_ion_id"].tolist() == [
            unlabelled_ion,
            labelled_ion,
        ]
        assert good_matches_df["relative_abundance"].tolist() == [0.989, 0.973]

    @pytest.mark.asyncio
    async def test_a_minor_line_no_ion_reads_as_main_is_not_a_calibrant(self):
        """The floor still applies: only a main-line reading rescues a peak."""
        isotopes = _labelled_pair("ion-a", "ion-z")
        isotopes = isotopes[isotopes["target_ion_id"] == "ion-a"]
        handler = _handler(_peaks([(UNLABELLED_MZ, 1.5e5), (LABELLED_MZ, 1.2e7)]))

        _, good_matches_df = await handler._match_calibration_compounds(isotopes)

        assert good_matches_df["target_isotope_formula"].tolist() == ["[15N]O3-"]

    @pytest.mark.asyncio
    async def test_isotopes_are_scored_against_their_own_ion_past_an_unmatched_one(
        self,
    ):
        """An isotope that found no peak does not shift the rows after it.

        Every peak sits on its target and holds its expected share of its
        ion's main line, so every matched isotope scores 1.
        """
        isotopes = pd.DataFrame(
            [
                ("ion-a", "C6H13O6+", 100.0, 0.9),
                ("ion-a", "[13C]C5H13O6+", 101.0, 0.1),
                ("ion-b", "C12H25O12+", 200.0, 0.8),
                ("ion-b", "[13C]C11H25O12+", 201.0, 0.2),
            ],
            columns=[
                "target_ion_id",
                "target_isotope_formula",
                "mz",
                "relative_abundance",
            ],
        )
        # No peak for the second isotope of the first ion.
        handler = _handler(_peaks([(100.0, 900.0), (200.0, 800.0), (201.0, 200.0)]))

        match_df, _ = await handler._match_calibration_compounds(isotopes)

        assert match_df["mz"].tolist() == [100.0, 200.0, 201.0]
        assert match_df["match_abundance_error"].tolist() == pytest.approx(
            [0.0, 0.0, 0.0], abs=1e-12
        )
        assert match_df["match_score"].tolist() == pytest.approx(
            [1.0, 1.0, 1.0], abs=1e-12
        )
