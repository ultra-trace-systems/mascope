"""Co-variation of a neutral's channels over a batch (step 4.2 of the assignment plan)."""

import math

import numpy as np
import pytest

from mascope_tools.composition.coherence import (
    CO_VARYING_R,
    FLAT_SPREAD,
    MIN_COMMON_MODE_TRACES,
    MIN_SPECTRA,
    PERSISTENT_SHARE,
    REASON_FEW_SPECTRA,
    REASON_FLAT,
    VERDICT_CO_VARYING,
    VERDICT_NOT_CO_VARYING,
    VERDICT_UNTESTABLE,
    CoVariation,
    batch_residuals,
    by_strength,
    read_co_variation,
    strongest,
)


SAMPLES = [f"s{i:02d}" for i in range(24)]


def trace(values, samples=SAMPLES):
    """A trace in intensity from log10 values, None for a gap."""
    return {s: 10.0**v for s, v in zip(samples, values) if v is not None}


def wave(n=24, period=7.0, phase=0.0, amplitude=0.3):
    return [amplitude * math.sin(2 * math.pi * i / period + phase) for i in range(n)]


class TestTheThresholds:
    def test_the_plan_s_line(self):
        assert CO_VARYING_R == 0.6

    def test_the_measured_floors(self):
        assert MIN_SPECTRA == 10
        assert FLAT_SPREAD == 0.02
        assert PERSISTENT_SHARE == 0.5
        assert MIN_COMMON_MODE_TRACES == 5


class TestTheReading:
    def test_two_traces_that_follow_each_other_co_vary(self):
        a = np.array(wave())
        reading = read_co_variation(a, 2 * a + 1.0)
        assert reading.verdict == VERDICT_CO_VARYING
        assert reading.r == pytest.approx(1.0)
        assert reading.n_spectra == 24
        assert reading.reason is None
        assert reading.testable

    def test_anti_correlation_is_a_reading_like_any_other(self):
        a = np.array(wave())
        reading = read_co_variation(a, -a)
        assert reading.verdict == VERDICT_NOT_CO_VARYING
        assert reading.r == pytest.approx(-1.0)
        assert reading.testable

    def test_r_at_the_line_co_varies_and_under_it_does_not(self):
        a = np.array(wave(period=24.0))
        b = np.array(wave(period=24.0, phase=1.2))
        r = float(np.corrcoef(a, b)[0, 1])
        assert read_co_variation(a, b, co_varying_r=r).verdict == VERDICT_CO_VARYING
        assert (
            read_co_variation(a, b, co_varying_r=r + 1e-9).verdict
            == VERDICT_NOT_CO_VARYING
        )

    def test_only_the_spectra_both_traces_hold_are_counted(self):
        a = np.array(wave())
        b = a.copy()
        b[:5] = np.nan
        a[-3:] = np.nan
        reading = read_co_variation(a, b)
        assert reading.n_spectra == 24 - 5 - 3
        assert reading.r == pytest.approx(1.0)

    def test_under_the_spectra_floor_it_is_untestable(self):
        a = np.array(wave(n=MIN_SPECTRA - 1))
        reading = read_co_variation(a, a)
        assert reading.verdict == VERDICT_UNTESTABLE
        assert reading.reason == REASON_FEW_SPECTRA
        assert reading.r is None
        assert reading.n_spectra == MIN_SPECTRA - 1
        assert not reading.testable

    def test_at_the_spectra_floor_it_is_read(self):
        a = np.array(wave(n=MIN_SPECTRA))
        assert read_co_variation(a, a).verdict == VERDICT_CO_VARYING

    def test_a_flat_partner_is_untestable(self):
        a = np.array(wave())
        flat = np.array(wave(amplitude=0.5 * FLAT_SPREAD))
        reading = read_co_variation(a, flat)
        assert reading.verdict == VERDICT_UNTESTABLE
        assert reading.reason == REASON_FLAT
        assert reading.r is None
        assert reading.spread[0] == pytest.approx(np.std(a))
        assert reading.spread[1] < FLAT_SPREAD

    def test_a_flat_trace_of_its_own_is_untestable_too(self):
        a = np.array(wave())
        flat = np.array(wave(amplitude=0.5 * FLAT_SPREAD))
        assert read_co_variation(flat, a).reason == REASON_FLAT

    def test_a_spread_just_over_the_floor_is_read(self):
        a = np.array(wave())
        b = a * (1.01 * FLAT_SPREAD / np.std(a))
        assert read_co_variation(a, b).verdict == VERDICT_CO_VARYING

    def test_no_spread_at_all_is_flat_whatever_the_floor(self):
        a = np.array(wave())
        reading = read_co_variation(a, np.zeros(24), flat_spread=0.0)
        assert reading.reason == REASON_FLAT

    def test_the_record_rounds(self):
        reading = CoVariation(
            VERDICT_CO_VARYING, 12, r=0.123456, spread=(0.0123456, 0.5)
        )
        assert reading.to_json() == {
            "verdict": VERDICT_CO_VARYING,
            "r": 0.123,
            "n_spectra": 12,
            "reason": None,
            "spread": [0.0123, 0.5],
        }

    def test_an_untestable_record_keeps_no_r(self):
        reading = read_co_variation(np.array([0.1]), np.array([0.2]))
        assert reading.to_json() == {
            "verdict": VERDICT_UNTESTABLE,
            "r": None,
            "n_spectra": 1,
            "reason": REASON_FEW_SPECTRA,
            "spread": None,
        }


class TestTheStrongestPartner:
    def test_the_highest_r_among_the_testable(self):
        low = CoVariation(VERDICT_NOT_CO_VARYING, 400, r=0.2)
        high = CoVariation(VERDICT_CO_VARYING, 30, r=0.8)
        untestable = CoVariation(VERDICT_UNTESTABLE, 900, reason=REASON_FLAT)
        assert strongest([low, untestable, high]) is high

    def test_else_the_untestable_one_with_the_most_spectra(self):
        few = CoVariation(VERDICT_UNTESTABLE, 4, reason=REASON_FEW_SPECTRA)
        flat = CoVariation(VERDICT_UNTESTABLE, 40, reason=REASON_FLAT)
        assert strongest([few, flat]) is flat

    def test_nothing_for_nothing(self):
        assert strongest([]) is None
        assert by_strength([]) == []

    def test_the_order_a_row_lists_its_partners_in(self):
        low = CoVariation(VERDICT_NOT_CO_VARYING, 400, r=-0.7)
        high = CoVariation(VERDICT_CO_VARYING, 30, r=0.8)
        few = CoVariation(VERDICT_UNTESTABLE, 4, reason=REASON_FEW_SPECTRA)
        flat = CoVariation(VERDICT_UNTESTABLE, 40, reason=REASON_FLAT)
        assert by_strength([few, low, flat, high]) == [high, low, flat, few]


def common_batch(n_background=8, common=None, *, seed=3):
    """A partition whose every trace follows one strong common mode."""
    rng = np.random.default_rng(seed)
    common = np.array(common if common is not None else wave(amplitude=0.8))
    traces = {
        f"bg{i}": trace(list(2.0 + common + 0.05 * rng.standard_normal(24)))
        for i in range(n_background)
    }
    return traces, common, rng


class TestTheResiduals:
    def test_the_traces_are_in_log10(self):
        traces = {"a": trace([1.0, 2.0, 3.0], SAMPLES[:3])}
        residuals = batch_residuals(traces, persistent_share=2.0)
        assert residuals.samples == tuple(SAMPLES[:3])
        # No trace is persistent at a share over one, so no spectrum has a
        # common mode and every residual is a gap.
        assert np.isnan(residuals.trace("a")).all()
        assert residuals.persistent == 0

    def test_a_non_positive_value_is_a_gap(self):
        traces = {f"t{i}": {"x": 10.0, "y": 100.0} for i in range(5)}
        traces["z"] = {"x": 0.0, "y": -1.0}
        residuals = batch_residuals(traces)
        assert np.isnan(residuals.trace("z")).all()

    def test_the_common_mode_is_the_median_of_the_centred_persistent_traces(self):
        # Five traces, each its own level plus a shared per-spectrum offset: the
        # common mode recovers the offset, centred, and the residuals are each
        # trace's level alone.
        offset = [0.0, 0.5, -0.25, 1.0]
        samples = SAMPLES[:4]
        traces = {
            f"t{i}": trace([1.0 + i + o for o in offset], samples) for i in range(5)
        }
        residuals = batch_residuals(traces, min_common_mode_traces=5)
        expected = np.array(offset) - np.median(offset)
        np.testing.assert_allclose(residuals.common_mode, expected)
        np.testing.assert_allclose(residuals.common_mode_traces, [5, 5, 5, 5])
        for i in range(5):
            np.testing.assert_allclose(
                residuals.trace(f"t{i}"), 1.0 + i + np.median(offset)
            )
        assert residuals.persistent == 5

    def test_removing_the_common_mode_unties_two_unrelated_traces(self):
        traces, common, rng = common_batch()
        traces["a"] = trace(list(3.0 + common + 0.1 * rng.standard_normal(24)))
        traces["b"] = trace(list(1.0 + common + 0.1 * rng.standard_normal(24)))
        raw = read_co_variation(
            np.log10([traces["a"][s] for s in SAMPLES]),
            np.log10([traces["b"][s] for s in SAMPLES]),
        )
        assert raw.verdict == VERDICT_CO_VARYING
        residuals = batch_residuals(traces)
        assert residuals.co_variation("a", "b").verdict == VERDICT_NOT_CO_VARYING

    def test_two_channels_of_one_neutral_still_co_vary_without_it(self):
        traces, common, rng = common_batch()
        own = np.array(wave(period=5.0, amplitude=0.4))
        traces["a"] = trace(list(3.0 + common + own + 0.02 * rng.standard_normal(24)))
        traces["b"] = trace(list(1.0 + common + own + 0.02 * rng.standard_normal(24)))
        reading = batch_residuals(traces).co_variation("a", "b")
        assert reading.verdict == VERDICT_CO_VARYING
        assert reading.n_spectra == 24

    def test_a_constant_factor_on_a_channel_moves_nothing(self):
        traces, common, rng = common_batch()
        own = np.array(wave(period=5.0, amplitude=0.4))
        traces["a"] = trace(list(3.0 + common + own + 0.05 * rng.standard_normal(24)))
        traces["b"] = trace(list(1.0 + common + own + 0.05 * rng.standard_normal(24)))
        before = batch_residuals(traces).co_variation("a", "b").r
        traces["b"] = {s: 1000.0 * v for s, v in traces["b"].items()}
        assert batch_residuals(traces).co_variation("a", "b").r == pytest.approx(before)

    def test_a_channel_running_against_its_parent_reads_anti_correlated(self):
        traces, common, rng = common_batch()
        source = np.array(wave(period=9.0, amplitude=0.5))
        traces["parent"] = trace(list(3.0 + common + source))
        traces["radical"] = trace(list(1.0 + common - source))
        reading = batch_residuals(traces).co_variation("parent", "radical")
        assert reading.verdict == VERDICT_NOT_CO_VARYING
        assert reading.r < -0.6

    def test_a_trace_under_the_persistent_share_is_not_read_into_the_common_mode(
        self,
    ):
        traces, _, _ = common_batch(n_background=6)
        traces["sparse"] = trace([5.0] * 11 + [None] * 13)
        residuals = batch_residuals(traces)
        assert residuals.persistent == 6
        assert residuals.common_mode_traces.max() == 6
        traces["sparse"] = trace([5.0] * 12 + [None] * 12)
        residuals = batch_residuals(traces)
        assert residuals.persistent == 7
        assert residuals.common_mode_traces[:12].tolist() == [7] * 12

    def test_a_spectrum_with_too_few_traces_has_no_common_mode(self):
        traces, _, _ = common_batch(n_background=5)
        traces["bg0"].pop(SAMPLES[3])
        residuals = batch_residuals(traces)
        assert residuals.common_mode_traces[3] == 4
        assert np.isnan(residuals.common_mode[3])
        assert np.isnan(residuals.trace("bg1")[3])
        assert np.isfinite(residuals.trace("bg1")[4])
        assert residuals.summary()["spectra_read"] == 23

    def test_the_spectra_axis_can_be_given(self):
        traces, _, _ = common_batch()
        order = list(reversed(SAMPLES)) + ["empty"]
        residuals = batch_residuals(traces, samples=order)
        assert residuals.samples == tuple(order)
        assert np.isnan(residuals.trace("bg0")[-1])
        default = batch_residuals(traces)
        np.testing.assert_allclose(
            residuals.trace("bg0")[:-1][::-1], default.trace("bg0")
        )


class TestTheReagentNormalisation:
    def test_without_reagent_ions_the_traces_are_not_normalised(self):
        traces, _, _ = common_batch()
        residuals = batch_residuals(traces)
        assert not residuals.normalised
        assert residuals.normaliser is None
        assert residuals.summary()["normalised"] is False

    def test_the_normaliser_is_the_summed_reagent_signal(self):
        traces, _, _ = common_batch()
        reagents = {
            "r1": {s: 100.0 * (i + 1) for i, s in enumerate(SAMPLES)},
            "r2": {s: 50.0 for s in SAMPLES[:12]},
        }
        residuals = batch_residuals(traces, reagent_traces=reagents)
        assert residuals.normalised
        assert residuals.summary()["normalised"] is True
        expected = [
            math.log10(100.0 * (i + 1) + (50.0 if i < 12 else 0.0)) for i in range(24)
        ]
        np.testing.assert_allclose(residuals.normaliser, expected)

    def test_a_spectrum_with_no_reagent_signal_drops_out(self):
        traces, _, _ = common_batch()
        reagents = {"r1": {s: 100.0 for s in SAMPLES if s != SAMPLES[5]}}
        residuals = batch_residuals(traces, reagent_traces=reagents)
        assert np.isnan(residuals.normaliser[5])
        assert np.isnan(residuals.trace("bg0")[5])
        assert np.isfinite(residuals.trace("bg0")[6])

    def test_the_reagent_ions_widen_the_spectra_axis(self):
        traces = {"a": {"x": 10.0}}
        residuals = batch_residuals(traces, reagent_traces={"r": {"y": 10.0}})
        assert residuals.samples == ("x", "y")

    def test_the_common_mode_is_read_off_the_normalised_traces(self):
        # One spectrum's reagent signal doubles while nothing else moves: the
        # normalised traces all drop there by log10(2), and that drop is what
        # the common mode reads.
        traces = {f"t{i}": {s: 10.0 ** (1 + i) for s in SAMPLES[:4]} for i in range(5)}
        reagents = {"r": {"s00": 1.0, "s01": 2.0, "s02": 1.0, "s03": 1.0}}
        residuals = batch_residuals(traces, reagent_traces=reagents)
        assert residuals.common_mode[1] == pytest.approx(-math.log10(2.0))
        assert residuals.common_mode[0] == pytest.approx(0.0)
