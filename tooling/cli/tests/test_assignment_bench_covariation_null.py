"""The co-variation null model's measurement (``tooling/assignment_bench/covariation_null.py``).

The pure half only: the pairs a partition gives, the decoy rule, the
statistics and the outputs, on made-up traces. No database.
"""

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mascope_tools.composition import co_variation_lr as lr


ROOT = Path(__file__).resolve().parents[2] / "assignment_bench"


def _load():
    spec = importlib.util.spec_from_file_location(
        "covariation_null", ROOT / "covariation_null.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cn = _load()

SAMPLES = [f"s{i:03d}" for i in range(60)]


# --- the chemistry ------------------------------------------------------------


@pytest.mark.parametrize(
    "notations, chemistry",
    [
        (["[M+CH4N2O+H]+", "[M+H]+", "[M+NH4]+"], "uronium"),
        (["[M-H]-", "[M+^NO3]-", "[M+NO3]-"], "15N-nitrate"),
        (["[M+Br]-", "[M-H]-"], "bromide"),
        (["[M+NO3]-", "[M-H]-", "[M+CO3]-"], "nitrate"),
        (["[M+NO3]-", "[M+Br]-"], "nitrate+bromide"),
        (["[M]+.", "[M+H]+", "[M-CH3]+"], "charge transfer (+)"),
        (["[M]-."], "charge transfer (-)"),
        # No committed channel: not charge transfer of either polarity.
        ([], "none"),
    ],
)
def test_chemistry_is_read_off_the_channels(notations, chemistry):
    assert cn.chemistry_of(notations) == chemistry


# --- the pairs ----------------------------------------------------------------


def _channel(batch_peak_id, channel):
    return SimpleNamespace(batch_peak_id=batch_peak_id, channel=channel)


def _anchor(batch_peak_id, tier="assigned", isotopologue_of=None):
    return SimpleNamespace(
        batch_peak_id=batch_peak_id,
        consensus_tier=tier,
        isotopologue_of=isotopologue_of,
    )


def _partition(n_neutrals=12, *, missing=None, seed=0):
    """Neutrals n0..n{k}, each through two channels that follow one abundance;
    each with an isotope line of its first channel. ``missing`` maps a trace
    to the spectra it lacks."""
    rng = np.random.default_rng(seed)
    traces, neutrals, anchors = {}, {}, {}
    common = rng.normal(0, 0.2, len(SAMPLES))
    for i in range(n_neutrals):
        own = rng.normal(0, 0.3, len(SAMPLES))
        a, b, iso = f"n{i:02d}a", f"n{i:02d}b", f"n{i:02d}i"
        for key, scale in ((a, 1.0), (b, 0.3), (iso, 0.05)):
            values = 10 ** (4 + common + own + rng.normal(0, 0.03, len(SAMPLES)))
            traces[key] = {
                s: float(v * scale)
                for s, v in zip(SAMPLES, values)
                if s not in (missing or {}).get(key, ())
            }
        neutrals[f"N{i:02d}"] = [_channel(a, "[M+H]+"), _channel(b, "[M+NH4]+")]
        anchors[a] = _anchor(a)
        anchors[b] = _anchor(b)
        anchors[iso] = _anchor(iso, isotopologue_of=a)
    part = SimpleNamespace(
        traces=sorted(k for k in traces if not k.endswith("i")),
        reagents=[],
        neutrals=neutrals,
    )
    return part, anchors, traces


def _pairs(part, anchors, traces, seed=421, decoys=5):
    return cn.read_partition_pairs(
        part, anchors, traces, np.random.default_rng(seed), decoys=decoys
    )


def test_a_partition_gives_each_channel_pair_once_its_decoys_and_isotopes():
    part, anchors, traces = _partition()
    pairs = _pairs(part, anchors, traces)
    kinds = [p.kind for p in pairs]
    assert kinds.count(cn.KIND_CHANNEL) == 12
    assert kinds.count(cn.KIND_DECOY) == 12 * 5
    assert kinds.count(cn.KIND_ISOTOPE) == 12
    # One species' channels and lines co-vary; chance pairs do not.
    channel = [p.r for p in pairs if p.kind == cn.KIND_CHANNEL]
    isotope = [p.r for p in pairs if p.kind == cn.KIND_ISOTOPE]
    decoy = [p.r for p in pairs if p.kind == cn.KIND_DECOY]
    assert min(channel) > 0.9 and min(isotope) > 0.9
    assert np.median(decoy) < 0.5


def test_a_decoy_belongs_to_its_channel_pair_and_never_to_the_same_neutral():
    part, anchors, traces = _partition()
    pairs = _pairs(part, anchors, traces)
    clusters = {p.cluster for p in pairs if p.kind == cn.KIND_CHANNEL}
    assert clusters == set(range(12))
    for cluster in clusters:
        mine = [p for p in pairs if p.kind == cn.KIND_DECOY and p.cluster == cluster]
        assert len(mine) == 5
        # The channel pair's own partner is never its decoy: no decoy reads
        # as high as the true pair.
        assert all(p.r < 0.9 for p in mine)


def test_two_channels_through_one_mechanism_are_not_a_pair():
    part, anchors, traces = _partition(n_neutrals=6)
    part.neutrals["N00"] = [_channel("n00a", "[M+H]+"), _channel("n00b", "[M+H]+")]
    pairs = _pairs(part, anchors, traces)
    assert sum(p.kind == cn.KIND_CHANNEL for p in pairs) == 5


def test_two_of_three_channels_through_one_mechanism_are_not_a_pair():
    part, anchors, traces = _partition(n_neutrals=6)
    traces["n00c"] = dict(traces["n00b"])
    part.traces.append("n00c")
    part.neutrals["N00"] = [
        _channel("n00a", "[M+H]+"),
        _channel("n00b", "[M+NH4]+"),
        _channel("n00c", "[M+H]+"),
    ]
    pairs = _pairs(part, anchors, traces)
    # n00a-n00b and n00b-n00c; n00a-n00c share a mechanism.
    assert sum(p.kind == cn.KIND_CHANNEL for p in pairs) == 5 + 2


def test_an_isotope_line_whose_parent_the_partition_does_not_read_is_left_out():
    part, anchors, traces = _partition(n_neutrals=6)
    # The parent is an anchor of the batch (a claimed peak, another
    # partition's) but not a trace of this partition.
    anchors["p"] = _anchor("p")
    anchors["q"] = _anchor("q", isotopologue_of="p")
    traces["p"] = dict(traces["n00a"])
    traces["q"] = dict(traces["n00i"])
    pairs = _pairs(part, anchors, traces)
    assert sum(p.kind == cn.KIND_ISOTOPE for p in pairs) == 6


def test_a_batch_draws_its_decoys_from_its_seed_and_its_own_id():
    first = cn.batch_rng(421, "batch-a").integers(0, 10**9, 5).tolist()
    assert first == cn.batch_rng(421, "batch-a").integers(0, 10**9, 5).tolist()
    assert first != cn.batch_rng(421, "batch-b").integers(0, 10**9, 5).tolist()
    assert first != cn.batch_rng(422, "batch-a").integers(0, 10**9, 5).tolist()


def test_a_decoy_is_matched_to_the_partners_prevalence():
    # Every trace but n00b's partner pool is present in all 60 spectra; n03a
    # and n03b in 30: a quarter off 60 is 45 to 75, so neither is a decoy for
    # a partner present in 60, and only they are for one present in 30.
    half = SAMPLES[30:]
    part, anchors, traces = _partition(missing={"n03a": half, "n03b": half})
    pairs = _pairs(part, anchors, traces, decoys=50)
    for p in pairs:
        if p.kind == cn.KIND_DECOY and p.cluster != 3:
            assert p.n_spectra == 60
    third = [p for p in pairs if p.kind == cn.KIND_DECOY and p.cluster == 3]
    # n03's partner holds 30 spectra; no other trace is within a quarter.
    assert third == []


def test_the_prevalence_is_the_partners_not_the_first_traces():
    # n03a, the first trace, is present in all 60 spectra; its partner n03b
    # and both of n05's traces in 30. n03's decoys are matched to n03b: n05's
    # two traces and nothing present in 60.
    half = SAMPLES[30:]
    part, anchors, traces = _partition(
        missing={"n03b": half, "n05a": half, "n05b": half}
    )
    pairs = _pairs(part, anchors, traces, decoys=50)
    third = [p for p in pairs if p.kind == cn.KIND_DECOY and p.cluster == 3]
    assert len(third) == 2
    # Read against n03a, which holds all 60, a decoy present in 30 shares 30.
    assert all(p.n_spectra == 30 for p in third)
    # n05's partner holds 30: its only decoy is n03b, the one other trace
    # within a quarter of it.
    fifth = [p for p in pairs if p.kind == cn.KIND_DECOY and p.cluster == 5]
    assert len(fifth) == 1


def test_a_channel_pairs_decoys_are_distinct_traces_of_other_neutrals():
    # u00 is a trace of the partition that reads no neutral (a committed row
    # without a notation): never a decoy. Each of the other eleven neutrals'
    # two traces is a decoy once, and no trace twice.
    part, anchors, traces = _partition()
    traces["u00"] = dict(traces["n00a"])
    part.traces.append("u00")
    pairs = _pairs(part, anchors, traces, decoys=50)
    for cluster in range(12):
        mine = [p.r for p in pairs if p.kind == cn.KIND_DECOY and p.cluster == cluster]
        assert len(mine) == 22
        assert len(set(mine)) == 22


def test_the_decoys_repeat_with_the_seed_and_move_with_another():
    part, anchors, traces = _partition()
    first = _pairs(part, anchors, traces, seed=1, decoys=3)
    again = _pairs(part, anchors, traces, seed=1, decoys=3)
    other = _pairs(part, anchors, traces, seed=2, decoys=3)
    assert first == again
    assert first != other


def test_an_isotope_pair_needs_both_rows_assigned_and_its_parent_read():
    part, anchors, traces = _partition(n_neutrals=4)
    anchors["n00i"].consensus_tier = "candidate"
    anchors["n01a"].consensus_tier = "candidate"
    # A line whose parent the partition does not read.
    anchors["x"] = _anchor("x", isotopologue_of="elsewhere")
    pairs = _pairs(part, anchors, traces)
    assert sum(p.kind == cn.KIND_ISOTOPE for p in pairs) == 2


def test_an_isotope_line_is_read_through_the_partitions_common_mode():
    part, anchors, traces = _partition(n_neutrals=8)
    pairs = _pairs(part, anchors, traces)
    from mascope_tools.composition.coherence import batch_residuals, read_co_variation

    residuals = batch_residuals({k: traces[k] for k in part.traces})
    expected = read_co_variation(
        residuals.trace("n00a"), residuals.residual_of(traces["n00i"])
    )
    isotope = [p for p in pairs if p.kind == cn.KIND_ISOTOPE][0]
    assert isotope.r == pytest.approx(expected.r)
    assert isotope.n_spectra == expected.n_spectra


# --- the statistics -----------------------------------------------------------


def _reading(kind, cluster, n, r, spread=0.05):
    return cn.PairReading(kind, cluster, n, r, spread, None)


def _stratum_pairs(rng, n_channel, true_share, n_spectra=100, decoys=20):
    pairs = []
    for c in range(n_channel):
        true = rng.random() < true_share
        r = rng.normal(0.9, 0.04) if true else rng.normal(0.0, 0.25)
        pairs.append(_reading(cn.KIND_CHANNEL, c, n_spectra, float(np.clip(r, -1, 1))))
        for _ in range(decoys):
            pairs.append(
                _reading(
                    cn.KIND_DECOY,
                    c,
                    n_spectra,
                    float(np.clip(rng.normal(0, 0.25), -1, 1)),
                )
            )
    return pairs


def test_a_stratum_with_many_true_pairs_clears():
    rng = np.random.default_rng(10)
    pairs = _stratum_pairs(rng, 200, 0.4)
    rows = cn.stratum_summary(pairs, np.random.default_rng(0), 50)
    row = rows[lr.stratum_of(100)]
    assert row["pairs"]["channel"] == 200
    assert row["true_share"] == pytest.approx(0.4, abs=0.1)
    assert row["bar_mixture"] is not None
    assert row["bar_bootstrap"]["with_bar"] >= cn.BOOTSTRAP_SUPPORT
    assert row["clears"]
    # The other strata hold nothing.
    assert not any(r["clears"] for i, r in enumerate(rows) if i != lr.stratum_of(100))


def test_a_stratum_of_too_few_channel_pairs_does_not_clear():
    rng = np.random.default_rng(11)
    pairs = _stratum_pairs(rng, cn.MIN_CHANNEL_PAIRS - 1, 0.6)
    row = cn.stratum_summary(pairs, np.random.default_rng(0), 50)[lr.stratum_of(100)]
    assert row["bar_mixture"] is not None
    assert not row["clears"]


def test_a_bar_the_resamples_seldom_find_does_not_clear(monkeypatch):
    rng = np.random.default_rng(10)
    pairs = _stratum_pairs(rng, 200, 0.4)
    monkeypatch.setattr(
        cn, "_bootstrap_bars", lambda clusters, rng, resamples: [None] * 90 + [0.9] * 10
    )
    row = cn.stratum_summary(pairs, np.random.default_rng(0), 100)[lr.stratum_of(100)]
    assert row["bar_mixture"] is not None
    assert row["bar_bootstrap"]["found"] == 10
    assert not row["clears"]


def test_a_stratum_of_chance_pairs_has_no_bar():
    rng = np.random.default_rng(12)
    pairs = _stratum_pairs(rng, 200, 0.0)
    row = cn.stratum_summary(pairs, np.random.default_rng(0), 50)[lr.stratum_of(100)]
    assert row["bar_mixture"] is None
    assert not row["clears"]


def test_the_null_tail_is_read_per_band_of_spread():
    pairs = [
        _reading(cn.KIND_DECOY, 0, 30, 0.7, spread=0.03),
        _reading(cn.KIND_DECOY, 0, 30, 0.1, spread=0.03),
        _reading(cn.KIND_DECOY, 0, 30, 0.9, spread=0.2),
        _reading(cn.KIND_DECOY, 0, 300, 0.9, spread=0.03),
    ]
    rows = cn.spread_summary(pairs)
    bands = rows[lr.stratum_of(30)]["bands"]
    assert bands[0]["n"] == 2 and bands[0]["share"] == 0.5
    assert bands[-1]["n"] == 1 and bands[-1]["share"] == 1.0
    assert rows[lr.stratum_of(300)]["bands"][0]["n"] == 1


def _batch(index, chemistry, pairs):
    return cn.BatchReading(index, chemistry, "orbi", 100, pairs)


def test_it_clears_on_two_reagents_only_where_two_chemistries_clear():
    rng = np.random.default_rng(13)
    one = _batch(1, "uronium", _stratum_pairs(rng, 150, 0.4))
    other = _batch(2, "15N-nitrate", _stratum_pairs(rng, 150, 0.4))
    none = _batch(3, "bromide", _stratum_pairs(rng, 150, 0.0))
    both = cn.summarise([one, other, none], seed=1, resamples=40)
    row = both["clears"][lr.stratum_of(100)]
    assert row["cleared_by"] == ["15N-nitrate", "uronium"]
    assert row["clears_on_two_reagents"]
    single = cn.summarise([one, none], seed=1, resamples=40)
    row = single["clears"][lr.stratum_of(100)]
    assert row["cleared_by"] == ["uronium"]
    assert not row["clears_on_two_reagents"]


def test_the_summary_repeats_with_its_seed():
    rng = np.random.default_rng(14)
    batch = _batch(1, "uronium", _stratum_pairs(rng, 80, 0.3))
    first = json.dumps(cn.summarise([batch], seed=5, resamples=30), sort_keys=True)
    again = json.dumps(cn.summarise([batch], seed=5, resamples=30), sort_keys=True)
    assert first == again


def test_the_outputs_render_and_the_table_literal_is_a_valid_table():
    rng = np.random.default_rng(15)
    result = cn.summarise(
        [_batch(1, "uronium", _stratum_pairs(rng, 120, 0.4))], seed=1, resamples=20
    )
    text = cn.markdown(result)
    assert "| uronium | 50-199 |" in text
    assert "does not clear on two reagents" in text
    source = cn.table_literal(result, 3, "made up")
    namespace = {
        "LikelihoodRatioTable": lr.LikelihoodRatioTable,
        "STRATA": lr.STRATA,
        "R_EDGES": lr.R_EDGES,
    }
    exec(source, namespace)
    table = namespace["TABLE"]
    assert table.version == 3
    assert table.provisional
    assert (
        table.bars[lr.stratum_of(100)]
        == result["groups"]["all"]["strata"][2]["bar_mixture"]
    )


def test_wilson_interval():
    low, high = cn.wilson(0, 10)
    assert low == 0.0 and 0.2 < high < 0.35
    assert cn.wilson(0, 0) == (None, None)
    low, high = cn.wilson(50, 100)
    assert low == pytest.approx(0.404, abs=0.002)
    assert high == pytest.approx(0.596, abs=0.002)


def test_a_batch_reads_its_partition_with_the_decoys_its_own_seed_draws():
    part, anchors, traces = _partition()
    rows = list(anchors.values())
    seen = []

    def partitions(anchors, notation_by_id):
        seen.append((len(anchors), notation_by_id))
        return {("mode", "orbi"): part}

    def read(batch_id):
        return cn.read_batch_pairs(
            1,
            batch_id,
            rows,
            {"m": "[M+H]+"},
            traces,
            decoys=3,
            partition_anchors=partitions,
        )

    first, again, other = read("batch-a"), read("batch-b"), read("batch-a")
    assert seen[0] == (len(rows), {"m": "[M+H]+"})
    (batch,) = first
    assert batch.chemistry == "charge transfer (+)"
    assert batch.instrument_type == "orbi"
    assert batch.spectra == len(SAMPLES)
    kinds = [p.kind for p in batch.pairs]
    assert kinds.count(cn.KIND_CHANNEL) == 12
    assert kinds.count(cn.KIND_ISOTOPE) == 12
    assert first[0].pairs == other[0].pairs
    decoys = [p for p in batch.pairs if p.kind == cn.KIND_DECOY]
    assert decoys != [p for p in again[0].pairs if p.kind == cn.KIND_DECOY]
    # The draws the batch's own generator makes.
    by_hand = cn.read_partition_pairs(
        part,
        anchors,
        traces,
        cn.batch_rng(cn.DEFAULT_SEED, "batch-a"),
        decoys=3,
        batch=1,
    )
    assert by_hand == batch.pairs
    assert {p.batch for p in batch.pairs} == {1}


def test_a_second_partitions_channel_pairs_are_clusters_of_their_own():
    part, anchors, traces = _partition()
    other, other_anchors, other_traces = _partition(seed=1)
    renamed = {f"x{k}": v for k, v in other_traces.items()}
    other.traces = [f"x{k}" for k in other.traces]
    other.neutrals = {
        f"X{n}": [_channel(f"x{c.batch_peak_id}", c.channel) for c in cs]
        for n, cs in other.neutrals.items()
    }
    for key, anchor in other_anchors.items():
        anchors[f"x{key}"] = _anchor(
            f"x{key}",
            isotopologue_of=anchor.isotopologue_of and f"x{anchor.isotopologue_of}",
        )
    traces.update(renamed)

    def partitions(anchors, notation_by_id):
        return {("mode", "orbi"): part, ("mode", "tof"): other}

    readings = cn.read_batch_pairs(
        3, "batch", list(anchors.values()), {}, traces, partition_anchors=partitions
    )
    clusters = [
        {p.cluster for p in reading.pairs if p.kind == cn.KIND_CHANNEL}
        for reading in readings
    ]
    assert clusters == [set(range(12)), set(range(12, 24))]


def test_two_batches_first_pairs_are_two_clusters(monkeypatch):
    seen = []

    def bars(clusters, rng, resamples):
        seen.append(len(clusters))
        return [None] * resamples

    monkeypatch.setattr(cn, "_bootstrap_bars", bars)
    rng = np.random.default_rng(16)
    one = _batch(1, "uronium", _stratum_pairs(rng, 40, 0.4))
    two = _batch(2, "uronium", _stratum_pairs(rng, 40, 0.4))
    for batch in (one, two):
        batch.pairs = [replace(p, batch=batch.index) for p in batch.pairs]
    cn.summarise([one, two], seed=1, resamples=5)
    # Per chemistry and over every batch: 80 channel pairs, not 40.
    assert 80 in seen and 40 not in seen
