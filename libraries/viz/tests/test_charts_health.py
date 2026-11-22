"""
The first four tier-1 chart kinds, on the synthetic campaign: ``coverage``,
``instrument_signal``, ``scope_summary`` and ``top_species``.

The assertions are against the answers the campaign was built from
(:class:`fixtures.campaign.Truth`): the gap day and its unfiled files, the
TIC step, the reagent drift, the run change and the episodes. Each kind
also renders on both backends and survives its document's round trip.
"""

import dataclasses
import inspect
import json

import numpy as np
import pandas as pd
import pytest
from fixtures.campaign import HALIDE_NEGATIVE, Campaign, generate
from fixtures.server import SCOPE, FakeClient

from mascope_viz import render
from mascope_viz.charts import Figure, registry
from mascope_viz.charts.coverage import coverage, row_labels
from mascope_viz.charts.signal import drift, instrument_signal, largest_step
from mascope_viz.charts.summary import scope_summary
from mascope_viz.charts.top import _period_end, distinct_keys, top_species
from mascope_viz.data import store as store_module
from mascope_viz.data.store import CampaignStore, sync


ZONE = "Europe/Helsinki"

KINDS = ("coverage", "instrument_signal", "scope_summary", "top_species")


@pytest.fixture(scope="module")
def month(synced) -> tuple[Campaign, CampaignStore]:
    campaign, _, report = synced
    return campaign, report.store


@pytest.fixture(scope="module")
def clusters(cluster_campaign, tmp_path_factory) -> tuple[Campaign, CampaignStore]:
    """The month whose reagent is spread over the bare ion and its water
    cluster (MAS-1094's two-reagent set), synced."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_module, "_now", lambda: pd.Timestamp("2026-08-01T00:00Z"))
        patch.setattr(store_module.time, "sleep", lambda seconds: None)
        report = sync(
            FakeClient(cluster_campaign),
            SCOPE,
            root=tmp_path_factory.mktemp("clusters"),
        )
    return cluster_campaign, report.store


def _reagent(campaign: Campaign) -> list[str]:
    return [] if campaign.truth.drift is None else [campaign.truth.drift.series_key]


def _day(moment: pd.Timestamp) -> pd.Timestamp:
    return moment.tz_convert(ZONE).normalize()


def _trace(fig: Figure, key: str) -> dict:
    return next(trace for trace in fig.summary["traces"] if trace["key"] == key)


# --- the registry ------------------------------------------------------------------


def test_the_four_kinds_are_registered_with_their_builders():
    builders = {
        "coverage": coverage,
        "instrument_signal": instrument_signal,
        "scope_summary": scope_summary,
        "top_species": top_species,
    }

    assert set(KINDS) <= set(registry.ids())
    for kind_id, build in builders.items():
        kind = registry.get(kind_id)
        assert kind.build is build
        assert kind.tier == 1
        json.dumps(kind.to_dict())
        # The registry says what the function takes, with its defaults
        signature = inspect.signature(build)
        taken = [name for name in signature.parameters if name != "store"]
        assert [param.name for param in kind.params] == taken, kind_id
        for param in kind.params:
            assert param.default == signature.parameters[param.name].default


@pytest.mark.parametrize("kind_id", KINDS)
def test_each_summary_has_the_fields_its_kind_names(month, kind_id):
    campaign, store = month
    params = {"reagents": _reagent(campaign)} if kind_id != "coverage" else {}
    if kind_id == "top_species":
        params = {}

    fig = registry.get(kind_id).build(store, **params)

    assert fig.id == kind_id
    assert set(registry.get(kind_id).summary) == set(fig.summary)
    # JSON-safe: times as ISO 8601 with the zone's offset, NaN as null
    text = json.dumps(fig.summary, allow_nan=False)
    assert "+03:00" in text


@pytest.mark.parametrize("kind_id", KINDS)
def test_each_kind_renders_on_both_backends(month, kind_id, tmp_path):
    campaign, store = month
    params = {"reagents": _reagent(campaign)}
    if kind_id in ("coverage", "top_species"):
        params = {}
    if kind_id == "scope_summary":
        params["generated_at"] = "2026-08-01T00:00Z"
    if kind_id == "top_species":
        # A day against the day before: a short figure, three lists
        day = _day(campaign.truth.episodes[0].start)
        params = {"period": "day", "start": day - pd.Timedelta(days=1)}
        params["end"] = day + pd.Timedelta(days=1)

    fig = registry.get(kind_id).build(store, **params)

    assert Figure.from_json(fig.to_json()) == fig
    drawn = render.matplotlib(fig)
    assert len(drawn.axes) >= 1
    assert len(render.plotly(fig).data) >= 1 or kind_id in (
        "scope_summary",
        "top_species",
    )
    render.save(fig, tmp_path / f"{kind_id}.png")
    render.save(fig, tmp_path / f"{kind_id}.html", plotlyjs="cdn")
    assert (tmp_path / f"{kind_id}.png").stat().st_size > 0


# --- coverage ------------------------------------------------------------------------


def test_coverage_shows_the_gap_day_and_its_unfiled_files(month):
    campaign, store = month
    gap = campaign.truth.gap

    fig = coverage(store)

    summary = fig.summary
    assert summary["gaps"] == [
        {
            "start": gap.start.tz_convert(ZONE).isoformat(),
            "end": gap.end.tz_convert(ZONE).isoformat(),
            "hours": 24.0,
            "n_files": gap.files,
            "n_unfiled": gap.files,
        }
    ]
    assert summary["hours_span"] == 30 * 24
    assert summary["hours_missing"] == 24
    assert summary["hours_acquired"] == 29 * 24
    assert summary["coverage_fraction"] == pytest.approx(29 / 30)
    assert summary["span"] == {
        "start": campaign.truth.start.tz_convert(ZONE).isoformat(),
        "end": campaign.truth.end.tz_convert(ZONE).isoformat(),
    }
    assert summary["n_unfiled"] == gap.files
    assert summary["n_files"] == len(campaign.tables["files"])
    assert summary["n_samples"] == len(campaign.tables["samples"])
    # The gap is shaded
    (panel,) = fig.panels
    (span,) = panel.options["spans"]
    assert (span["start"], span["end"], span["kind"]) == (gap.start, gap.end, "gap")
    # The unfiled files are marked, in a row of their own, in the gap alone
    data = fig.data
    marked = data[data["unfiled"]]
    assert set(marked["row"]) == {
        f"{'negative' if campaign.params.charge < 0 else 'positive'} / in no batch"
    }
    assert marked["t"].min() == gap.start
    assert marked["t"].max() == gap.end - pd.Timedelta(hours=1)
    assert marked["n_unfiled"].sum() == gap.files
    assert panel.encodings == {
        "x": "t",
        "y": "row",
        "value": "n_samples",
        "mark": "unfiled",
    }
    assert "gaps of at least 1h shaded" in fig.subtitle
    assert fig.notes[0].startswith(f"{gap.files} of {summary['n_files']} files")


def test_coverage_has_a_row_per_polarity_and_mode(month):
    campaign, store = month
    polarity = "negative" if campaign.params.charge < 0 else "positive"

    fig = coverage(store)

    rows = {row["row"]: row for row in fig.summary["rows"]}
    assert set(rows) == {f"{polarity} / Campaign mode", f"{polarity} / in no batch"}
    filed = rows[f"{polarity} / Campaign mode"]
    assert filed["mode"] == "im-1" and filed["mode_name"] == "Campaign mode"
    assert filed["n_samples"] == len(campaign.tables["samples"])
    assert rows[f"{polarity} / in no batch"]["mode"] is None
    assert list(fig.data["row"].cat.categories) == list(rows)
    # Files per hour are in the frame, beside the samples
    assert fig.data["n_files"].sum() == len(campaign.tables["files"])


def test_coverage_names_a_modes_id_when_two_modes_share_its_name():
    bins = pd.DataFrame(
        {
            "polarity": ["+", "+", "-", None, "+"],
            "mode": ["m1", "m2", "m3", None, "m4"],
            "mode_name": ["Spring", "Spring", "Autumn", None, None],
        }
    )

    assert row_labels(bins) == [
        "positive / Spring (m1)",
        "positive / Spring (m2)",
        "negative / Autumn",
        "in no batch",
        "positive / mode m4",
    ]


def test_coverage_of_a_window_and_a_coarser_cell(month):
    campaign, store = month
    gap = campaign.truth.gap

    fig = coverage(
        store,
        cadence="6h",
        min_gap="2D",
        start=gap.start - pd.Timedelta(days=1),
        end=gap.end + pd.Timedelta(days=1),
    )

    assert fig.panels[0].options["width"] == "6h"
    assert fig.panels[0].axes.colour.label == "Samples per 6h"
    assert fig.summary["gaps"] == [] and fig.panels[0].options["spans"] == []
    assert (
        fig.summary["start"]
        == (gap.start - pd.Timedelta(days=1)).tz_convert(ZONE).isoformat()
    )
    assert fig.context.start is not None


@pytest.mark.parametrize("cadence", ["W", "MS"])
def test_coverage_refuses_a_calendar_cadence(month, cadence):
    """The view bins by a calendar period, but a strip's cells share one
    width: the chart says so rather than failing on its label."""
    _, store = month
    with pytest.raises(ValueError, match="calendar period"):
        coverage(store, cadence=cadence)
    assert coverage(store, cadence="7D").panels[0].axes.colour.label == (
        "Samples per 7 days"
    )


# --- instrument_signal ---------------------------------------------------------------


def test_instrument_signal_finds_the_tic_step_at_its_time(month):
    campaign, store = month
    step = campaign.truth.tic_step

    fig = instrument_signal(store, reagents=_reagent(campaign))

    found = _trace(fig, "tic")["step"]
    assert abs(pd.Timestamp(found["at"]) - step.at) <= pd.Timedelta(hours=1)
    assert found["factor"] == pytest.approx(step.after / step.before, rel=0.02)
    assert found["window"] == "1 day"
    assert _trace(fig, "tic")["drift"] == pytest.approx(0.6, rel=0.02)
    assert fig.panels[0].axes.y.label == "TIC, mean per scan"
    assert fig.panels[0].axes.y.scale == "log"
    assert fig.context.cadence == "1h" and fig.context.band == [10.0, 90.0]
    assert fig.subtitle.startswith("1h medians, p10-p90 band")
    # The summary spans the first bin's start to the last one's end
    assert fig.summary["start"] == campaign.truth.start.tz_convert(ZONE).isoformat()
    assert fig.summary["end"] == campaign.truth.end.tz_convert(ZONE).isoformat()


@pytest.mark.parametrize("campaign", ["halide_negative"], indirect=True)
def test_instrument_signal_reads_the_reagent_drift(month):
    campaign, store = month
    drift = campaign.truth.drift
    gap = campaign.truth.gap

    fig = instrument_signal(store, reagents=[drift.series_key])

    reagent = _trace(fig, drift.series_key)
    # The first and last full local days, 2 and 30 June: their middles
    first, last = 1.375, 29.375
    want = (drift.intercept + drift.slope_per_day * last) / (
        drift.intercept + drift.slope_per_day * first
    )
    assert reagent["drift"] == pytest.approx(want, rel=0.01)
    assert reagent["drift"] == pytest.approx(0.65, abs=0.02)
    assert reagent["name"] == "reagent ion" and reagent["presence"] == 1.0
    # A drift is no step, and the gap day makes none
    for trace in fig.summary["traces"]:
        at = pd.Timestamp(trace["step"]["at"])
        assert not gap.start - pd.Timedelta(days=1) <= at <= gap.end, trace["key"]
    assert 0.95 < reagent["step"]["factor"] < 1.0
    assert fig.panels[1].axes.y.label == "Peak area"
    assert fig.panels[1].where == {"panel": ["reagents"]}
    assert [panel.title for panel in fig.panels] == ["TIC", "Reagent ions"]
    assert fig.share_x


def test_instrument_signal_without_a_reagent_draws_the_tic_alone(month):
    _, store = month

    fig = instrument_signal(store)

    assert len(fig.panels) == 1
    assert set(fig.data["key"]) == {"tic"}
    assert fig.summary["reagents"] == [] and fig.summary["shares"] == []
    assert fig.notes[0].startswith("No reagent ion was named")


@pytest.mark.parametrize("campaign", ["halide_negative"], indirect=True)
def test_one_reagent_ion_has_no_sum_and_no_shares(month):
    campaign, store = month

    fig = instrument_signal(store, reagents=campaign.truth.drift.series_key)

    assert set(fig.data["key"]) == {"tic", campaign.truth.drift.series_key}
    assert set(fig.data["panel"]) == {"tic", "reagents"}
    assert not any(note.startswith("The sum adds") for note in fig.notes)


def test_two_reagent_ions_draw_their_sum_and_their_shares(clusters):
    campaign, store = clusters
    bare, cluster = campaign.truth.shares.series_keys
    total = f"{bare}+{cluster}"
    drift = campaign.truth.drift

    fig = instrument_signal(store, reagents=[bare, cluster])

    data = fig.data
    assert set(data.loc[data["panel"] == "reagents", "key"]) == {bare, cluster, total}
    assert set(data.loc[data["panel"] == "shares", "key"]) == {bare, cluster}
    assert [panel.title for panel in fig.panels] == [
        "TIC",
        "Reagent ions and their sum",
        "Each reagent ion's share of the sum",
    ]
    names = dict(zip(data["key"], data["name"]))
    assert names[total] == "reagent ion + reagent water cluster"
    # The shares sum to one where both ions have a value
    shares = data[data["panel"] == "shares"].pivot(
        index="t", columns="key", values="value"
    )
    both = shares.dropna()
    assert len(both) > 600
    np.testing.assert_allclose(both[bare] + both[cluster], 1.0, rtol=1e-9)
    # Where the cluster is lost, the bare ion is the whole sum
    lost = shares[shares[cluster].isna() & shares[bare].notna()]
    assert len(lost) and np.allclose(lost[bare], 1.0)
    # The sum drifts at the fixture's slope
    first, last = 1.375, 29.375
    want = (drift.intercept + drift.slope_per_day * last) / (
        drift.intercept + drift.slope_per_day * first
    )
    assert _trace(fig, total)["drift"] == pytest.approx(want, rel=0.01)
    # Each member's share cycles between the fixture's trough and peak
    by_key = {row["key"]: row for row in fig.summary["shares"]}
    truth = campaign.truth.shares
    assert truth.trough < by_key[bare]["median_share"] < truth.peak
    assert by_key[bare]["median_share"] + by_key[cluster][
        "median_share"
    ] == pytest.approx(1.0, abs=0.01)
    # A share's diurnal cycle makes no step, beside the gap day either
    for key in (bare, cluster, total):
        assert 0.95 < _trace(fig, key)["step"]["factor"] < 1.05, key
    assert any(note.startswith("The sum adds the reagent ions") for note in fig.notes)
    assert fig.panels[2].axes.y.range == (0.0, 1.0)


def test_instrument_signal_says_where_its_normaliser_was_a_partial_sum(clusters):
    campaign, store = clusters
    keys = list(campaign.truth.shares.series_keys)
    lost = campaign.truth.shares.lost_samples

    fig = instrument_signal(store, normalise=keys)

    share = lost / len(campaign.tables["samples"])
    assert fig.notes[1] == (
        f"The denominator was a partial sum in {share:.1%} of the samples: "
        "one of its ions had no value there, and the others divided."
    )
    assert fig.context.normalise_members == keys
    assert "normalised to reagent ion + reagent water cluster" in fig.subtitle
    whole = instrument_signal(store, normalise=keys[0])
    assert not any(note.startswith("The denominator") for note in whole.notes)


def test_instrument_signal_says_its_normalisation(month):
    campaign, store = month

    fig = instrument_signal(store, reagents=_reagent(campaign), normalise="tic")

    assert "normalised to TIC" in fig.subtitle
    assert fig.panels[0].axes.y.label == "TIC, mean per scan / TIC"
    if campaign.truth.drift is not None:
        assert fig.panels[1].axes.y.label == "signal / TIC"
    assert fig.summary["normalise"] == "tic"


def test_instrument_signal_draws_a_run_change_its_normaliser_reads(month):
    campaign, store = month
    change = campaign.truth.run_change
    # A formula read the same by both runs, as the normaliser
    renamed = {key for pair in change.readings for key in pair}
    formula = next(
        key
        for key in sorted(campaign.truth.cycles)
        if key.startswith("formula:") and key not in renamed
    )

    fig = instrument_signal(store, normalise=formula)

    assert fig.summary["mixed_runs"] is True
    (rule,) = fig.panels[0].options["rules"]
    assert rule["at"] == change.at
    assert rule["label"].startswith("engine run:")
    assert fig.summary["run_changes"][0]["at"] == change.at.tz_convert(ZONE).isoformat()
    assert any(note.startswith("The engine run that reads") for note in fig.notes)


def test_instrument_signal_refuses_the_tic_or_a_key_twice_as_a_reagent(month):
    _, store = month

    with pytest.raises(ValueError, match="TIC"):
        instrument_signal(store, reagents=["tic"])
    with pytest.raises(ValueError, match="twice"):
        instrument_signal(store, reagents=["ion:ti0001", "ion:ti0001"])


def test_a_step_compares_the_same_hours_beside_a_gap():
    """A strong diurnal cycle and a day without samples: a window that
    half reaches into the gap holds the evening alone, and an unpaired
    median would read the cycle as a step."""
    t = pd.date_range("2026-06-01", periods=24 * 20, freq="1h", tz=ZONE)
    hours = np.arange(len(t)) % 24
    cycle = 1.0 + 0.6 * np.cos(2 * np.pi * (hours - 4) / 24)
    values = 1000.0 * cycle
    values[24 * 10 : 24 * 11] = np.nan

    found = largest_step(pd.Series(t), values, pd.Timedelta("24h"))

    assert found["factor"] == pytest.approx(1.0, abs=1e-9)
    stepped = values * np.where(np.arange(len(t)) >= 24 * 5 + 7, 0.5, 1.0)
    found = largest_step(pd.Series(t), stepped, pd.Timedelta("24h"))
    assert pd.Timestamp(found["at"]) == t[24 * 5 + 7]
    assert found["factor"] == pytest.approx(0.5)
    assert largest_step(pd.Series(t[:1]), values[:1], pd.Timedelta("24h")) is None


def test_a_step_needs_more_than_half_a_window_of_pairs():
    """A dip in the last few bins is too short to compare: each side of a
    step holds more than half a window of paired bins."""
    t = pd.date_range("2026-06-01", periods=24 * 6, freq="1h", tz=ZONE)
    values = np.full(len(t), 1000.0) * (1 + 0.001 * (np.arange(len(t)) % 3))
    values[-3:] *= 0.3

    found = largest_step(pd.Series(t), values, pd.Timedelta("24h"))

    assert found["factor"] == pytest.approx(1.0, abs=0.01)


def test_a_step_is_read_over_the_paired_bins_alone():
    """A bin without a value drops its pair, not the whole comparison."""
    t = pd.date_range("2026-06-01", periods=24 * 8, freq="1h", tz=ZONE)
    values = np.full(len(t), 1000.0) * (1 + 0.001 * (np.arange(len(t)) % 5))
    at = 24 * 4
    values[at:] *= 0.5
    values[::7] = np.nan

    found = largest_step(pd.Series(t), values, pd.Timedelta("24h"))

    assert found is not None
    assert abs(pd.Timestamp(found["at"]) - t[at]) <= pd.Timedelta(hours=1)
    assert found["factor"] == pytest.approx(0.5, rel=0.01)


def test_a_drift_passes_over_a_day_holding_too_few_values():
    t = pd.date_range("2026-06-01", periods=24 * 5, freq="1h", tz=ZONE)
    values = np.full(len(t), 1000.0)
    # The last day holds five values, at half the level
    values[24 * 4 :] = np.nan
    values[24 * 4 : 24 * 4 + 5] = 500.0

    assert drift(pd.Series(t), values) == pytest.approx(1.0)
    values[24 * 4 : 24 * 4 + 13] = 500.0
    assert drift(pd.Series(t), values) == pytest.approx(0.5)


@pytest.mark.parametrize("last", [True, False], ids=["last-day", "first-day"])
@pytest.mark.parametrize(
    ("change", "hours"),
    [("2026-03-29", 23), ("2026-10-25", 25)],
    ids=["spring-23h", "autumn-25h"],
)
def test_a_drift_reads_a_day_across_a_clock_change(change, hours, last):
    """29 March 2026 is 23 hours long in Helsinki and 25 October 25 hours:
    a day's end is the next midnight on the wall clock, not 24 hours on."""
    clock_day = pd.Timestamp(change, tz=ZONE)
    # Six full days, the clock change's the last of them or the first
    start = clock_day - pd.DateOffset(days=5) if last else clock_day
    t = pd.date_range(start, start + pd.DateOffset(days=6), freq="1h", inclusive="left")
    on_clock_day = t.normalize() == clock_day
    assert on_clock_day.sum() == hours
    # The half-level day is the last full day, or every day after the first
    values = np.where(on_clock_day == last, 500.0, 1000.0)

    assert drift(pd.Series(t), values) == pytest.approx(0.5)


@pytest.mark.parametrize("width", ["6min", "7min", "11min"])
def test_a_step_pairs_bins_that_do_not_divide_the_window(width):
    """A bin pairs with the bin nearest one window before it: 7-minute bins
    do not divide a day, and an exact match would pair none."""
    t = pd.date_range("2026-06-01", "2026-06-05", freq=width, tz=ZONE, inclusive="left")
    at = len(t) // 2
    values = np.full(len(t), 1000.0) * (1 + 0.001 * (np.arange(len(t)) % 3))
    values[at:] *= 0.5

    found = largest_step(pd.Series(t), values, pd.Timedelta("24h"))

    assert found is not None
    assert abs(pd.Timestamp(found["at"]) - t[at]) <= pd.Timedelta(width)
    assert found["factor"] == pytest.approx(0.5, rel=0.01)


def test_a_step_pairs_irregular_samples_and_never_a_bin_with_itself():
    rng = np.random.default_rng(5)
    offsets = np.cumsum(rng.uniform(5, 9, size=4 * 24 * 60 // 7)) * 60e9
    t = pd.DatetimeIndex(
        pd.Timestamp("2026-06-01", tz=ZONE).value + offsets.astype("int64"), tz="UTC"
    ).tz_convert(ZONE)
    at = len(t) // 2
    values = np.full(len(t), 1000.0)
    values[at:] = 250.0

    found = largest_step(pd.Series(t), values, pd.Timedelta("24h"))
    assert found["factor"] == pytest.approx(0.25)
    assert abs(pd.Timestamp(found["at"]) - t[at]) <= pd.Timedelta("10min")
    # A window shorter than a bin finds no partner before any bin
    assert largest_step(pd.Series(t), values, pd.Timedelta("1min")) is None


def test_a_step_on_local_days_pairs_across_a_clock_change():
    """Daily bins in local time are 23 or 25 hours apart across a clock
    change; the days beside it still pair."""
    t = pd.date_range("2026-10-15", "2026-11-05", freq="1D", tz=ZONE, inclusive="left")
    at = list(t).index(pd.Timestamp("2026-10-26", tz=ZONE))
    values = np.where(np.arange(len(t)) >= at, 400.0, 1000.0)

    found = largest_step(pd.Series(t), values, pd.Timedelta("3D"))

    assert pd.Timestamp(found["at"]) == t[at]
    assert found["factor"] == pytest.approx(0.4)


def test_a_step_on_a_flat_noisy_trace_is_placed_at_the_step():
    """Without a cycle the median ratio is the same for most of a window
    either side of a step, and noise alone would pick among those times."""
    t = pd.date_range("2026-06-01", periods=24 * 10, freq="1h", tz=ZONE)
    rng = np.random.default_rng(3)
    values = 1000.0 * (1 + 0.003 * rng.standard_normal(len(t)))
    at = 24 * 4 + 13
    values[at:] *= 0.6

    found = largest_step(pd.Series(t), values, pd.Timedelta("24h"))

    assert pd.Timestamp(found["at"]) == t[at]
    assert found["factor"] == pytest.approx(0.6, rel=0.01)


# --- scope_summary -------------------------------------------------------------------


def test_scope_summary_flags_the_run_change(month):
    campaign, store = month
    change = campaign.truth.run_change

    fig = scope_summary(store, generated_at="2026-08-01T00:00Z")

    marked = fig.data[fig.data["run_change"]]
    assert list(marked["item"]) == ["Engine runs", "Run change"]
    at = change.at.tz_convert(ZONE)
    assert marked["value"].iloc[1].startswith(f"{at:%Y-%m-%d %H:%M}, ")
    assert marked["value"].iloc[1].endswith("mascope 0.5.0 to mascope 0.6.0")
    summary = fig.summary
    assert summary["mixed_runs"] is True
    assert [c["at"] for c in summary["run_changes"]] == [at.isoformat()]
    assert [run["engine_version"] for run in summary["runs"]] == ["0.5.0", "0.6.0"]
    assert fig.context.mixed_runs is True
    assert fig.panels[0].encodings["mark"] == "run_change"
    assert fig.notes


def test_scope_summary_says_what_the_store_holds(month):
    campaign, store = month
    reagent = _reagent(campaign)

    fig = scope_summary(store, reagents=reagent, generated_at="2026-08-01T00:00Z")

    rows = dict(zip(fig.data["item"], fig.data["value"]))
    summary = fig.summary
    tof = campaign.params.instrument_type == "TOF"
    assert rows["Datasets"] == "Campaign"
    assert rows["Collections"] == "Targets"
    assert rows["Batches"] == "29" and summary["n_batches"] == 29
    assert rows["Samples"] == str(len(campaign.tables["samples"]))
    assert rows["Files"] == f"{len(campaign.tables['files'])}, 144 of them in no batch"
    assert rows["Span"] == "2026-06-01 03:00 to 2026-07-01 03:00"
    assert rows["Time zone"] == ZONE
    assert rows["Instrument"] == f"instrument X ({campaign.params.instrument_type})"
    assert rows["Intensity"] == ("peak area" if tof else "peak height")
    assert rows["Chemistry"] == (
        f"{'negative' if tof else 'positive'}; modes Campaign mode"
    )
    assert rows["Reagent ions"] == ("reagent ion" if reagent else "none named")
    assert rows["Normalisation"] == "none: raw values"
    assert rows["Cadence"] == "auto: 1min"
    assert rows["Sample spacing"] == "1min (median)"
    assert rows["Coverage"] == "96.7% of the span acquired"
    assert rows["Below detection"].startswith("missing, not zero")
    assert rows["Versions"].startswith("mascope_viz ")
    assert rows["Server"] == "Mascope v1.12.0, engine 0.5.0"
    assert rows["Generated"] == "2026-08-01 03:00"
    assert summary["datasets"] == [{"id": "d1", "name": "Campaign"}]
    assert summary["collections"] == [{"id": "c1", "name": "Targets"}]
    assert summary["sample_spacing_seconds"] == 60.0
    assert summary["coverage_fraction"] == pytest.approx(29 / 30)
    assert summary["generated_at"] == "2026-08-01T03:00:00+03:00"
    assert summary["intensity_variable"] == ("peak area" if tof else "peak height")
    assert summary["below_detection"] == "missing"
    assert summary["provenance"]["produced_with"]["mascope_version"] == "v1.12.0"


def test_scope_summary_lists_the_normalisers_members_by_name(clusters):
    campaign, store = clusters
    keys = list(campaign.truth.shares.series_keys)

    fig = scope_summary(store, reagents=keys, normalise=keys, cadence="1h")

    rows = dict(zip(fig.data["item"], fig.data["value"]))
    assert rows["Normalisation"] == "reagent ion + reagent water cluster"
    assert rows["Normaliser members"] == "reagent ion, reagent water cluster"
    assert rows["Reagent ions"] == "reagent ion, reagent water cluster"
    assert rows["Cadence"] == "1h"
    assert [m["key"] for m in fig.summary["normalise_members"]] == keys
    assert fig.summary["normalise"] == keys
    assert fig.context.normalise_members == keys


def test_scope_summary_of_a_store_without_names_shows_the_ids(month, monkeypatch):
    _, store = month
    meta = {
        name: value
        for name, value in store.meta.items()
        if name not in ("datasets", "collections")
    }
    meta["collection"] = "c9"
    monkeypatch.setattr(CampaignStore, "meta", property(lambda self: meta))

    fig = scope_summary(store, generated_at="2026-08-01T00:00Z")

    rows = dict(zip(fig.data["item"], fig.data["value"]))
    assert rows["Datasets"] == "d1"
    assert rows["Collections"] == "c9"


def test_the_sync_records_the_datasets_and_collections_names(month):
    _, store = month

    assert store.meta["datasets"] == [{"id": "d1", "name": "Campaign"}]
    assert store.meta["collections"] == [{"id": "c1", "name": "Targets"}]


def test_scope_summary_refuses_a_key_the_store_does_not_hold(month):
    _, store = month

    with pytest.raises(ValueError, match="ion:nope"):
        scope_summary(store, reagents=["ion:nope"])


# --- top_species ---------------------------------------------------------------------


@pytest.mark.parametrize("campaign", ["halide_negative"], indirect=True)
def test_top_species_raw_is_the_reagent_drift_and_normalising_removes_it(month):
    campaign, store = month
    reagent = campaign.truth.drift.series_key

    raw = top_species(store, n=100)
    normalised = top_species(store, n=100, normalise=reagent)

    def changes(fig: Figure) -> np.ndarray:
        ratios = [row["ratio"] for row in fig.summary["ratio"]]
        return np.abs(np.log(np.array(ratios, dtype=float)))

    # By week over the month, every species falls with the reagent ion: the
    # samples of the first local week lie 3.44 days from the start on
    # average, those of the last (29 and 30 June) 28.94
    drift = campaign.truth.drift
    fall = (drift.intercept + drift.slope_per_day * 28.94) / (
        drift.intercept + drift.slope_per_day * 3.44
    )
    assert np.median(changes(raw)) == pytest.approx(-np.log(fall), abs=0.03)
    assert np.median(changes(normalised)) < 0.1
    assert "normalised to reagent ion" in normalised.subtitle
    # Without a window, the summary spans the weeks compared
    assert raw.summary["start"] == "2026-06-01T00:00:00+03:00"
    assert raw.summary["end"] == "2026-07-06T00:00:00+03:00"
    assert normalised.summary["normalise"] == reagent


def test_top_species_leads_with_the_episode_species(month):
    campaign, store = month
    episode = campaign.truth.episodes[0]
    day = _day(episode.start)
    want = set(distinct_keys(store, list(episode.series_keys)))

    fig = top_species(
        store,
        n=len(want),
        period="day",
        start=day - pd.Timedelta(days=1),
        end=day + pd.Timedelta(days=1),
    )

    ratio = fig.summary["ratio"]
    assert {row["key"] for row in ratio} == want
    assert all(row["ratio"] > 1.5 for row in ratio)
    assert fig.summary["first_period"] == (day - pd.Timedelta(days=1)).isoformat()
    assert fig.summary["last_period"] == day.isoformat()
    assert f"the day from {day:%Y-%m-%d} over the day from" in fig.subtitle
    assert not fig.data["reread"].any()


def test_top_species_flags_the_formulas_new_at_a_run_change(month):
    campaign, store = month
    change = campaign.truth.run_change

    fig = top_species(
        store,
        period="day",
        tz="UTC",
        start=change.at - pd.Timedelta(days=1),
        end=change.at + pd.Timedelta(days=1),
    )

    new = fig.summary["new"]
    assert {row["key"] for row in new} == {key for _, key in change.readings}
    assert all(row["reread"] for row in new)
    assert fig.summary["last_period"] == change.at.isoformat()
    assert fig.notes[0].startswith("The engine run changes in the last period")
    marked = fig.data[fig.data["reread"]]
    assert set(marked["list"]) == {"new"}
    # Before the change, nothing is flagged
    before = top_species(
        store,
        period="day",
        tz="UTC",
        start=change.at - pd.Timedelta(days=2),
        end=change.at,
    )
    assert not before.data["reread"].any()


def test_top_species_lists_a_target_ion_and_its_formula_once(month):
    campaign, store = month
    species = store.table("species").set_index("series_key")
    ions = [key for key in campaign.truth.cycles if key.startswith("ion:")]
    twins = {
        key
        for key in species.index
        if key.startswith("formula:")
        and any(
            (species.at[key, "formula"], species.at[key, "mechanism"])
            == (species.at[ion, "formula"], species.at[ion, "mechanism"])
            for ion in ions
        )
    }

    fig = top_species(store, n=100)

    assert len(twins) == campaign.params.n_targets
    listed = set(fig.data["key"])
    assert not listed & twins
    assert set(ions) <= set(distinct_keys(store, None))
    assert not set(distinct_keys(store, None)) & twins
    for by in ("median", "ratio", "new"):
        keys = [row["key"] for row in fig.summary[by]]
        assert len(keys) == len(set(keys))
    assert [panel.title for panel in fig.panels] == [
        "Brightest: by median",
        "Biggest changers: last period over the first",
        "New: in the last period, absent before",
    ]
    assert fig.summary["median"][0]["presence"] == 1.0


def test_top_species_ranks_only_the_species_present_enough(month):
    _, store = month

    every = top_species(store, n=100, min_present=0)
    most = top_species(store, n=100, min_present=0.95)

    presence = {row["key"]: row["presence"] for row in every.summary["median"]}
    assert {row["key"] for row in most.summary["median"]} == {
        key for key, share in presence.items() if share >= 0.95
    }
    assert "present in 95% of the samples compared" in most.subtitle
    assert most.summary["min_present"] == 0.95


def test_a_period_ends_on_the_wall_clock_across_a_clock_change():
    week = pd.Timestamp("2026-10-19", tz=ZONE)
    month = pd.Timestamp("2026-10-01", tz=ZONE)

    assert _period_end(week, "week") == pd.Timestamp("2026-10-26", tz=ZONE)
    assert _period_end(month, "month") == pd.Timestamp("2026-11-01", tz=ZONE)
    assert _period_end(week, "day") == pd.Timestamp("2026-10-20", tz=ZONE)


def test_top_species_of_one_period_says_it_compares_nothing(month):
    campaign, store = month
    day = _day(campaign.truth.run_change.at)

    fig = top_species(store, period="day", start=day, end=day + pd.Timedelta(days=1))

    assert f"one day only, from {day:%Y-%m-%d}: no ratio, nothing new" in fig.subtitle
    assert fig.summary["ratio"] == [] and fig.summary["new"] == []
    assert len(fig.summary["median"]) == 10


#: Four days, the third unfiled, the run changing on the second.
SHORT = dataclasses.replace(
    HALIDE_NEGATIVE,
    days=4,
    gap_day=2,
    run_change_day=1,
    n_species=8,
    n_targets=3,
    episodes=(),
)


@pytest.fixture(scope="module")
def late_species(tmp_path_factory) -> tuple[Campaign, CampaignStore, str, str]:
    """The short campaign with a target ion matched on the run change's day
    alone, and a formula read by both runs detected on the last day alone."""
    campaign = generate(SHORT, seed=2)
    tables = dict(campaign.tables)
    samples = tables["samples"]
    batch = samples["sample_batch_id"].astype(str)
    day_of = dict(zip(samples["sample_item_id"].astype(str), batch))
    change_day = batch.iloc[
        (samples["t_mid_utc"] >= campaign.truth.run_change.at).idxmax()
    ]
    last_day = batch.iloc[-1]
    renamed = {key for pair in campaign.truth.run_change.readings for key in pair}
    ion = "ion:ti0001"
    formula = next(
        key
        for key in sorted(campaign.truth.cycles)
        if key.startswith("formula:") and key not in renamed
    )
    ions = tables["ion_series"].copy()
    off = (ions["series_key"].astype(str) == ion) & (
        ions["sample_item_id"].astype(str).map(day_of) != change_day
    )
    ions.loc[off, "intensity"] = np.nan
    ions.loc[off, "match_category"] = 0
    peaks = tables["peak_series"].copy()
    off = (peaks["series_key"].astype(str) == formula) & (
        peaks["sample_item_id"].astype(str).map(day_of) != last_day
    )
    peaks.loc[off, "intensity"] = np.nan
    tables.update(ion_series=ions, peak_series=peaks)
    campaign = dataclasses.replace(campaign, tables=tables)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_module, "_now", lambda: pd.Timestamp("2026-08-01T00:00Z"))
        patch.setattr(store_module.time, "sleep", lambda seconds: None)
        report = sync(FakeClient(campaign), SCOPE, root=tmp_path_factory.mktemp("late"))
    return campaign, report.store, ion, formula


def test_an_ion_new_at_a_run_change_is_not_marked(late_species):
    campaign, store, ion, _ = late_species
    change = campaign.truth.run_change

    fig = top_species(
        store, period="day", tz="UTC", end=change.at + pd.Timedelta(days=1)
    )

    new = {row["key"]: row["reread"] for row in fig.summary["new"]}
    renamed = {key for _, key in change.readings}
    assert new == {ion: False, **{key: True for key in renamed}}


def test_a_formula_new_after_an_earlier_run_change_is_not_marked(late_species):
    campaign, store, _, formula = late_species

    fig = top_species(store, period="day", tz="UTC")

    assert fig.summary["mixed_runs"] is True
    assert [(row["key"], row["reread"]) for row in fig.summary["new"]] == [
        (formula, False)
    ]
    assert not any(
        note.startswith("The engine run changes in the last") for note in fig.notes
    )


# --- what a real server gives: blank names, both polarities ------------------------


#: The target ions without a compound name, and the blank name each is
#: given: the server's ``""`` for the reagent, a space for one compound.
UNNAMED = {"ti0000": "", "ti0001": " "}

#: A target ion whose name, formula and ion formula the server gives blank.
BLANK = "ti0003"


def _stale_text(value):
    """The store's text before blank names were dropped: kept as given."""
    return None if value is None or pd.isna(value) else str(value)


def _mixed_client() -> tuple[Campaign, FakeClient]:
    """The short campaign as a server of both chemistries gives it: the
    second day's samples positive, two target ions without a compound name
    (the server's ``""``, and a space), and a third with a blank formula
    and ion formula as well."""
    campaign = generate(SHORT, seed=2)
    client = FakeClient(campaign)
    middle = client.batch_ids[1]
    client.by_batch[middle] = client.by_batch[middle].assign(polarity="+")
    blank = ["target_compound_name", "target_compound_formula", "ion_formula"]
    for ions in client.ions.values():
        for ion, blank_name in UNNAMED.items():
            ions.loc[ions["target_ion_id"] == ion, "target_compound_name"] = blank_name
        ions.loc[ions["target_ion_id"] == BLANK, blank] = ""
    return campaign, client


@pytest.fixture(scope="module", params=["fresh", "stale"])
def mixed(request, tmp_path_factory) -> tuple[Campaign, CampaignStore]:
    """The mixed server's campaign, synced; ``stale`` as a store synced
    before blank names were dropped, which still holds them."""
    campaign, client = _mixed_client()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_module, "_now", lambda: pd.Timestamp("2026-08-01T00:00Z"))
        patch.setattr(store_module.time, "sleep", lambda seconds: None)
        if request.param == "stale":
            patch.setattr(store_module, "_text", _stale_text)
        report = sync(client, SCOPE, root=tmp_path_factory.mktemp(request.param))
    return campaign, report.store


def test_the_store_holds_a_blank_name_as_none(mixed, request):
    _, store = mixed
    species = store.table("species").set_index("series_key")

    names = [species.at[f"ion:{ion}", "name"] for ion in UNNAMED]
    blank = species.loc[f"ion:{BLANK}", ["name", "formula", "ion_formula"]]
    stale = request.node.callspec.params["mixed"] == "stale"
    if stale:
        assert names == list(UNNAMED.values())
        assert list(blank) == ["", "", ""]
    else:
        assert pd.isna(names).all()
        assert blank.isna().all()
    assert species.at["ion:ti0002", "name"] == "compound 02"


def test_an_ion_without_a_name_is_named_by_its_formula(mixed):
    campaign, store = mixed
    reagents = [f"ion:{ion}" for ion in UNNAMED]
    # The first day, negative alone: a sum never spans polarities
    end = campaign.truth.start + pd.Timedelta(days=1)

    signal = instrument_signal(store, reagents=reagents, tz="UTC", end=end)
    summary = scope_summary(store, reagents=reagents, normalise=reagents[0])
    top = top_species(store, period="day", normalise=reagents, tz="UTC", end=end)

    assert [r["name"] for r in signal.summary["reagents"]] == ["I", "C13H26O"]
    assert {t["key"]: t["name"] for t in signal.summary["traces"]} == {
        "tic": "TIC",
        "ion:ti0000": "I",
        "ion:ti0001": "C13H26O",
        "ion:ti0000+ion:ti0001": "I + C13H26O",
    }
    assert set(signal.data["name"]) == {"TIC", "I", "C13H26O", "I + C13H26O"}
    rows = dict(zip(summary.data["item"], summary.data["value"]))
    assert rows["Reagent ions"] == "I, C13H26O"
    assert rows["Normalisation"] == "I"
    # The view names the normaliser, and the subtitle prints it
    assert top.summary["normalise_name"] == "I + C13H26O"
    assert "I + C13H26O" in top.subtitle
    # A named ion keeps its name
    named = instrument_signal(store, reagents=["ion:ti0002"])
    assert named.summary["reagents"][0]["name"] == "compound 02"


def test_an_ion_without_a_name_or_a_formula_is_named_by_its_key(mixed):
    campaign, store = mixed
    key = f"ion:{BLANK}"
    end = campaign.truth.start + pd.Timedelta(days=1)

    fig = instrument_signal(store, reagents=[key], normalise=key, tz="UTC", end=end)

    assert fig.summary["reagents"] == [{"key": key, "name": key, "formula": None}]
    assert fig.summary["normalise_name"] == key


@pytest.mark.parametrize("build", [instrument_signal, top_species])
def test_a_window_of_both_polarities_says_it_pools_them(mixed, build):
    campaign, store = mixed
    start = campaign.truth.start
    pooled = "The window's samples are of both polarities"

    both = build(store, tz="UTC")
    negative = build(store, tz="UTC", end=start + pd.Timedelta(days=1))

    assert both.summary["polarities"] == ["+", "-"]
    assert [note for note in both.notes if note.startswith(pooled)]
    assert negative.summary["polarities"] == ["-"]
    assert not [note for note in negative.notes if note.startswith(pooled)]
    what = "the TIC pools them" if build is instrument_signal else "rank the species"
    assert what in next(note for note in both.notes if note.startswith(pooled))
