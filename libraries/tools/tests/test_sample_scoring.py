"""What a sample is scored at, derived in one place.

Two engines read a sample: Mascope's own and one outside it reading through
the SDK. What the sample is scored at is derived by the library
(``pattern_scoring_for_sample``), so the two cannot order its rules
differently. These pin each rule, in the order an engine reads them.
"""

import math
from types import SimpleNamespace

import pytest

from mascope_tools.composition.mass_accuracy import (
    PRED_SIGMA_PPM,
    ReagentOffset,
    SampleMassAccuracy,
)
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM
from mascope_tools.composition.scoring import pattern_scoring_for_sample


def _params(tolerance: float = 5.0, floor: float = 1e-5) -> SimpleNamespace:
    return SimpleNamespace(mz_tolerance=tolerance, isotope_abundance_threshold=floor)


class TestTheSampleIsScoredAtWhatWasMeasuredOfIt:
    """The derivation, rule by rule, in the order an engine reads it."""

    def test_a_fitted_width_is_widened_as_every_width_is(self):
        scoring = pattern_scoring_for_sample(
            SampleMassAccuracy(-0.24, 0.30, 14), "orbi", _params()
        )

        assert scoring.sigma_ppm == pytest.approx(math.hypot(0.30, PRED_SIGMA_PPM))
        assert scoring.mu_ppm == pytest.approx(-0.24)

    @pytest.mark.parametrize("instrument", ["orbi", "tof"])
    def test_the_instrument_class_stands_in_for_an_unmeasured_width(self, instrument):
        scoring = pattern_scoring_for_sample(
            SampleMassAccuracy(), instrument, _params()
        )

        assert scoring.sigma_ppm == pytest.approx(
            math.hypot(INSTRUMENT_FALLBACK_SIGMA_PPM[instrument], PRED_SIGMA_PPM)
        )
        assert scoring.mu_ppm == 0.0

    def test_the_reagent_lines_stand_in_for_an_unmeasured_offset(self):
        reading = ReagentOffset(mu_ppm=-1.2555, lines=7, beyond_width=True)

        scoring = pattern_scoring_for_sample(
            SampleMassAccuracy(None, None, 7, reading), "orbi", _params()
        )

        assert scoring.mu_ppm == pytest.approx(-1.2555)

    def test_reagent_lines_inside_the_width_leave_the_sample_uncorrected(self):
        reading = ReagentOffset(mu_ppm=0.30, lines=9, beyond_width=False)

        scoring = pattern_scoring_for_sample(
            SampleMassAccuracy(None, None, 2, reading), "orbi", _params()
        )

        assert scoring.mu_ppm == 0.0

    def test_the_window_and_the_floor_are_the_match_parameters(self):
        scoring = pattern_scoring_for_sample(
            SampleMassAccuracy(), "tof", _params(tolerance=15, floor=1e-4)
        )

        assert scoring.mz_tolerance_ppm == 15.0
        assert scoring.abundance_floor == 1e-4

    def test_the_files_resolving_power_rides_along(self):
        def resolution(mz):
            return 8000.0

        scoring = pattern_scoring_for_sample(
            SampleMassAccuracy(), "tof", _params(), resolution
        )

        assert scoring.resolution is resolution
        assert (
            pattern_scoring_for_sample(
                SampleMassAccuracy(), "tof", _params()
            ).resolution
            is None
        )
