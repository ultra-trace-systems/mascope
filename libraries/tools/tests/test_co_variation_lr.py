"""The co-variation null model's likelihood ratio and its lookup (step 4.21)."""

import numpy as np
import pytest
from scipy.stats import beta

from mascope_tools.composition.co_variation_lr import (
    EXCESS_Z,
    LR_BAR,
    R_EDGES,
    STRATA,
    TABLE,
    LikelihoodRatioTable,
    bar_of,
    excess_shown,
    histogram,
    isotonic_increasing,
    likelihood_ratio,
    mixture_lr,
    null_share,
    null_share_error,
    r_bin,
    ratio_lr,
    stratum_of,
)
from mascope_tools.composition.coherence import (
    MIN_SPECTRA,
    REASON_FEW_SPECTRA,
    REASON_FLAT,
    VERDICT_CO_VARYING,
    VERDICT_NOT_CO_VARYING,
    VERDICT_UNTESTABLE,
    CoVariation,
    batch_residuals,
)


BINS = len(R_EDGES) - 1


# --- the strata and the bins -------------------------------------------------


@pytest.mark.parametrize(
    "n, stratum",
    [(0, None), (9, None), (10, 0), (19, 0), (20, 1), (49, 1), (50, 2), (199, 2)]
    + [(200, 3), (5000, 3)],
)
def test_stratum_edges(n, stratum):
    assert stratum_of(n) == stratum


def test_strata_start_where_a_reading_is_testable():
    assert STRATA[0] == MIN_SPECTRA


@pytest.mark.parametrize(
    "r, index",
    [(-1.0, 0), (-0.95, 0), (-0.9, 1), (0.0, 10), (0.5999, 15), (0.6, 16)]
    + [(0.95, 19), (1.0, 19), (1.2, 19), (-1.3, 0)],
)
def test_r_bin_edges(r, index):
    assert r_bin(r) == index


def test_histogram_is_a_probability_mass_with_its_pseudo_counts():
    mass = histogram([0.65, 0.65, -0.5], pseudo_count=0.5)
    assert mass.sum() == pytest.approx(1.0)
    total = 3 + 0.5 * BINS
    assert mass[16] == pytest.approx(2.5 / total)
    assert mass[5] == pytest.approx(1.5 / total)
    assert mass[0] == pytest.approx(0.5 / total)


# --- the monotone fit --------------------------------------------------------


def test_isotonic_pools_a_falling_run_into_its_weighted_mean():
    fit = isotonic_increasing([1.0, 3.0, 2.0, 4.0], [1, 1, 3, 1])
    assert fit.tolist() == pytest.approx([1.0, 2.25, 2.25, 4.0])


def test_isotonic_leaves_a_rising_row_as_it_is():
    assert isotonic_increasing([0.1, 0.2, 5.0]).tolist() == pytest.approx(
        [0.1, 0.2, 5.0]
    )


def test_isotonic_never_falls():
    rng = np.random.default_rng(3)
    fit = isotonic_increasing(rng.normal(size=40), rng.uniform(1, 5, size=40))
    assert np.all(np.diff(fit) >= -1e-12)


# --- the estimates -----------------------------------------------------------


def _null(rng, n):
    return np.clip(rng.normal(0.0, 0.25, n), -1, 1)


def _true(rng, n):
    return np.clip(rng.normal(0.85, 0.07, n), -1, 1)


def test_null_share_reads_the_chance_pairings_below_the_nulls_median():
    rng = np.random.default_rng(1)
    null = _null(rng, 20000)
    channel = np.concatenate([_null(rng, 6000), _true(rng, 4000)])
    assert null_share(channel, null) == pytest.approx(0.6, abs=0.03)


def test_null_share_is_held_in_zero_to_one():
    assert null_share([-0.9] * 10, [0.0, 0.1, -0.1, 0.2]) == 1.0
    assert null_share([0.9] * 10, [0.0, 0.1, -0.1, 0.2]) == 0.0
    assert null_share([], [0.1]) == 1.0


def test_mixture_ratio_is_one_where_the_channels_are_all_chance():
    rng = np.random.default_rng(2)
    ratios, pi = mixture_lr(_null(rng, 3000), _null(rng, 20000))
    assert pi == pytest.approx(0.0, abs=0.05)
    # Where the channel pairs show no excess, nothing is evidence.
    assert np.all(ratios <= 1.5)


def test_mixture_ratio_is_one_or_lower_where_the_null_dominates():
    rng = np.random.default_rng(4)
    null = _null(rng, 20000)
    channel = np.concatenate([_null(rng, 6000), _true(rng, 4000)])
    ratios, pi = mixture_lr(channel, null)
    assert pi == pytest.approx(0.4, abs=0.03)
    assert np.all(np.diff(ratios) >= 0)
    assert np.all(ratios[: r_bin(0.3) + 1] <= 1.0)
    assert ratios[r_bin(0.9)] >= LR_BAR


def test_mixture_ratio_reads_the_true_component_not_the_channel_share():
    # Channel pairs a quarter true: the raw channel-over-null ratio at a high
    # r is a quarter of the true one; the mixture estimate recovers it.
    rng = np.random.default_rng(5)
    null = _null(rng, 40000)
    channel = np.concatenate([_null(rng, 7500), _true(rng, 2500)])
    ratios, pi = mixture_lr(channel, null)
    truth = histogram(_true(rng, 200000)) / histogram(null)
    raw = histogram(channel, pseudo_count=0.0) / histogram(null)
    index = r_bin(0.75)
    assert ratios[index] == pytest.approx(truth[index], rel=0.25)
    assert raw[index] < 0.5 * truth[index]


def test_null_share_reads_below_the_nulls_median_by_default():
    null = [0.1, 0.2, 0.3, 0.4, 0.5]
    channel = [0.15, 0.9, 0.9, 0.9, 0.9]
    # Under 0.3: two of five decoys, one of five channel pairs.
    assert null_share(channel, null) == pytest.approx(0.5)
    assert null_share(channel, null, split=0.3) == pytest.approx(0.5)
    # Under 0.0 no decoy lies: nothing to read the share by.
    assert null_share(channel, null, split=0.0) == 1.0


def test_the_shares_error_is_the_delta_method_over_both_samples():
    null = [0.1, 0.2, 0.3, 0.4, 0.5]
    channel = [0.15, 0.9, 0.9, 0.9, 0.9]
    b_channel, b_null, share = 0.2, 0.4, 0.5
    # The channel term: one of five under the split, its exact one-sided
    # 95 % upper bound, over z.
    upper = beta.ppf(0.95, 2, 4)
    error_channel = (upper - b_channel) / EXCESS_Z
    expected = np.sqrt(error_channel**2 + share**2 * b_null * (1 - b_null) / 5) / b_null
    assert null_share_error(channel, null) == pytest.approx(expected, rel=1e-3)


def test_the_shares_error_on_many_pairs_is_the_binomial_one():
    rng = np.random.default_rng(23)
    null = _null(rng, 20000)
    channel = np.concatenate([_null(rng, 1400), _true(rng, 600)])
    below = float(np.mean(channel < np.median(null)))
    binomial = np.sqrt(below * (1 - below) / channel.size) / 0.5
    assert null_share_error(channel, null) == pytest.approx(binomial, rel=0.1)


def test_a_handful_of_pairs_over_the_median_shows_no_excess():
    # None of them under the split: the binomial error would be nought, and
    # two pairs would stand clear of chance and give a bar at 0.9.
    rng = np.random.default_rng(24)
    null = _null(rng, 700)
    for channel in ([0.92, 0.95], [0.8, 0.85, 0.9]):
        assert null_share(channel, null) == 0.0
        assert null_share_error(channel, null) > 0.5
        assert not excess_shown(channel, null)
        ratios, _ = mixture_lr(channel, null)
        assert ratios.tolist() == [1.0] * BINS
        assert bar_of(ratios) is None


def test_every_pair_under_the_split_has_the_nulls_error_alone():
    # The channel share is one: its upper bound is one too, and only the
    # null's sampling error is left.
    null = [0.1, 0.2, 0.3, 0.4, 0.5]
    share, b_null = 2.5, 0.4
    expected = share * np.sqrt(b_null * (1 - b_null) / 5) / b_null
    assert null_share_error([-0.5, -0.4], null) == pytest.approx(expected)


@pytest.mark.parametrize("pairs", [3, 5, 10, 30])
def test_chance_pairs_show_an_excess_at_most_one_draw_in_twenty(pairs):
    # Channel pairs drawn from the null itself, a few at a time: the guard
    # holds its one-sided 95 % however small the sample.
    rng = np.random.default_rng(25)
    null = _null(rng, 700)
    draws = 2000
    shown = sum(excess_shown(rng.choice(null, pairs), null) for _ in range(draws))
    assert shown / draws <= 0.06


def test_a_ratio_is_never_negative():
    # Channel pairs thinner than the null at low r: the mixture's raw ratio
    # is below nought there, and a ratio is not.
    rng = np.random.default_rng(31)
    null = _null(rng, 20000)
    channel = np.concatenate([_null(rng, 300), _true(rng, 700)])
    ratios, _ = mixture_lr(channel, null)
    assert np.all(ratios >= 0.0)
    assert ratios[r_bin(-0.3)] == 0.0


def test_a_bin_no_pair_reached_is_not_read_as_evidence():
    # Neither a channel pair nor a decoy falls between 0.5 and 0.7; a
    # pseudo-count on the smaller sample would read those empty bins far
    # above the null.
    rng = np.random.default_rng(32)
    null = np.clip(rng.normal(0.0, 0.15, 4000), -0.45, 0.45)
    chance = np.clip(rng.normal(0.0, 0.15, 40), -0.45, 0.45)
    channel = np.concatenate([chance, [0.75] * 30])
    ratios, _ = mixture_lr(channel, null)
    assert ratios[r_bin(0.55)] == 0.0
    assert ratios[r_bin(0.65)] == 0.0
    assert ratios[r_bin(0.75)] >= LR_BAR
    alt = ratio_lr([0.75] * 30, null)
    assert alt[r_bin(0.55)] == 0.0
    assert alt[r_bin(-0.5)] == 0.0


def test_the_monotone_fit_weighs_each_bin_by_its_pairs():
    alt = [0.15] * 40 + [0.25] * 2
    null = [0.15] * 10 + [0.25] * 10 + [-0.5] * 100
    expected = isotonic_increasing(
        histogram(alt, pseudo_count=0.0) / histogram(null),
        1
        + histogram(alt, pseudo_count=0.0) * 42
        + histogram(null, pseudo_count=0.0) * 120,
    )
    assert ratio_lr(alt, null).tolist() == pytest.approx(expected.tolist())
    unweighted = isotonic_increasing(histogram(alt, pseudo_count=0.0) / histogram(null))
    assert ratio_lr(alt, null).tolist() != pytest.approx(unweighted.tolist())


def test_a_share_near_nought_shows_no_excess_and_gives_no_ratio():
    # Chance pairs only, a few of them: the share read off them wanders a few
    # points from one, and dividing by it would turn a bin's noise into a
    # ratio over the bar.
    # One-sided at 95 %, about one sample in twenty still shows an excess.
    shown = 0
    for seed in range(60):
        rng = np.random.default_rng(seed)
        channel, null = _null(rng, 200), _null(rng, 4000)
        ratios, _ = mixture_lr(channel, null)
        if excess_shown(channel, null):
            shown += 1
        else:
            assert ratios.tolist() == [1.0] * BINS
    assert shown <= 6


def test_the_guard_reads_the_true_share_against_its_error():
    rng = np.random.default_rng(21)
    null = _null(rng, 20000)
    channel = np.concatenate([_null(rng, 140), _true(rng, 60)])
    pi = 1 - null_share(channel, null)
    error = null_share_error(channel, null)
    assert 0.05 < error < 0.1
    assert excess_shown(channel, null)
    # Asked to stand further off nought than it does, it is not shown.
    assert not excess_shown(channel, null, z=pi / error + 0.01)
    ratios, _ = mixture_lr(channel, null, z=pi / error + 0.01)
    assert ratios.tolist() == [1.0] * BINS
    assert mixture_lr(channel, null)[0][r_bin(0.9)] >= LR_BAR


def test_the_shares_error_counts_both_samples():
    rng = np.random.default_rng(22)
    channel = _null(rng, 400)
    few, many = _null(rng, 50), _null(rng, 50000)
    assert null_share_error(channel, few) > null_share_error(channel, many)
    assert null_share_error([], many) == float("inf")


def test_ratio_lr_is_the_monotone_density_ratio():
    rng = np.random.default_rng(6)
    null = _null(rng, 10000)
    ratios = ratio_lr(_true(rng, 2000), null)
    assert np.all(np.diff(ratios) >= 0)
    assert ratios[r_bin(-0.5)] < 0.1
    assert ratios[r_bin(0.9)] > 100


def test_bar_is_the_lower_edge_of_the_first_bin_that_reaches_the_ratio():
    ratios = [0.0] * 15 + [2.0, 9.99, 10.0, 30.0, 40.0]
    assert bar_of(ratios) == pytest.approx(0.7)
    assert bar_of([1.0] * BINS) is None
    assert bar_of(ratios, lr_bar=2.0) == pytest.approx(0.5)


# --- the table and its lookup ------------------------------------------------


def test_the_table_is_versioned_provisional_and_monotone():
    assert TABLE.version == 1
    # The bar cleared on one reagent chemistry only (the plan's log).
    assert TABLE.provisional
    assert len(TABLE.ratios) == len(STRATA)
    for row in TABLE.ratios:
        assert len(row) == BINS
        assert all(b >= a for a, b in zip(row, row[1:]))


def test_the_table_is_one_or_lower_where_the_null_dominates():
    for edge in STRATA:
        for r in (-0.9, -0.5, -0.2, 0.0):
            assert TABLE.lookup(r, edge) <= 1.0


def test_the_lookup_is_monotone_in_r_in_every_stratum():
    rs = np.linspace(-1, 1, 81)
    for edge in STRATA:
        values = [TABLE.lookup(float(r), edge) for r in rs]
        assert all(b >= a for a, b in zip(values, values[1:]))


def test_the_lookup_reads_the_row_of_the_pairs_stratum():
    table = LikelihoodRatioTable(
        version=9,
        strata=STRATA,
        r_edges=R_EDGES,
        ratios=tuple(tuple([float(i)] * BINS) for i in range(len(STRATA))),
        bars=(None,) * len(STRATA),
    )
    assert table.lookup(0.7, 9) is None
    assert table.lookup(0.7, 10) == 0.0
    assert table.lookup(0.7, 19) == 0.0
    assert table.lookup(0.7, 20) == 1.0
    assert table.lookup(0.7, 49) == 1.0
    assert table.lookup(0.7, 50) == 2.0
    assert table.lookup(0.7, 199) == 2.0
    assert table.lookup(0.7, 200) == 3.0


def test_the_lookup_reads_the_bin_of_r():
    row = tuple(float(i) for i in range(BINS))
    table = LikelihoodRatioTable(
        version=9,
        strata=STRATA,
        r_edges=R_EDGES,
        ratios=(row,) * len(STRATA),
        bars=(None,) * len(STRATA),
    )
    assert table.lookup(0.5999, 30) == 15.0
    assert table.lookup(0.6, 30) == 16.0
    assert table.lookup(1.0, 30) == 19.0
    assert table.lookup(-1.0, 30) == 0.0


def test_the_tables_bar_is_its_strata():
    table = LikelihoodRatioTable(
        version=9,
        strata=STRATA,
        r_edges=R_EDGES,
        ratios=((1.0,) * BINS,) * len(STRATA),
        bars=(0.5, 0.6, 0.7, 0.8),
    )
    assert table.bar(9) is None
    assert [table.bar(n) for n in (10, 49, 50, 200)] == [0.5, 0.6, 0.7, 0.8]
    assert TABLE.bar(9) is None
    for edge, bar in zip(STRATA, TABLE.bars):
        assert TABLE.bar(edge) == bar
    assert TABLE.bars[2] == pytest.approx(0.9)
    assert TABLE.lookup(0.9, 50) >= LR_BAR
    assert TABLE.lookup(0.89, 50) < LR_BAR


def test_a_table_refuses_a_row_that_falls():
    row = [1.0] * BINS
    row[5] = 0.5
    with pytest.raises(ValueError, match="fall"):
        LikelihoodRatioTable(
            version=1,
            strata=STRATA,
            r_edges=R_EDGES,
            ratios=(tuple(row),) + ((1.0,) * BINS,) * (len(STRATA) - 1),
            bars=(None,) * len(STRATA),
        )


def test_a_table_refuses_a_row_of_the_wrong_length():
    with pytest.raises(ValueError, match="one ratio per bin"):
        LikelihoodRatioTable(
            version=1,
            strata=STRATA,
            r_edges=R_EDGES,
            ratios=((1.0,) * (BINS - 1),) * len(STRATA),
            bars=(None,) * len(STRATA),
        )
    with pytest.raises(ValueError, match="one bar per stratum"):
        LikelihoodRatioTable(
            version=1,
            strata=STRATA,
            r_edges=R_EDGES,
            ratios=((1.0,) * BINS,) * len(STRATA),
            bars=(None,),
        )


def test_the_table_round_trips_as_json():
    data = TABLE.to_json()
    assert data["version"] == 1
    assert data["provisional"] is True
    assert data["bars"] == list(TABLE.bars)
    assert len(data["ratios"]) == len(STRATA)


def test_likelihood_ratio_of_a_reading():
    reading = CoVariation(VERDICT_CO_VARYING, 120, r=0.95, spread=(0.1, 0.1))
    assert likelihood_ratio(reading) == TABLE.lookup(0.95, 120)
    weak = CoVariation(VERDICT_NOT_CO_VARYING, 120, r=-0.4, spread=(0.1, 0.1))
    assert likelihood_ratio(weak) <= 1.0
    untestable = CoVariation(VERDICT_UNTESTABLE, 4, reason=REASON_FEW_SPECTRA)
    assert likelihood_ratio(untestable) is None
    # A flat pair holds spectra enough for a stratum, and no r.
    flat = CoVariation(VERDICT_UNTESTABLE, 30, reason=REASON_FLAT, spread=(0.01, 0.1))
    assert likelihood_ratio(flat) is None


def test_likelihood_ratio_reads_the_table_it_is_given():
    table = LikelihoodRatioTable(
        version=2,
        strata=STRATA,
        r_edges=R_EDGES,
        ratios=((7.0,) * BINS,) * len(STRATA),
        bars=(None,) * len(STRATA),
    )
    reading = CoVariation(VERDICT_CO_VARYING, 30, r=0.7, spread=(0.1, 0.1))
    assert likelihood_ratio(reading, table) == 7.0


# --- a trace read through a partition's own residuals ------------------------


def _traces(rng, keys, samples):
    base = rng.normal(0, 0.3, len(samples))
    return {
        key: {
            s: float(10 ** (4 + base[i] + rng.normal(0, 0.1)))
            for i, s in enumerate(samples)
        }
        for key in keys
    }


def test_residual_of_puts_a_trace_through_the_partitions_steps():
    rng = np.random.default_rng(7)
    samples = [f"s{i:02d}" for i in range(30)]
    traces = _traces(rng, [f"t{i}" for i in range(8)], samples)
    reagents = _traces(rng, ["r"], samples)
    residuals = batch_residuals(traces, reagent_traces=reagents, samples=samples)
    # A trace of the partition reads exactly as its own row.
    assert np.allclose(residuals.residual_of(traces["t3"]), residuals.trace("t3"))
    # One the common mode was not read off: log, the normaliser, the common
    # mode, nothing else.
    line = {s: v / 90.0 for s, v in traces["t3"].items() if s != "s05"}
    expected = (
        np.log10([line.get(s, np.nan) for s in samples])
        - residuals.normaliser
        - residuals.common_mode
    )
    out = residuals.residual_of(line)
    assert np.isnan(out[5])
    assert np.allclose(out[np.isfinite(out)], expected[np.isfinite(expected)])


def test_residual_of_without_a_normaliser():
    rng = np.random.default_rng(8)
    samples = [f"s{i:02d}" for i in range(20)]
    traces = _traces(rng, [f"t{i}" for i in range(6)], samples)
    residuals = batch_residuals(traces, samples=samples)
    assert not residuals.normalised
    line = {s: v * 0.01 for s, v in traces["t1"].items()}
    expected = np.log10([line[s] for s in samples]) - residuals.common_mode
    assert np.allclose(residuals.residual_of(line), expected, equal_nan=True)
