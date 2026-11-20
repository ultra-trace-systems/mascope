"""Co-variation of a neutral's channels over a batch (step 4.2 of the assignment plan)."""

import math

import numpy as np
import pytest

from mascope_tools.composition.coherence import (
    CO_VARYING_R,
    FLAT_CV,
    FLAT_MIN_SHARE,
    FLAT_SPREAD,
    FLAT_VERDICTS,
    MIN_COMMON_MODE_TRACES,
    MIN_SPECTRA,
    MIN_VARYING_SHARE,
    NORMALISER_REAGENT,
    NORMALISER_TIC,
    PERSISTENT_SHARE,
    REASON_FEW_SPECTRA,
    REASON_FLAT,
    REASON_RARE,
    REASON_STEADY,
    VERDICT_CO_VARYING,
    VERDICT_FLAT,
    VERDICT_NOT_CO_VARYING,
    VERDICT_UNTESTABLE,
    VERDICT_VARYING,
    CoVariation,
    batch_normaliser,
    batch_residuals,
    by_strength,
    normalised_cv,
    read_co_variation,
    read_flat_series,
    read_flatness,
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

    def test_a_trace_with_no_value_is_never_persistent(self):
        traces, _, _ = common_batch(n_background=5)
        traces["empty"] = {}
        residuals = batch_residuals(traces, persistent_share=0.0)
        assert residuals.persistent == 5

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


# --- a flat series (step 4.2e) --------------------------------------------------


def _flat(level=1000.0, samples=SAMPLES):
    """A trace that holds one height in every spectrum."""
    return {s: level for s in samples}


def _ratio(normaliser, share, samples=SAMPLES):
    """A trace that holds a constant ratio to ``normaliser``."""
    return {s: normaliser[s] * share for s in samples}


def _diel(n=24, amplitude=0.6, level=3.0):
    """A trace that rises and falls with the day: a cv far over the bar."""
    return trace([level + value for value in wave(n, period=12.0, amplitude=amplitude)])


def _varying_partition(extra=None):
    """A partition most of whose traces move, so a flat one means background."""
    traces = {f"v{i}": _diel() for i in range(4)}
    traces.update(extra or {})
    return traces


class TestTheFlatThresholds:
    def test_the_reference_s_line_and_the_floors(self):
        assert FLAT_CV == 0.25
        assert FLAT_MIN_SHARE == PERSISTENT_SHARE == 0.5
        assert MIN_VARYING_SHARE == 0.20

    def test_the_verdicts_flat_first(self):
        assert FLAT_VERDICTS == (VERDICT_FLAT, VERDICT_VARYING, VERDICT_UNTESTABLE)


class TestTheNormaliser:
    def test_the_reagent_ions_summed_per_spectrum(self):
        reagents = {
            "r1": {s: 100.0 for s in SAMPLES},
            "r2": {s: 50.0 for s in SAMPLES[:12]},
        }
        normaliser = batch_normaliser(SAMPLES, reagent_traces=reagents, tic=None)
        assert normaliser.kind == NORMALISER_REAGENT
        np.testing.assert_allclose(normaliser.values, [150.0] * 12 + [100.0] * 12)
        assert normaliser.divisor is normaliser.values

    def test_the_tic_where_no_reagent_ion_is_held(self):
        tic = {s: 1e6 * (1 + 0.01 * i) for i, s in enumerate(SAMPLES)}
        normaliser = batch_normaliser(SAMPLES, reagent_traces={}, tic=tic)
        assert normaliser.kind == NORMALISER_TIC
        np.testing.assert_allclose(normaliser.values, [tic[s] for s in SAMPLES])
        assert normaliser.to_json()["normalised"] is True

    def test_the_reagent_ions_before_the_tic(self):
        normaliser = batch_normaliser(
            SAMPLES, reagent_traces={"r": _flat(10.0)}, tic=_flat(1e6)
        )
        assert normaliser.kind == NORMALISER_REAGENT

    def test_reagent_ions_present_in_no_spectrum_fall_back_to_the_tic(self):
        normaliser = batch_normaliser(
            SAMPLES, reagent_traces={"r": {"elsewhere": 5.0}}, tic=_flat(1e6)
        )
        assert normaliser.kind == NORMALISER_TIC

    def test_none_where_the_partition_holds_neither(self):
        normaliser = batch_normaliser(SAMPLES, reagent_traces={}, tic={})
        assert normaliser.kind is None and normaliser.divisor is None
        assert normaliser.to_json() == {
            "normaliser": None,
            "normaliser_cv": None,
            "normaliser_rejected": False,
            "normalised": False,
        }

    def test_one_that_moves_more_than_a_flat_trace_is_rejected(self):
        # A normaliser at the bar or over it would put its own swings into
        # every trace it divides.
        moving = {s: 10.0 ** (6 + v) for s, v in zip(SAMPLES, wave(amplitude=0.3))}
        normaliser = batch_normaliser(SAMPLES, reagent_traces={}, tic=moving)
        assert normaliser.cv >= FLAT_CV
        assert normaliser.rejected and normaliser.divisor is None
        assert normaliser.to_json()["normalised"] is False
        assert normaliser.to_json()["normaliser_rejected"] is True

    def test_one_just_under_the_bar_divides(self):
        values = np.array([1.0, 1.0 + 2 * 0.2])
        tic = {"a": 1.0, "b": 1.4}
        normaliser = batch_normaliser(["a", "b"], tic=tic, flat_cv=0.3)
        assert normaliser.cv == pytest.approx(np.std(values, ddof=1) / values.mean())
        assert not normaliser.rejected

    def test_one_exactly_at_the_bar_is_rejected(self):
        # "0.25 or more": a normaliser whose cv is the bar itself is not
        # divided by, as a trace at the bar varies.
        tic = {"a": 1.0, "b": 1.4}
        cv = batch_normaliser(["a", "b"], tic=tic, flat_cv=1.0).cv
        at_the_bar = batch_normaliser(["a", "b"], tic=tic, flat_cv=cv)
        assert at_the_bar.cv == cv
        assert at_the_bar.rejected and at_the_bar.divisor is None
        assert not batch_normaliser(["a", "b"], tic=tic, flat_cv=cv + 1e-9).rejected


class TestTheNormalisedCv:
    def test_a_constant_ratio_to_the_normaliser_has_none(self):
        tic = {s: 1e6 * (1 + 0.02 * i) for i, s in enumerate(SAMPLES)}
        normaliser = batch_normaliser(SAMPLES, tic=tic)
        assert not normaliser.rejected
        cv, n = normalised_cv(_ratio(tic, 1e-3), SAMPLES, normaliser.divisor)
        assert cv == pytest.approx(0.0, abs=1e-12)
        assert n == len(SAMPLES)
        # Undivided, the same trace follows the normaliser's drift.
        raw, _ = normalised_cv(_ratio(tic, 1e-3), SAMPLES)
        assert raw == pytest.approx(normaliser.cv) and raw > 0.1

    def test_the_sample_standard_deviation_over_the_mean(self):
        cv, n = normalised_cv({"a": 1.0, "b": 3.0}, ["a", "b"])
        assert cv == pytest.approx(math.sqrt(2.0) / 2.0)
        assert n == 2

    def test_over_the_spectra_the_trace_is_present_in(self):
        partial = {s: 100.0 for s in SAMPLES[:10]}
        cv, n = normalised_cv(partial, SAMPLES)
        assert cv == pytest.approx(0.0) and n == 10

    def test_a_spectrum_without_a_normaliser_drops_out(self):
        divisor = np.array([1.0] * 23 + [np.nan])
        cv, n = normalised_cv(_flat(), SAMPLES, divisor)
        assert n == 23

    def test_under_two_spectra_there_is_none(self):
        assert normalised_cv({"a": 5.0}, ["a", "b"]) == (None, 1)


class TestTheFlatVerdict:
    def test_flat_under_the_bar_in_enough_of_the_spectra(self):
        verdict = read_flat_series(0.1, 20, 24)
        assert verdict.flat and verdict.verdict == VERDICT_FLAT
        assert verdict.share == pytest.approx(20 / 24)
        assert verdict.to_json() == {
            "verdict": VERDICT_FLAT,
            "cv": 0.1,
            "n_present": 20,
            "n_spectra": 24,
            "share": round(20 / 24, 4),
            "reason": None,
        }

    def test_varying_at_the_bar_and_over_it(self):
        assert read_flat_series(FLAT_CV, 24, 24).verdict == VERDICT_VARYING
        assert read_flat_series(FLAT_CV - 1e-9, 24, 24).verdict == VERDICT_FLAT

    def test_a_trace_in_under_the_floor_share_is_untestable(self):
        verdict = read_flat_series(0.05, 11, 24)
        assert verdict.verdict == VERDICT_UNTESTABLE
        assert verdict.reason == REASON_RARE
        assert read_flat_series(0.05, 12, 24).flat
        # The bar comes first: a rare trace that moves still reads varying.
        assert read_flat_series(FLAT_CV, 11, 24).verdict == VERDICT_VARYING

    def test_a_trace_on_too_few_spectra_is_untestable(self):
        verdict = read_flat_series(0.05, MIN_SPECTRA - 1, MIN_SPECTRA - 1)
        assert verdict.reason == REASON_FEW_SPECTRA
        assert read_flat_series(0.05, MIN_SPECTRA, MIN_SPECTRA).flat
        assert read_flat_series(None, 20, 24).reason == REASON_FEW_SPECTRA
        # The floor comes before the bar: a trace that moves, on too few
        # spectra, is not read either, rather than read varying.
        moving = read_flat_series(0.6, MIN_SPECTRA - 1, 200)
        assert moving.verdict == VERDICT_UNTESTABLE
        assert moving.reason == REASON_FEW_SPECTRA

    def test_a_steady_partition_reads_no_trace_flat(self):
        verdict = read_flat_series(0.05, 24, 24, steady=True)
        assert verdict.verdict == VERDICT_UNTESTABLE
        assert verdict.reason == REASON_STEADY
        # A varying trace still varies there.
        assert read_flat_series(0.5, 24, 24, steady=True).verdict == VERDICT_VARYING


class TestAPartitionsFlatness:
    def test_a_flat_trace_in_a_varying_partition_is_background(self):
        traces = _varying_partition({"bg": _flat(), "plume": _diel(amplitude=1.0)})
        flatness = read_flatness(traces, tic=_flat(1e6))
        assert not flatness.steady
        assert flatness.series["bg"].flat
        assert flatness.series["plume"].verdict == VERDICT_VARYING
        assert flatness.normaliser.kind == NORMALISER_TIC
        summary = flatness.summary()
        assert summary["flat"] == 1 and summary["varying"] == 5
        assert summary["traces_read"] == 6
        assert summary["varying_share"] == round(5 / 6, 4)

    def test_a_trace_that_follows_the_reagent_signal_is_flat(self):
        # The reagent ions dim through the batch; a source cluster dims with
        # them, a constant ratio to the source.
        reagent = {s: 1e5 * (1 - 0.012 * i) for i, s in enumerate(SAMPLES)}
        traces = _varying_partition({"cluster": _ratio(reagent, 0.01)})
        flatness = read_flatness(traces, reagent_traces={"r": reagent})
        assert flatness.normaliser.kind == NORMALISER_REAGENT
        assert flatness.series["cluster"].flat
        assert flatness.series["cluster"].cv == pytest.approx(0.0, abs=1e-9)

    def test_a_partition_that_holds_still_reads_none_flat(self):
        traces = {f"bg{i}": _flat(100.0 * (i + 1)) for i in range(9)}
        traces["one"] = _diel()
        flatness = read_flatness(traces, tic=_flat(1e6))
        assert flatness.varying_share == pytest.approx(0.1)
        assert flatness.steady
        assert flatness.series["bg0"].reason == REASON_STEADY

    def test_the_varying_share_is_read_over_every_trace(self):
        # Two of ten vary: at the floor, the partition varies.
        traces = {f"bg{i}": _flat() for i in range(8)}
        traces.update({"d1": _diel(), "d2": _diel()})
        flatness = read_flatness(traces, tic=_flat(1e6), read=["bg0"])
        assert flatness.varying_share == pytest.approx(0.2)
        assert not flatness.steady
        assert list(flatness.series) == ["bg0"] and flatness.series["bg0"].flat

    def test_traces_on_too_few_spectra_do_not_count_towards_the_share(self):
        # A partition that holds still stays steady when a few sporadic
        # traces, read on under MIN_SPECTRA spectra, swing: only the traces
        # read on enough spectra say how much of it varies.
        traces = {f"bg{i}": _flat(100.0 * (i + 1)) for i in range(9)}
        traces["one"] = _diel()
        swings = [0.0, 0.6, -0.5, 0.5, -0.4, 0.7]
        for i in range(5):
            traces[f"sporadic{i}"] = trace(swings, SAMPLES[i : i + len(swings)])
        flatness = read_flatness(traces, tic=_flat(1e6))
        cv, n = normalised_cv(traces["sporadic0"], SAMPLES, flatness.normaliser.divisor)
        assert 2 <= n < MIN_SPECTRA and cv >= FLAT_CV
        assert flatness.traces_read == 10
        assert flatness.varying_share == pytest.approx(0.1)
        assert flatness.steady
        assert flatness.series["bg0"].reason == REASON_STEADY
        assert flatness.series["sporadic0"].reason == REASON_FEW_SPECTRA

    def test_only_the_traces_asked_get_a_verdict(self):
        traces = _varying_partition({"bg": _flat()})
        flatness = read_flatness(traces, tic=_flat(1e6), read=["bg", "missing"])
        assert list(flatness.series) == ["bg"]

    def test_a_rejected_normaliser_leaves_the_traces_undivided(self):
        moving = {s: 10.0 ** (6 + v) for s, v in zip(SAMPLES, wave(amplitude=0.3))}
        traces = _varying_partition({"bg": _flat()})
        flatness = read_flatness(traces, tic=moving)
        assert flatness.normaliser.rejected
        # Divided by the moving TIC the flat trace would vary; undivided, flat.
        assert flatness.series["bg"].flat

    def test_the_share_counts_the_partition_s_spectra(self):
        half = {s: 100.0 for s in SAMPLES[:11]}
        traces = _varying_partition({"half": half})
        flatness = read_flatness(traces, tic=_flat(1e6))
        assert flatness.series["half"].reason == REASON_RARE
        assert flatness.series["half"].n_spectra == len(SAMPLES)


def test_read_pair_is_the_readings_own_pair_and_one_normalisation():
    """One function and one normalisation for every reader of a pair
    (MAS-983): for two analyte channels it is the reading's own residuals'
    pair, the partition's whole reagent signal the normaliser; a reagent ion
    read against its own core is divided by every other reagent ion, never by
    itself."""
    from mascope_tools.composition.coherence import batch_residuals, read_pair

    rng = np.random.default_rng(7)
    samples = [f"s{i:02d}" for i in range(24)]
    drift = rng.normal(0, 0.3, len(samples))

    def trace(scale, noise=0.05, follow=1.0):
        return {
            s: float(10 ** (scale + follow * d + rng.normal(0, noise)))
            for s, d in zip(samples, drift)
        }

    traces = {f"a{i}": trace(4 + i * 0.1) for i in range(8)}
    traces["x"] = trace(5)
    traces["y"] = trace(5.2)
    reagents = {"core": trace(7), "ion": trace(6.5), "other": trace(6.8, follow=0.0)}
    whole = batch_residuals(traces, reagent_traces=reagents)
    assert read_pair(traces, "x", "y", reagent_traces=reagents) == whole.co_variation(
        "x", "y"
    )
    both = {**traces, "core": reagents["core"], "ion": reagents["ion"]}
    without = batch_residuals(both, reagent_traces={"other": reagents["other"]})
    assert read_pair(both, "ion", "core", reagent_traces=reagents) == (
        without.co_variation("ion", "core")
    )
    assert read_pair(both, "ion", "core") == batch_residuals(both).co_variation(
        "ion", "core"
    )
