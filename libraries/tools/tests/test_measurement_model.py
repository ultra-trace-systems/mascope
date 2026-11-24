"""The measurement model a run scores and caps at (``measurement_model``)."""

import dataclasses

import numpy as np
import pandas as pd
import pytest

from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance
from mascope_tools.composition.mass_accuracy import (
    MU_SOURCE_FITTED,
    MU_SOURCE_NONE,
    MU_SOURCE_REAGENT,
    PRED_SIGMA_PPM,
    MassTrend,
    ReagentOffset,
    SampleMassAccuracy,
    trend_width_floor_ppm,
    widen_sigma_ppm,
)
from mascope_tools.composition.measurement_model import (
    ANCHOR_COMMITS,
    ANCHOR_LIBRARY_IONS,
    ANCHOR_LIBRARY_LINES,
    ANCHOR_SOURCE_LINES,
    MODEL_V1,
    MODEL_V2,
    REFIT_BOUND_WIDTHS,
    MeasurementModel,
    refit_adopts,
)
from mascope_tools.composition.models import PatternScoring
from mascope_tools.composition.scoring import (
    pattern_scoring_for_model,
    pattern_scoring_for_sample,
    score_candidates,
)


ORBI_CLASS = 0.3

FITTED = SampleMassAccuracy(mu_ppm=0.2, sigma_ppm=0.3, anchors=40, anchor_ions=12)

TREND = MassTrend(
    intercept_ppm=0.1,
    offset_mda=-0.12,
    sigma_ppm=0.1,
    points=60,
    mz_lo=60.0,
    mz_hi=400.0,
)


class Params:
    mz_tolerance = 3.0
    isotope_abundance_threshold = 0.001


def v1(accuracy=FITTED, tolerance=None) -> MeasurementModel:
    return MeasurementModel.v1(accuracy, ORBI_CLASS, tolerance)


class TestV1:
    def test_is_stage_as_measurement_widened(self):
        model = v1()
        assert model.version == MODEL_V1
        assert model.measured
        assert model.scoring_mu_ppm == 0.2
        assert model.width_ppm == pytest.approx(widen_sigma_ppm(0.3))
        assert model.anchors[ANCHOR_LIBRARY_LINES] == 40
        assert model.anchors[ANCHOR_LIBRARY_IONS] == 12

    def test_a_thin_library_stands_in_and_is_not_measured(self):
        reagent = ReagentOffset(mu_ppm=-1.3, lines=5, beyond_width=True)
        model = v1(SampleMassAccuracy(anchors=3, reagent=reagent))
        assert model.mu_source == MU_SOURCE_REAGENT
        assert model.scoring_mu_ppm == -1.3
        assert model.width_ppm == pytest.approx(widen_sigma_ppm(ORBI_CLASS))
        # A stand-in centre is no measurement of the sample's own: no row is
        # capped for its distance from it.
        assert not model.measured
        assert model.z_of(5.0, 100.0) is None

    def test_scores_as_the_samples_pattern_scoring_did(self):
        tolerance = EnvelopeTolerance(centre=0.1)
        old = pattern_scoring_for_sample(FITTED, "orbi", Params(), None, tolerance)
        new = pattern_scoring_for_model(v1(tolerance=tolerance), Params())
        assert new.sigma_ppm == pytest.approx(old.sigma_ppm)
        assert new.mu_ppm == old.mu_ppm
        assert new.envelope_tolerance is tolerance
        assert new.centre is None

    def test_fit_sigma_widens_back_to_the_width(self):
        model = v1()
        assert widen_sigma_ppm(model.fit_sigma_ppm) == pytest.approx(model.width_ppm)


def refit(model=None, **overrides) -> MeasurementModel:
    fields = {
        "mu_ppm": -0.4,
        "sigma_ppm": 0.1,
        "commits": 200,
        "source_lines": 6,
    }
    fields.update(overrides)
    return (model or v1()).v2(**fields)


class TestV2:
    def test_is_floored_at_the_width_the_search_ranked_at(self):
        model = refit()
        assert model.version == MODEL_V2
        assert model.mu_source == MU_SOURCE_FITTED
        # Its own anchors spread 0.1 ppm; the rows it judges were ranked at
        # v1's width, which is the floor.
        assert widen_sigma_ppm(0.1) < v1().width_ppm
        assert model.width_ppm == pytest.approx(v1().width_ppm)
        assert model.anchors == {
            ANCHOR_COMMITS: 200,
            ANCHOR_SOURCE_LINES: 6,
            "source_capped": 0,
        }

    def test_a_wider_refit_judges_in_its_own_width(self):
        model = refit(sigma_ppm=0.9)
        assert model.width_ppm == pytest.approx(widen_sigma_ppm(0.9))

    def test_keeps_v1s_tolerance(self):
        tolerance = EnvelopeTolerance(centre=0.05)
        assert refit(v1(tolerance=tolerance)).envelope_tolerance is tolerance

    def test_unmeasured_without_a_centre(self):
        assert not refit(mu_ppm=None, sigma_ppm=None).measured


class TestCentre:
    def test_trend_is_held_at_its_coverage(self):
        model = refit(trend=TREND)
        assert model.centre_ppm(30.0) == pytest.approx(TREND.centre_ppm(60.0))
        assert model.centre_ppm(1000.0) == pytest.approx(TREND.centre_ppm(400.0))
        assert model.centre_ppm(100.0) == pytest.approx(0.1 - 1.2)
        # A row whose m/z is unknown reads the constant centre.
        assert model.centre_ppm(None) == -0.4

    def test_centres_agree_with_the_scalar(self):
        model = refit(trend=TREND)
        mz = np.array([30.0, 61.0, 100.0, 250.0, 900.0, np.nan])
        expected = [model.centre_ppm(m) for m in mz[:-1]] + [model.scoring_mu_ppm]
        assert model.centres_ppm(mz) == pytest.approx(expected)

    def test_farthest_centre_is_an_end_of_the_trend(self):
        model = refit(trend=TREND)
        assert model.max_abs_centre_ppm() == pytest.approx(abs(TREND.centre_ppm(60.0)))
        assert refit().max_abs_centre_ppm() == pytest.approx(0.4)


class TestDistance:
    def test_is_read_at_the_centre_and_width_a_fit_is_scored_at(self):
        model = refit(trend=TREND)
        mz, error = 150.0, 0.7
        assert model.z_of(error, mz) == pytest.approx(
            (error - model.centre_ppm(mz)) / model.width_ppm
        )
        scoring = pattern_scoring_for_model(model, Params())
        assert scoring.sigma_ppm == pytest.approx(model.width_ppm)
        assert float(scoring.centres_at(np.array([mz]))[0]) == pytest.approx(
            model.centre_ppm(mz)
        )

    def test_trend_floor_below_the_lowest_commit(self):
        model = refit(trend=TREND)
        mz = 40.0
        assert trend_width_floor_ppm(mz) > model.width_ppm
        assert model.z_of(1.0, mz) == pytest.approx(
            (1.0 - model.centre_ppm(mz)) / trend_width_floor_ppm(mz)
        )

    def test_widths_are_the_widths_z_of_reads(self):
        model = refit(trend=TREND)
        mz = np.array([30.0, 40.0, 61.0, 150.0, 900.0])
        widths = model.widths_ppm(mz)
        assert widths == pytest.approx(
            [(1.0 - model.centre_ppm(m)) / model.z_of(1.0, m) for m in mz]
        )
        # The floor passes the width at low m/z only.
        assert widths[0] == pytest.approx(trend_width_floor_ppm(30.0))
        assert widths[-1] == pytest.approx(model.width_ppm)

    def test_an_unknown_mz_keeps_the_width(self):
        model = refit(trend=TREND)
        assert model.widths_ppm([np.nan, 0.0, -5.0]) == pytest.approx(
            [model.width_ppm] * 3
        )
        assert model.z_of(1.0, 0.0) == pytest.approx(
            (1.0 - model.centre_ppm(0.0)) / model.width_ppm
        )

    def test_no_trend_no_floor(self):
        model = refit()
        assert model.widths_ppm([30.0, 400.0]) == pytest.approx([model.width_ppm] * 2)
        assert model.z_of(1.0, 30.0) == pytest.approx(
            (1.0 - model.scoring_mu_ppm) / model.width_ppm
        )

    def test_fit_sigmas_widen_back_to_the_widths(self):
        model = refit(trend=TREND)
        mz = np.array([30.0, 150.0])
        sigmas = model.fit_sigmas_ppm(mz)
        assert [widen_sigma_ppm(s) for s in sigmas] == pytest.approx(
            list(model.widths_ppm(mz))
        )
        assert sigmas[1] == pytest.approx(model.fit_sigma_ppm)

    def test_no_width_no_widths(self):
        model = dataclasses.replace(
            refit(trend=TREND),
            sigma_ppm=None,
            fallback_sigma_ppm=None,
            width_floor_ppm=None,
        )
        assert model.width_ppm is None
        assert model.widths_ppm([30.0]) is None
        assert model.fit_sigmas_ppm([30.0]) is None


class TestRefitAdopts:
    def test_an_unmeasured_refit_leaves_v1(self):
        adopted, record = refit_adopts(v1(), refit(mu_ppm=None, sigma_ppm=None))
        assert not adopted
        assert record == {"fitted": False, "adopted": False}

    def test_a_refit_inside_the_bound_leaves_v1(self):
        width = v1().width_ppm
        near = refit(mu_ppm=0.2 + 0.9 * REFIT_BOUND_WIDTHS * width, sigma_ppm=0.3)
        adopted, record = refit_adopts(v1(), near)
        assert not adopted
        assert record["fitted"] and not record["adopted"]

    def test_a_centre_beyond_the_bound_is_adopted(self):
        width = v1().width_ppm
        far = refit(mu_ppm=0.2 + 1.1 * REFIT_BOUND_WIDTHS * width, sigma_ppm=0.3)
        adopted, record = refit_adopts(v1(), far)
        assert adopted
        assert record["centre_moved_widths"] > REFIT_BOUND_WIDTHS

    def test_a_width_beyond_the_bound_is_adopted(self):
        adopted, record = refit_adopts(v1(), refit(mu_ppm=0.2, sigma_ppm=0.9))
        assert adopted
        assert record["centre_moved_widths"] == 0.0
        assert record["width_moved"] > REFIT_BOUND_WIDTHS

    def test_a_trend_is_compared_over_the_committed_range(self):
        # Constant parts agree; the trend bends away from v1 at low m/z only.
        trend = MassTrend(0.2, -0.02, 0.1, 60, 60.0, 400.0)
        model = refit(mu_ppm=0.2, sigma_ppm=0.3, trend=trend)
        assert not refit_adopts(v1(), model, [300.0, 400.0])[0]
        assert refit_adopts(v1(), model, [62.0, 300.0])[0]

    def test_a_measured_refit_replaces_a_stand_in(self):
        thin = v1(SampleMassAccuracy(anchors=3))
        # Inside the bound of the stand-in, and still taken: the stand-in is
        # no measurement to cap at.
        adopted, _ = refit_adopts(thin, refit(thin, mu_ppm=0.01, sigma_ppm=0.3))
        assert adopted


class TestRecord:
    def test_round_trips(self):
        tolerance_record = {
            "form": "centre_width",
            "level": "own",
            "centre": 0.04,
            "floor": 0.05,
            "lines": 30,
            "absent": 2,
            "detect_snr": 3.0,
        }
        model = refit(
            MeasurementModel.v1(
                SampleMassAccuracy(
                    mu_ppm=0.2,
                    sigma_ppm=0.3,
                    anchors=40,
                    anchor_ions=12,
                    reagent=ReagentOffset(mu_ppm=-0.9, lines=7),
                ),
                ORBI_CLASS,
                EnvelopeTolerance.from_record(tolerance_record),
            ),
            trend=TREND,
            trend_rows=80,
        )
        back = MeasurementModel.from_run_config(
            {
                "measurement_model": model.record(),
                "envelope_tolerance": tolerance_record,
            }
        )
        assert back.version == MODEL_V2
        assert back.width_ppm == pytest.approx(model.width_ppm, abs=1e-4)
        assert back.centre_ppm(100.0) == pytest.approx(
            model.centre_ppm(100.0), abs=1e-3
        )
        assert back.envelope_tolerance == model.envelope_tolerance
        assert back.reagent == model.reagent
        assert dict(back.anchors) == dict(model.anchors)
        assert back.trend_rows == 80
        assert back.measured

    def test_an_older_run_is_read_at_what_it_scored_at(self):
        legacy = {
            "pattern_scoring": {
                "sigma_ppm": round(widen_sigma_ppm(0.3), 4),
                "mu_ppm": 0.2,
                "sigma_source": "fitted",
                "mu_source": MU_SOURCE_FITTED,
                "fitted_anchors": 40,
                "fitted_anchor_ions": 12,
            },
            # The gate's calibration it capped at is not what it scored at.
            "mass_calibration": {"mu_ppm": -0.4, "sigma_ppm": 0.1},
        }
        model = MeasurementModel.from_run_config(legacy, ORBI_CLASS)
        assert model.version == MODEL_V1
        assert model.scoring_mu_ppm == 0.2
        assert model.sigma_ppm == pytest.approx(0.3, abs=1e-3)
        assert model.width_ppm == pytest.approx(widen_sigma_ppm(0.3), abs=1e-4)

    def test_an_older_run_at_the_class_width(self):
        legacy = {
            "pattern_scoring": {
                "sigma_ppm": round(widen_sigma_ppm(5.0), 4),
                "mu_ppm": 0.0,
                "sigma_source": "instrument_class",
                "mu_source": MU_SOURCE_NONE,
            }
        }
        model = MeasurementModel.from_run_config(legacy)
        assert model.sigma_ppm is None
        assert model.width_ppm == pytest.approx(widen_sigma_ppm(5.0), abs=1e-4)
        assert model.mu_ppm is None

    def test_a_config_without_either_records_nothing(self):
        assert MeasurementModel.from_run_config({}) is None
        assert MeasurementModel.from_run_config(None) is None
        # From before the offset was recorded.
        assert MeasurementModel.from_run_config({"pattern_scoring": {}}) is None


def _envelope_peaks(ion: str, shift_ppm: float) -> tuple[pd.DataFrame, float]:
    """A peak list holding the ion's envelope, every line ``shift_ppm`` off."""
    probe = score_candidates(
        pd.DataFrame({"ion": [ion]}), pd.DataFrame({"mz": [1.0], "intensity": [1.0]})
    )
    mz = np.asarray(probe["predicted_mz"].iloc[0], dtype=float)
    rel = np.asarray(probe["predicted_rel"].iloc[0], dtype=float)
    keep = rel >= 0.01
    peaks = pd.DataFrame(
        {
            "mz": mz[keep] * (1 + shift_ppm * 1e-6),
            "intensity": 1e6 * rel[keep],
            "signal_to_noise": 1e3 * rel[keep],
        }
    )
    return peaks, float(peaks["mz"].iloc[0])


class TestFinderCentre:
    def test_a_constant_centre_function_scores_as_the_constant(self):
        peaks, observed = _envelope_peaks("C8H8O2-", 1.0)
        candidates = pd.DataFrame({"ion": ["C8H8O2-"], "observed_mass": [observed]})
        constant = PatternScoring(sigma_ppm=0.6, mu_ppm=1.2)
        function = PatternScoring(
            sigma_ppm=0.6, mu_ppm=0.0, centre=lambda mz: np.full(np.shape(mz), 1.2)
        )
        a = score_candidates(candidates, peaks, constant)["fit_score"]
        b = score_candidates(candidates, peaks, function)["fit_score"]
        assert float(a.iloc[0]) > 0
        assert np.allclose(a.to_numpy(), b.to_numpy())

    def test_a_centre_on_the_lines_scores_them_as_centred(self):
        peaks, observed = _envelope_peaks("C8H8O2-", 1.0)
        candidates = pd.DataFrame({"ion": ["C8H8O2-"], "observed_mass": [observed]})
        at_zero = PatternScoring(sigma_ppm=0.6)
        on_lines = PatternScoring(
            sigma_ppm=0.6, centre=lambda mz: np.full(np.shape(mz), 1.0)
        )
        a = float(score_candidates(candidates, peaks, at_zero)["fit_score"].iloc[0])
        b = float(score_candidates(candidates, peaks, on_lines)["fit_score"].iloc[0])
        assert b > a


def test_prediction_spread_is_the_one_the_width_adds():
    # The width a model judges in is the fitted one widened by this, so the
    # score and the distance divide by one number.
    assert widen_sigma_ppm(0.0) == pytest.approx(PRED_SIGMA_PPM)
