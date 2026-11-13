"""
The views, on the synthetic campaign synced into a campaign store.

``synced`` (conftest) is each chemistry's month, synced once from the fake
server; the tests that need a store of their own sync a short campaign. The
assertions are against the answers the campaign was built from
(:class:`fixtures.campaign.Truth`) or against pandas over the campaign's own
tables, not against what the views computed last time.
"""

import dataclasses
import json

import numpy as np
import pandas as pd
import pytest
from fixtures.campaign import HALIDE_NEGATIVE, Campaign, generate
from fixtures.server import SCOPE, FakeClient

from mascope_sdk.exceptions import AuthenticationError
from mascope_viz.data import Scope, views
from mascope_viz.data import store as store_module
from mascope_viz.data.store import CampaignStore, sync


ZONE = "Europe/Helsinki"

#: What every frame's attrs say.
ATTRS = {
    "view",
    "tz",
    "cadence",
    "normalise",
    "normalise_name",
    "keys",
    "start",
    "end",
    "mixed_runs",
    "run_changes",
    "provenance",
}


@pytest.fixture(scope="module")
def month(synced) -> tuple[Campaign, CampaignStore]:
    campaign, _, report = synced
    return campaign, report.store


def _sync(
    campaign: Campaign | FakeClient,
    root,
    scope: Scope = SCOPE,
    now: str = "2026-08-01T00:00:00Z",
) -> CampaignStore:
    client = campaign if isinstance(campaign, FakeClient) else FakeClient(campaign)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_module, "_now", lambda: pd.Timestamp(now))
        patch.setattr(store_module.time, "sleep", lambda seconds: None)
        return sync(client, scope, root=root).store


#: Four days, the third unfiled (a scope's files listing ends at its last
#: sample, so an unfiled last day would list no file), the run changing on
#: the second.
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
def short_campaign() -> Campaign:
    return generate(SHORT, seed=2)


def _values(campaign: Campaign) -> pd.DataFrame:
    """Every value the campaign holds, one row per (sample, key), with the
    sample's time: what the store's series read back."""
    samples = campaign.tables["samples"]
    when = pd.Series(
        samples["t_mid_utc"].to_numpy(), index=samples["sample_item_id"].astype(str)
    )
    tic = pd.Series(samples["tic"].to_numpy(), index=when.index)
    frames = []
    for table in ("ion_series", "peak_series"):
        rows = campaign.tables[table]
        ids = rows["sample_item_id"].astype(str)
        frames.append(
            pd.DataFrame(
                {
                    "sample_item_id": ids.to_numpy(),
                    "t": pd.DatetimeIndex(ids.map(when)),
                    "key": rows["series_key"].astype(str).to_numpy(),
                    "value": rows["intensity"].to_numpy(),
                    "tic": ids.map(tic).to_numpy(),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def values(month) -> pd.DataFrame:
    campaign, _ = month
    return _values(campaign)


def _keep(campaign: Campaign, kept: pd.Series) -> Campaign:
    """*campaign* with only the samples *kept* marks, and their values."""
    tables = dict(campaign.tables)
    samples = tables["samples"]
    ids = set(samples.loc[kept, "sample_item_id"].astype(str))
    tables["samples"] = samples[kept].reset_index(drop=True)
    for table in ("ion_series", "peak_series"):
        rows = tables[table]
        tables[table] = rows[rows["sample_item_id"].astype(str).isin(ids)]
    return dataclasses.replace(campaign, tables=tables)


def _minutes(campaign: Campaign) -> pd.Series:
    """Each sample's minute from the campaign's first sample."""
    t = campaign.tables["samples"]["t_mid_utc"]
    return (t - t.iloc[0]) // pd.Timedelta(minutes=1)


def _episode_keys(campaign: Campaign) -> set[str]:
    return {key for e in campaign.truth.episodes for key in e.series_keys}


def _renamed(campaign: Campaign) -> set[str]:
    return {key for pair in campaign.truth.run_change.readings for key in pair}


# --- the cadence rule -------------------------------------------------------------


@pytest.mark.parametrize(
    "spacing, minutes",
    [(60, 1), (30, 1), (61, 2), (73, 2), (150, 3), (600, 10)],
)
def test_the_auto_cadence_is_the_median_spacing_rounded_up(spacing, minutes):
    times = pd.Timestamp("2026-06-01T00:00:00Z") + pd.to_timedelta(
        np.arange(100) * spacing, unit="s"
    )

    assert views.auto_cadence(times) == pd.Timedelta(minutes=minutes)


def test_the_auto_cadence_of_few_samples_is_their_span_over_fifty_bins():
    t = pd.Timestamp("2026-06-01T00:00:00Z")

    assert views.auto_cadence([t, t + pd.Timedelta(hours=5)]) == pd.Timedelta(minutes=6)
    assert views.auto_cadence([t]) == pd.Timedelta(minutes=1)
    # Duplicates count once
    assert views.auto_cadence([t, t, t, t + pd.Timedelta(hours=5)]) == pd.Timedelta(
        minutes=6
    )


def test_bins_at_the_auto_cadence_never_comb_where_whole_minutes_would():
    """Peaky's case: samples 73 s apart, a little irregular. One-minute bins
    catch none now and then; the rule's two minutes always catch one."""
    rng = np.random.default_rng(0)
    seconds = np.arange(2000) * 73.0 + rng.uniform(-5, 5, 2000)
    times = pd.Timestamp("2026-06-01T00:00:00Z") + pd.to_timedelta(seconds, unit="s")

    def empty_bins(width: pd.Timedelta) -> int:
        bins = (times - times[0].floor(width)) // width
        return int((np.bincount(np.asarray(bins)) == 0).sum())

    assert empty_bins(pd.Timedelta(minutes=1)) > 100
    assert views.auto_cadence(times) == pd.Timedelta(minutes=2)
    assert empty_bins(views.auto_cadence(times)) == 0


def test_the_auto_cadence_never_leaves_a_bin_empty_inside_acquired_time(month):
    campaign, store = month
    gap = campaign.truth.gap
    keys = sorted(set(campaign.truth.cycles) | {views.TIC})

    frame = views.series(store, keys)

    assert frame.attrs["cadence"] == "1min"
    assert frame.attrs["auto"]
    in_gap = (frame["t"] >= gap.start) & (frame["t"] < gap.end)
    for key, rows in frame[~in_gap].groupby("key", observed=True):
        # Inside the span of the batches that read the key
        read = rows["n_samples"].to_numpy() > 0
        first, last = np.flatnonzero(read)[[0, -1]]
        assert read[first : last + 1].all(), key
    assert (frame.loc[in_gap, "n_samples"] == 0).all()
    # A formula the first run alone reads has no samples after the change
    change = campaign.truth.run_change.at
    for old, _ in campaign.truth.run_change.readings:
        late = frame[(frame["key"] == old) & (frame["t"] >= change)]
        assert len(late) > 0
        assert (late["n_samples"] == 0).all() and late["value"].isna().all()


def test_the_auto_cadence_is_the_widest_of_the_keys(short_campaign, tmp_path):
    """Keys read in different batches can sample at different spacings: the
    frame bins at the widest, so that no key's bins comb."""
    samples = short_campaign.tables["samples"]
    # From the second batch on, a sample every other minute
    kept = (samples["sample_batch_id"] == "batch-01") | (
        _minutes(short_campaign) % 2 == 0
    )
    store = _sync(_keep(short_campaign, kept), tmp_path)
    # The second run reads these formulas from the second batch on, the
    # first run the others in the first batch alone
    old, new = (list(keys) for keys in zip(*short_campaign.truth.run_change.readings))

    assert views.series(store, old).attrs["cadence"] == "1min"
    assert views.series(store, new).attrs["cadence"] == "2min"
    both = views.series(store, [old[0], new[0]])
    assert both.attrs["cadence"] == "2min"
    assert views.series(store, [new[0], old[0]]).attrs["cadence"] == "2min"
    gap = short_campaign.truth.gap
    late = both[
        (both["key"] == new[0]) & ((both["t"] < gap.start) | (both["t"] >= gap.end))
    ]
    read = np.flatnonzero(late["n_samples"].to_numpy() > 0)
    assert (late["n_samples"].iloc[read[0] : read[-1] + 1] > 0).all()


# --- series ---------------------------------------------------------------------


def test_the_gap_day_is_nan_in_series(month):
    campaign, store = month
    gap = campaign.truth.gap
    keys = sorted(campaign.truth.cycles)

    for cadence in ("auto", "10min", "1h"):
        frame = views.series(store, keys, cadence=cadence)
        in_gap = (frame["t"] >= gap.start) & (frame["t"] < gap.end)

        assert in_gap.sum() > 0
        assert frame.loc[in_gap, "value"].isna().all()
        assert (frame.loc[in_gap, "n"] == 0).all()
        assert (frame.loc[in_gap, "n_samples"] == 0).all()
        # The bins around it hold values
        around = frame[
            (frame["t"] >= gap.start - pd.Timedelta(hours=2))
            & (frame["t"] < gap.end + pd.Timedelta(hours=2))
            & ~in_gap
        ]
        assert around["value"].notna().any()


def test_hourly_bins_in_utc_are_the_stores_rollups(month):
    """Two computations of one thing: the store's hourly rollups at sync,
    and the series view at an hour in UTC."""
    _, store = month
    rollups = store.table("rollups")
    rollups = rollups[rollups["bin"] == "hour"]
    keys = sorted(rollups["series_key"].astype(str).unique())

    frame = views.series(store, keys, cadence="1h", band=(10, 90), tz="UTC")
    got = frame[frame["n"] > 0].copy()
    got["key"] = got["key"].astype(str)
    got = got.set_index(["key", "t"]).sort_index()
    want = rollups.assign(series_key=rollups["series_key"].astype(str))
    want = want.set_index(["series_key", "t"]).sort_index()

    assert len(got) == len(want)
    np.testing.assert_allclose(got["value"], want["median"], rtol=1e-12)
    np.testing.assert_allclose(got["lo"], want["p10"], rtol=1e-12)
    np.testing.assert_allclose(got["hi"], want["p90"], rtol=1e-12)
    np.testing.assert_array_equal(got["n"], want["n"])


@pytest.mark.parametrize("stat", views.STATS)
def test_a_bins_value_is_its_stat(month, values, stat):
    campaign, store = month
    key = sorted(k for k in campaign.truth.cycles if k.startswith("ion:"))[0]
    raw = values[values["key"] == key].dropna(subset=["value"])
    local = raw["t"].dt.tz_convert(ZONE).dt.floor("1h")
    want = raw.groupby(local)["value"].agg(stat)

    frame = views.series(store, key, cadence="1h", stat=stat, band=(25, 75))
    got = frame.set_index("t")

    np.testing.assert_allclose(got.loc[want.index, "value"], want)
    quartiles = raw.groupby(local)["value"].quantile(0.25)
    np.testing.assert_allclose(got.loc[want.index, "lo"], quartiles)
    assert frame.attrs["stat"] == stat
    assert frame.attrs["band"] == (25, 75)


def test_bins_start_on_the_zones_hours_days_and_months(month):
    _, store = month
    key = views.TIC

    hours = views.series(store, key, cadence="1h")["t"]
    days = views.series(store, key, cadence="1D")["t"]
    months = views.series(store, key, cadence="MS")["t"]

    assert str(hours.dt.tz) == ZONE
    assert (hours.dt.minute == 0).all() and (hours.dt.second == 0).all()
    assert (days.dt.hour == 0).all() and (days.dt.minute == 0).all()
    assert list(months) == [
        pd.Timestamp("2026-06-01", tz=ZONE),
        pd.Timestamp("2026-07-01", tz=ZONE),
    ]
    tens = views.series(store, key, cadence="10min")["t"]
    assert (tens.dt.minute % 10 == 0).all() and (tens.dt.second == 0).all()
    # A calendar bin holds a window that starts inside it
    late = views.series(store, key, cadence="MS", start="2026-06-10")
    assert late["t"].iloc[0] == pd.Timestamp("2026-06-01", tz=ZONE)
    weeks = views.series(store, key, cadence="W", start="2026-06-10")["t"]
    assert (weeks.dt.day_of_week == 0).all()
    assert weeks.iloc[0] == pd.Timestamp("2026-06-08", tz=ZONE)


def test_an_offset_on_a_periods_end_bins_the_periods_it_ends(month, values):
    """``W`` is ``W-SUN``, the weeks that end on a Sunday: Monday to Sunday,
    the weeks of periods. ``ME`` is the months that end on their last day:
    the calendar months, as ``MS`` has them."""
    campaign, store = month
    key = _with_nans(campaign, values)

    weeks = views.series(store, key, cadence="W")
    by_week = views.periods(store, key, "week")

    assert list(weeks["t"]) == list(by_week["period"])
    np.testing.assert_array_equal(weeks["n"], by_week["n"])
    np.testing.assert_allclose(weeks["value"], by_week["median"])
    sundays = views.series(store, key, cadence="W-SAT")["t"]
    assert (sundays.dt.day_of_week == 6).all() and (sundays.dt.hour == 0).all()
    # A window from a week's last day, or a month's, starts in that period
    late = views.series(store, key, cadence="W", start="2026-06-14")["t"]
    assert late.iloc[0] == pd.Timestamp("2026-06-08", tz=ZONE)
    late = views.series(store, key, cadence="ME", start="2026-06-30")["t"]
    assert late.iloc[0] == pd.Timestamp("2026-06-01", tz=ZONE)
    months = views.series(store, views.TIC, cadence="MS")[["t", "n_samples"]]
    pd.testing.assert_frame_equal(
        views.series(store, views.TIC, cadence="ME")[["t", "n_samples"]], months
    )
    # June and the hours of July 1 are in two quarters, one year
    quarters = views.series(store, views.TIC, cadence="QE")
    assert list(quarters["t"]) == [
        pd.Timestamp("2026-04-01", tz=ZONE),
        pd.Timestamp("2026-07-01", tz=ZONE),
    ]
    np.testing.assert_array_equal(quarters["n_samples"], months["n_samples"])
    years = views.series(store, views.TIC, cadence="YE")
    assert list(years["t"]) == [pd.Timestamp("2026-01-01", tz=ZONE)]
    assert years["n_samples"].item() == months["n_samples"].sum()
    # SME ends on the 15th and the last: halves from the 1st and the 16th
    halves = views.series(store, views.TIC, cadence="SME")
    assert list(halves["t"]) == [
        pd.Timestamp("2026-06-01", tz=ZONE),
        pd.Timestamp("2026-06-16", tz=ZONE),
        pd.Timestamp("2026-07-01", tz=ZONE),
    ]
    assert halves["n_samples"].iloc[-1] == months["n_samples"].iloc[-1]


def test_raw_rows_are_the_samples_and_below_detection_stays_nan(month, values):
    campaign, store = month
    keys = sorted(campaign.truth.cycles)

    frame = views.series(store, keys, cadence="raw")
    sampled = frame[frame["n_samples"] == 1]

    assert frame.attrs["cadence"] == "raw"
    assert frame["lo"].isna().all() and frame["hi"].isna().all()
    for key in keys:
        want = values[values["key"] == key]
        got = sampled[sampled["key"] == key]
        assert len(got) == len(want), key
        assert got["value"].isna().sum() == want["value"].isna().sum(), key
        np.testing.assert_allclose(
            np.sort(got["value"].dropna()), np.sort(want["value"].dropna())
        )
    assert (frame["value"] != 0).all()
    assert (frame["n"] == frame["value"].notna()).all()


def test_raw_rows_break_where_a_bin_would_be_empty(month):
    campaign, store = month
    gap = campaign.truth.gap
    key = sorted(k for k in campaign.truth.cycles if k.startswith("ion:"))[0]

    frame = views.series(store, key, cadence="raw")
    breaks = frame[frame["n_samples"] == 0]

    assert list(breaks["t"]) == [gap.start]
    assert breaks["value"].isna().all()
    assert frame["t"].is_monotonic_increasing


def test_a_window_keeps_the_samples_inside_it(month):
    _, store = month

    frame = views.series(store, views.TIC, start="2026-06-10", end="2026-06-11")

    assert frame["t"].min() == pd.Timestamp("2026-06-10T00:00", tz=ZONE)
    assert frame["t"].max() == pd.Timestamp("2026-06-10T23:59", tz=ZONE)
    assert frame.attrs["start"] == "2026-06-09T21:00:00+00:00"
    assert frame.attrs["end"] == "2026-06-10T21:00:00+00:00"
    aware = views.series(
        store,
        views.TIC,
        start=pd.Timestamp("2026-06-09T21:00Z"),
        end=pd.Timestamp("2026-06-10T21:00Z"),
    )
    pd.testing.assert_frame_equal(frame, aware)


def test_a_window_holds_a_sample_at_its_start_and_none_at_its_end(month):
    _, store = month
    when = store.table("samples")["t_mid_utc"].sort_values().reset_index(drop=True)
    lo, hi = when[100], when[160]

    raw = views.series(store, views.TIC, cadence="raw", start=lo, end=hi)
    sampled = raw[raw["n_samples"] == 1]

    assert sampled["t"].min() == lo
    assert sampled["t"].max() < hi
    assert len(sampled) == ((when >= lo) & (when < hi)).sum()
    began = store.table("files")["datetime_utc"].sort_values().reset_index(drop=True)
    found = views.coverage(store, start=began[10], end=began[20])
    assert found.n_files == ((began >= began[10]) & (began < began[20])).sum()
    assert found.n_files < ((began >= began[10]) & (began <= began[20])).sum()


def test_a_window_of_a_series_key_bins_its_values_alone(month, values):
    campaign, store = month
    key = sorted(k for k in campaign.truth.cycles if k.startswith("ion:"))[0]
    lo = pd.Timestamp("2026-06-10T00:00", tz=ZONE)
    hi = pd.Timestamp("2026-06-10T06:00", tz=ZONE)
    raw = values[(values["key"] == key) & (values["t"] >= lo) & (values["t"] < hi)]
    hour = raw["t"].dt.tz_convert(ZONE).dt.floor("1h")

    frame = views.series(store, key, cadence="1h", start=lo, end=hi).set_index("t")

    assert list(frame.index) == list(pd.date_range(lo, hi, freq="1h", inclusive="left"))
    np.testing.assert_array_equal(frame["n_samples"], raw.groupby(hour).size())
    np.testing.assert_allclose(frame["value"], raw.groupby(hour)["value"].median())


# --- normalisation ----------------------------------------------------------------


def _relative_slopes(frame: pd.DataFrame, start: pd.Timestamp) -> list[float]:
    """Each key's daily-median slope per day, over its intercept."""
    slopes = []
    for _, rows in frame.dropna(subset=["value"]).groupby("key", observed=True):
        days = (rows["t"] - start).dt.total_seconds() / 86400.0
        slope, intercept = np.polyfit(days, rows["value"], 1)
        slopes.append(slope / intercept)
    return slopes


def test_reagent_ion_normalisation_removes_the_known_drift(halide_campaign, month):
    campaign, store = month
    if campaign is not halide_campaign:
        pytest.skip("the positive chemistry has no reagent ion")
    truth = campaign.truth
    # The flat species outside the episodes: their daily medians carry the
    # drift alone
    keys = sorted(
        k
        for k, cycle in truth.cycles.items()
        if cycle.peak_hour is None and k not in _episode_keys(campaign)
    )
    known = truth.drift.slope_per_day / truth.drift.intercept

    raw = views.series(store, keys, cadence="1D", tz="UTC")
    normalised = views.series(
        store, keys, cadence="1D", normalise=truth.drift.series_key, tz="UTC"
    )

    assert known == pytest.approx(-0.012)
    for slope in _relative_slopes(raw, truth.start):
        assert slope == pytest.approx(known, abs=0.001)
    for slope in _relative_slopes(normalised, truth.start):
        assert abs(slope) < 0.0005
    assert normalised.attrs["normalise"] == truth.drift.series_key
    assert normalised.attrs["normalise_name"] == "reagent ion"


def test_normalising_divides_each_sample_before_binning(month, values):
    campaign, store = month
    key = sorted(k for k in campaign.truth.cycles if k.startswith("formula:"))[0]
    by = sorted(k for k in campaign.truth.cycles if k.startswith("ion:"))[-1]
    raw = values[values["key"] == key].set_index("sample_item_id")["value"]
    divisor = values[values["key"] == by].set_index("sample_item_id")["value"]
    tic = values[values["key"] == key].set_index("sample_item_id")["tic"]

    for normalise, want in (("tic", raw / tic), (by, raw / divisor.reindex(raw.index))):
        frame = views.series(store, key, cadence="raw", normalise=normalise)
        got = frame[frame["n_samples"] == 1]["value"].to_numpy()
        expected = want.loc[
            values[values["key"] == key].sort_values("t")["sample_item_id"]
        ]
        np.testing.assert_allclose(got, expected.to_numpy())
        # A sample whose normaliser has no value is NaN
        assert np.isnan(got).sum() == expected.isna().sum()
    # The hourly median of the ratios, not the ratio of the medians
    hourly = views.series(store, key, cadence="1h", normalise="tic", tz="UTC")
    ratio = (raw / tic).to_frame("v")
    ratio["t"] = values[values["key"] == key].set_index("sample_item_id")["t"]
    want = ratio.dropna().groupby(ratio["t"].dt.floor("1h"))["v"].median()
    np.testing.assert_allclose(hourly.set_index("t").loc[want.index, "value"], want)


def test_a_sample_with_no_tic_or_a_zero_one_is_nan_when_normalised(
    short_campaign, tmp_path
):
    tables = dict(short_campaign.tables)
    samples = tables["samples"].copy()
    samples.loc[0, "tic"] = 0.0
    samples.loc[1, "tic"] = np.nan
    tables["samples"] = samples
    store = _sync(dataclasses.replace(short_campaign, tables=tables), tmp_path)

    frame = views.series(store, views.TIC, cadence="raw", normalise="tic")
    reagent = views.series(
        store, short_campaign.truth.drift.series_key, cadence="raw", normalise="tic"
    )
    reagent = reagent[reagent["n_samples"] == 1]

    assert frame["value"].iloc[:2].isna().all()
    np.testing.assert_allclose(frame["value"].iloc[2:].dropna(), 1.0)
    # The reagent ion is matched in every sample: only the TIC is missing
    assert reagent["value"].iloc[:2].isna().all()
    assert np.isfinite(reagent["value"].iloc[2:]).all()


def _by_sample(frame: pd.DataFrame, store: CampaignStore) -> pd.Series:
    """A raw frame's values of one key by sample, each found by its time."""
    samples = store.table("samples")
    at = pd.Series(
        samples["sample_item_id"].astype(str).to_numpy(), index=samples["t_mid_utc"]
    )
    rows = frame[frame["n_samples"] == 1]
    found = at.loc[rows["t"].dt.tz_convert("UTC")].to_numpy()
    return pd.Series(rows["value"].to_numpy(), index=found)


def test_two_batch_peaks_of_one_formula_are_summed_per_sample(short_campaign, tmp_path):
    """The fake server's twin is a second batch peak of the first species'
    formula, at half its height in the first fifty samples."""
    client = FakeClient(short_campaign, twin=True)
    store = _sync(client, tmp_path)
    batch = client.batch_ids[0]
    points = client.points[batch]
    parent = client.anchors[batch].iloc[0]
    rows = points[
        points["batch_peak_id"].isin([parent["batch_peak_id"], f"{batch}-twin"])
    ]
    want = rows.groupby("sample_item_id")["intensity"].sum()
    key = store_module.formula_key(parent["consensus_formula"], client.mechanism)

    got = _by_sample(views.series(store, key, cadence="raw"), store)

    twin = points.loc[points["batch_peak_id"] == f"{batch}-twin", "sample_item_id"]
    assert len(twin) == 50
    np.testing.assert_allclose(got.loc[want.index], want)


# --- series from the rollups ------------------------------------------------------


def _from_rows(store: CampaignStore, keys, **kwargs) -> pd.DataFrame:
    """:func:`views.series` binned from the store's rows, whatever it asks."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(views, "_rollable", lambda *args, **kw: False)
        return views.series(store, keys, **kwargs)


def _read_tables(store: CampaignStore, call) -> tuple[pd.DataFrame, list]:
    """What *call* returns, and the ``(table, batches)`` it read the store
    with."""
    read = []
    table = CampaignStore.table

    def recording(self, name, **kwargs):
        read.append((name, kwargs.get("batches")))
        return table(self, name, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(CampaignStore, "table", recording)
        return call(), read


def _as_from_rows(store: CampaignStore, keys, source: str, **kwargs) -> pd.DataFrame:
    """:func:`views.series` of *keys*, checked to come from *source* and to
    be the frame the rows give, attrs included."""
    got = views.series(store, keys, **kwargs)
    want = _from_rows(store, keys, **kwargs)

    assert got.attrs["source"] == source
    assert want.attrs["source"] == "rows"
    pd.testing.assert_frame_equal(got, want, rtol=1e-12)
    assert {k: v for k, v in got.attrs.items() if k != "source"} == {
        k: v for k, v in want.attrs.items() if k != "source"
    }
    return got


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cadence": "1h", "tz": "UTC"},
        # Helsinki is whole hours from UTC, through its clock changes too
        {"cadence": "1h"},
        {"cadence": "1D", "tz": "UTC"},
        {"cadence": "24h", "tz": "UTC"},
        {"cadence": "1h", "tz": "UTC", "stat": "mean", "band": None},
        # Across a batch: the batch reads the keys only in its samples inside
        {"cadence": "1h", "start": "2026-06-03T10:00Z", "end": "2026-06-09T00:00Z"},
        {"cadence": "1D", "tz": "UTC", "start": "2026-06-03", "end": "2026-06-09"},
    ],
)
def test_series_from_the_rollups_is_the_series_from_the_rows(month, kwargs):
    _, store = month
    keys = [*sorted(store.table("species")["series_key"].astype(str)), views.TIC]

    frame = _as_from_rows(store, keys, "rollups", **kwargs)

    assert frame["n"].sum() > 0
    assert (frame["n_samples"] > 0).any()


def test_series_from_the_rollups_reads_no_series_rows(month):
    """The rollups stand in for the rows: only a batch the window cuts has
    its rows read, for which keys it reads inside the window."""
    campaign, store = month
    keys = sorted(campaign.truth.cycles)
    series_tables = {"ion_series", "peak_series"}

    frame, read = _read_tables(store, lambda: views.series(store, keys, cadence="1h"))

    assert frame.attrs["source"] == "rollups"
    assert ("rollups", None) in read
    assert not [name for name, _ in read if name in series_tables]

    frame, read = _read_tables(
        store,
        lambda: views.series(
            store, keys, cadence="1h", start="2026-06-03T10:00Z", end="2026-06-09"
        ),
    )

    assert frame.attrs["source"] == "rollups"
    # The end is midnight in Helsinki, inside the UTC day's batch
    cut = {tuple(batches) for name, batches in read if name in series_tables}
    assert cut == {("batch-03",), ("batch-08",)}


@pytest.mark.parametrize(
    "kwargs",
    [
        # A rollup day is a UTC day
        {"cadence": "1D"},
        # India is five and a half hours from UTC
        {"cadence": "1h", "tz": "Asia/Kolkata"},
        {"cadence": "6h", "tz": "UTC"},
        {"cadence": "10min", "tz": "UTC"},
        {"cadence": "auto", "tz": "UTC"},
        {"cadence": "raw", "tz": "UTC"},
        {"cadence": "1h", "tz": "UTC", "normalise": "tic"},
        {"cadence": "1h", "tz": "UTC", "stat": "max"},
        {"cadence": "1h", "tz": "UTC", "band": (25, 75)},
        # No sample in the window
        {"cadence": "1h", "tz": "UTC", "start": "2026-08-01", "end": "2026-08-02"},
    ],
)
def test_series_bins_the_rows_where_the_rollups_would_differ(month, kwargs):
    """Decided before anything is read of the batches."""
    campaign, store = month
    keys = [*sorted(campaign.truth.cycles), views.TIC]

    frame, read = _read_tables(store, lambda: views.series(store, keys, **kwargs))

    assert frame.attrs["source"] == "rows"
    assert not [name for name, batches in read if name == "rollups" or batches]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": "2026-06-03T10:30Z"},
        {"end": "2026-06-09T00:30Z"},
        {"start": "2026-06-03T10:00Z", "end": "2026-06-03T10:00:31Z"},
    ],
)
def test_series_bins_the_rows_where_a_bin_reaches_outside_the_window(month, kwargs):
    campaign, store = month
    keys = [*sorted(campaign.truth.cycles), views.TIC]

    frame, read = _read_tables(
        store, lambda: views.series(store, keys, cadence="1h", tz="UTC", **kwargs)
    )

    assert frame.attrs["source"] == "rows"
    assert "rollups" not in {name for name, _ in read}


def test_series_bins_the_rows_while_a_rollup_day_is_pending(short_campaign, tmp_path):
    """A sync stopped before it rolled a day up leaves the day's rollups
    stale."""
    store = _sync(short_campaign, tmp_path)
    keys = sorted(store.table("species")["series_key"].astype(str))
    _as_from_rows(store, keys, "rollups", cadence="1h")

    meta = store.meta
    meta["pending_rollups"] = ["2026-06-02"]
    store.meta_path.write_text(json.dumps(meta))

    _as_from_rows(store, keys, "rows", cadence="1h")


def test_series_bins_the_rows_of_a_formula_two_batch_peaks_carry(
    short_campaign, tmp_path
):
    """A sample's value is the sum of the two peaks' values; the rollups
    count each. An ion's series is still the rollups'."""
    client = FakeClient(short_campaign, twin=True)
    store = _sync(client, tmp_path)
    parent = client.anchors[client.batch_ids[0]].iloc[0]
    formula = store_module.formula_key(parent["consensus_formula"], client.mechanism)
    ion = sorted(k for k in short_campaign.truth.cycles if k.startswith("ion:"))[0]

    _as_from_rows(store, [formula], "rows", cadence="1h")
    _as_from_rows(store, [ion], "rollups", cadence="1h")


def test_series_bins_the_rows_where_a_formula_has_two_readings_in_a_bin(
    short_campaign, tmp_path
):
    """The second run's first sample moved into the first run's last hour:
    that hour, and that day, hold the formulas of both readings."""
    tables = dict(short_campaign.tables)
    samples = tables["samples"].copy()
    first = samples.index[samples["sample_batch_id"] == "batch-02"][0]
    samples.loc[first, "datetime_utc"] = pd.Timestamp("2026-06-01T23:00:00Z")
    samples.loc[first, "t_mid_utc"] = pd.Timestamp("2026-06-01T23:00:30Z")
    tables["samples"] = samples
    store = _sync(dataclasses.replace(short_campaign, tables=tables), tmp_path)
    renamed = _renamed(short_campaign)
    formulas = sorted(
        k
        for k in store.table("species")["series_key"].astype(str)
        if k.startswith("formula:") and k not in renamed
    )
    ions = sorted(k for k in short_campaign.truth.cycles if k.startswith("ion:"))
    rollups = store.table("rollups", keys=formulas[:1])
    assert (rollups.groupby(["bin", "t"], observed=True).size() == 2).any()

    for cadence, t, n in (
        ("1h", "2026-06-01T23:00Z", 61),
        ("1D", "2026-06-01T00:00Z", 1441),
    ):
        frame = _as_from_rows(store, formulas[:1], "rows", cadence=cadence, tz="UTC")
        assert frame.set_index("t").loc[pd.Timestamp(t), "n"] == n
        _as_from_rows(store, ions, "rollups", cadence=cadence, tz="UTC")


def test_a_batch_the_window_cuts_reads_a_key_only_inside_it(short_campaign, tmp_path):
    """The second batch reads an ion before noon and not after: from noon
    on, its samples are no samples of the ion, though its ``species`` lists
    the ion."""
    ion = sorted(k for k in short_campaign.truth.cycles if k.startswith("ion:"))[0]
    tables = dict(short_campaign.tables)
    samples = tables["samples"]
    noon = pd.Timestamp("2026-06-02T12:00:00Z")
    after = set(
        samples.loc[
            (samples["sample_batch_id"] == "batch-02") & (samples["t_mid_utc"] >= noon),
            "sample_item_id",
        ].astype(str)
    )
    rows = tables["ion_series"]
    tables["ion_series"] = rows[
        ~(
            (rows["series_key"].astype(str) == ion)
            & rows["sample_item_id"].astype(str).isin(after)
        )
    ]
    store = _sync(dataclasses.replace(short_campaign, tables=tables), tmp_path)

    frame = _as_from_rows(
        store, [ion, views.TIC], "rollups", cadence="1h", tz="UTC", start=noon
    )

    of_ion = frame[frame["key"] == ion].set_index("t")["n_samples"]
    assert (of_ion[: pd.Timestamp("2026-06-02T23:00Z")] == 0).all()
    assert (of_ion["2026-06-04"] == 60).all()
    tic = frame[frame["key"] == views.TIC].set_index("t")
    assert tic.loc[noon, "n_samples"] == 60


# --- diurnal ----------------------------------------------------------------------


def test_diurnal_recovers_each_species_known_phase(month):
    campaign, store = month
    cycles = campaign.truth.cycles

    frame = views.diurnal(store, sorted(cycles))

    assert frame.attrs["cadence"] == "hour of day"
    assert frame.attrs["tz"] == ZONE
    for key, rows in frame.groupby("key", observed=True):
        median = rows.set_index("hour")["value"]
        cycle = cycles[key]
        if cycle.peak_hour is None:
            assert median.max() / median.min() < 1.1, key
            continue
        # The peak is on the hour, so the hours either side of it tie
        peak = int(median.idxmax())
        assert peak in (cycle.peak_hour, (cycle.peak_hour - 1) % 24), key
        assert median.max() / median.min() > 1.5, key


def test_diurnal_is_in_the_zone_it_is_asked_for(month):
    campaign, store = month
    key, cycle = next(
        (k, c) for k, c in sorted(campaign.truth.cycles.items()) if c.kind == "day"
    )

    local = views.diurnal(store, key).set_index("hour")["value"]
    utc = views.diurnal(store, key, tz="UTC").set_index("hour")["value"]

    # Helsinki is three hours ahead of UTC in June
    np.testing.assert_allclose(utc.to_numpy(), np.roll(local.to_numpy(), -3))


def _with_nans(campaign: Campaign, values: pd.DataFrame) -> str:
    """The target ion with the most records without a match or a value."""
    ions = values[values["key"].str.startswith("ion:")]
    missing = ions["value"].isna().groupby(ions["key"]).sum()
    assert missing.max() > 0
    return str(missing.idxmax())


def test_diurnal_quartiles_and_counts(month, values):
    campaign, store = month
    key = _with_nans(campaign, values)
    raw = values[values["key"] == key].dropna(subset=["value"])
    hour = raw["t"].dt.tz_convert(ZONE).dt.hour

    frame = views.diurnal(store, key).set_index("hour")

    np.testing.assert_allclose(frame["value"], raw.groupby(hour)["value"].median())
    np.testing.assert_allclose(frame["lo"], raw.groupby(hour)["value"].quantile(0.25))
    np.testing.assert_allclose(frame["hi"], raw.groupby(hour)["value"].quantile(0.75))
    np.testing.assert_array_equal(frame["n"], raw.groupby(hour).size())


@pytest.mark.parametrize(
    "by, groups",
    [
        ("month", ["Jun", "Jul"]),
        ("season", ["JJA"]),
        ("weekday", ["weekday", "weekend"]),
    ],
)
def test_diurnal_splits_by_month_season_or_weekday(month, values, by, groups):
    campaign, store = month
    key = views.TIC

    frame = views.diurnal(store, key, by=by)

    assert list(dict.fromkeys(frame["group"])) == groups
    assert frame.attrs["by"] == by
    assert len(frame) == 24 * len(groups)
    assert frame["n"].sum() == len(campaign.tables["samples"])
    if by == "weekday":
        samples = campaign.tables["samples"]
        local = samples["t_mid_utc"].dt.tz_convert(ZONE)
        weekend = int((local.dt.day_of_week >= 5).sum())
        assert frame.loc[frame["group"] == "weekend", "n"].sum() == weekend


# --- periods ----------------------------------------------------------------------


def _week_of(moment: pd.Timestamp) -> pd.Timestamp:
    local = moment.tz_convert(ZONE).tz_localize(None)
    return local.to_period("W-SUN").start_time.tz_localize(ZONE)


def test_periods_show_the_episode_weeks(month):
    campaign, store = month
    truth = campaign.truth
    keys = sorted(truth.cycles)
    normalise = truth.drift.series_key if truth.drift else "none"
    episode_keys = _episode_keys(campaign)

    frame = views.periods(store, keys, "week", normalise=normalise)

    for key, rows in frame.groupby("key", observed=True):
        p95 = rows.set_index("period")["p95"]
        for episode in truth.episodes:
            if key in episode.series_keys:
                week = _week_of(episode.start)
                assert p95[week] / p95.drop(week).median() > 1.15, key
        if key not in episode_keys:
            assert p95.max() / p95.median() < 1.1, key


def test_periods_are_weeks_from_monday_with_their_distribution(month, values):
    campaign, store = month
    key = _with_nans(campaign, values)
    raw = values[values["key"] == key].dropna(subset=["value"])
    week = raw["t"].map(_week_of)

    frame = views.periods(store, key, "week").set_index("period")

    assert (frame.index.day_of_week == 0).all()
    assert (frame.index.hour == 0).all()
    grouped = raw.groupby(week)["value"]
    np.testing.assert_array_equal(frame["n"], grouped.size())
    np.testing.assert_allclose(frame["median"], grouped.median())
    np.testing.assert_allclose(frame["p95"], grouped.quantile(0.95))
    np.testing.assert_allclose(frame["mean"], grouped.mean())
    assert frame.attrs["cadence"] == "week"
    days = views.periods(store, key, "day")
    assert len(days) == 31  # June in Helsinki, and the three hours of July 1
    assert days.loc[days["period"] == _day(campaign.truth.gap.start), "n"].item() <= (
        3 * 60
    )


def _day(moment: pd.Timestamp) -> pd.Timestamp:
    return moment.tz_convert(ZONE).normalize()


# --- coverage ---------------------------------------------------------------------


def test_the_gap_day_is_a_gap_in_coverage(month):
    campaign, store = month
    gap = campaign.truth.gap

    found = views.coverage(store)

    assert len(found.gaps) == 1
    row = found.gaps.iloc[0]
    assert row["start"] == gap.start
    assert row["end"] == gap.end
    assert row["duration"] == pd.Timedelta(days=1)
    assert row["n_files"] == gap.files
    assert row["n_unfiled"] == gap.files
    assert found.gaps.attrs["view"] == "coverage"
    assert views.coverage(store, min_gap="2D").gaps.empty


def test_coverage_counts_files_and_samples_per_hour_by_polarity_and_mode(month):
    campaign, store = month
    gap = campaign.truth.gap
    files = campaign.tables["files"]

    found = views.coverage(store)
    bins = found.bins

    assert found.n_files == len(files)
    assert found.n_unfiled == gap.files
    assert found.n_samples == len(campaign.tables["samples"])
    polarity = "-" if campaign.params.charge < 0 else "+"
    assert set(zip(bins["polarity"], bins["mode"], bins["mode_name"])) == {
        (polarity, "im-1", "Campaign mode"),
        (polarity, None, None),
    }
    filed = bins[bins["mode"] == "im-1"].set_index("t")
    unfiled = bins[bins["mode"].isna()].set_index("t")
    in_gap = (filed.index >= gap.start) & (filed.index < gap.end)
    assert (filed.loc[~in_gap, "n_files"] == 6).all()
    assert (filed.loc[~in_gap, "n_samples"] == 60).all()
    assert (filed.loc[in_gap, ["n_files", "n_samples"]] == 0).all().all()
    assert (unfiled.loc[in_gap, "n_unfiled"] == 6).all()
    assert (filed["n_unfiled"] == 0).all()
    assert unfiled.loc[~in_gap, "n_files"].sum() == 0
    assert bins["n_files"].sum() == len(files)
    assert bins["n_samples"].sum() == found.n_samples
    assert str(bins["t"].dt.tz) == ZONE
    assert (bins["t"].dt.minute == 0).all()
    assert bins.attrs["cadence"] == "1h"


def test_coverage_of_a_window_inside_the_gap_is_all_gap(month):
    campaign, store = month
    gap = campaign.truth.gap

    found = views.coverage(
        store,
        start=gap.start + pd.Timedelta(hours=6),
        end=gap.start + pd.Timedelta(hours=12),
    )

    assert found.n_samples == 0
    assert found.n_files == 36
    assert len(found.gaps) == 1
    assert found.gaps.iloc[0]["n_unfiled"] == 36


def test_a_gap_at_a_windows_edge_runs_to_its_first_or_last_file(month):
    campaign, store = month
    gap = campaign.truth.gap
    half = pd.Timedelta(hours=12)

    leading = views.coverage(store, start=gap.start + half, end=gap.end + half).gaps
    trailing = views.coverage(store, start=gap.start - half, end=gap.start + half).gaps

    assert list(zip(leading["start"], leading["end"])) == [(gap.start + half, gap.end)]
    assert leading["n_unfiled"].tolist() == [gap.files // 2]
    # The last file of the window starts 10 minutes before its end
    assert list(zip(trailing["start"], trailing["end"])) == [
        (gap.start, gap.start + half)
    ]
    assert trailing["n_files"].tolist() == [gap.files // 2]


def test_a_long_sample_window_over_shorter_ones_leaves_no_gap(short_campaign, tmp_path):
    """A sample that acquires for three hours holds the instrument busy
    though the samples after it in time order end sooner."""
    tables = dict(short_campaign.tables)
    samples = tables["samples"].copy()
    t = samples["t_mid_utc"] - short_campaign.truth.start
    # The first sample acquires for three hours; of the samples cut from
    # its time, the first half hour's stay and the rest go
    samples.loc[0, "t1"] = samples.loc[0, "t0"] + 3 * 3600.0
    gone = (t > pd.Timedelta(minutes=30)) & (t < pd.Timedelta(hours=3))
    ids = set(samples.loc[~gone, "sample_item_id"].astype(str))
    tables["samples"] = samples[~gone].reset_index(drop=True)
    for table in ("ion_series", "peak_series"):
        rows = tables[table]
        tables[table] = rows[rows["sample_item_id"].astype(str).isin(ids)]
    store = _sync(dataclasses.replace(short_campaign, tables=tables), tmp_path)

    gaps = views.coverage(store, min_gap="30min").gaps

    gap = short_campaign.truth.gap
    assert list(zip(gaps["start"], gaps["end"])) == [(gap.start, gap.end)]


def test_a_gap_inside_a_filed_file_counts_the_file_as_filed(short_campaign, tmp_path):
    """The first file's last five minutes have no sample: a gap over a file
    a batch holds."""
    tables = dict(short_campaign.tables)
    samples = tables["samples"]
    gone = samples["sample_file_id"].eq(samples["sample_file_id"].iloc[0]) & (
        samples["t0"] >= 300
    )
    ids = set(samples.loc[~gone, "sample_item_id"].astype(str))
    tables["samples"] = samples[~gone].reset_index(drop=True)
    for table in ("ion_series", "peak_series"):
        rows = tables[table]
        tables[table] = rows[rows["sample_item_id"].astype(str).isin(ids)]
    store = _sync(dataclasses.replace(short_campaign, tables=tables), tmp_path)
    start = short_campaign.truth.start

    gaps = views.coverage(store, min_gap="5min").gaps

    first = gaps.iloc[0]
    assert (first["start"], first["end"]) == (
        start + pd.Timedelta(minutes=5),
        start + pd.Timedelta(minutes=10),
    )
    assert (first["n_files"], first["n_unfiled"]) == (1, 0)


def test_a_file_the_listing_gives_no_polarity_takes_its_samples(
    short_campaign, tmp_path
):
    client = FakeClient(short_campaign)
    client.files["polarity"] = None
    store = _sync(client, tmp_path)

    bins = views.coverage(store).bins

    assert set(zip(bins["polarity"], bins["mode"])) == {("-", "im-1"), (None, None)}
    unfiled = bins[bins["polarity"].isna()]
    assert unfiled["n_files"].sum() == short_campaign.truth.gap.files


def test_coverage_leaves_a_mode_the_server_did_not_name_unnamed(
    short_campaign, tmp_path
):
    client = FakeClient(short_campaign)
    client.fail[("ionization.modes", None)] = AuthenticationError("no")
    store = _sync(client, tmp_path)

    bins = views.coverage(store).bins

    assert set(bins["mode"].dropna()) == {"im-1"}
    assert bins["mode_name"].isna().all()


def test_a_renamed_mode_takes_the_name_of_its_newest_batch_fetched_last(
    short_campaign, tmp_path
):
    client = FakeClient(short_campaign)
    _sync(client, tmp_path)
    client.mode_name = "Renamed mode"
    # Only the newest batch is fetched again, a day on, and lists the new name
    client.modified[client.batch_ids[-1]] += pd.Timedelta(hours=1)
    store = _sync(client, tmp_path, now="2026-08-02T00:00:00Z")

    bins = views.coverage(store).bins

    names = store.table("samples")["ionization_mode_name"]
    assert set(names) == {"Campaign mode", "Renamed mode"}
    assert set(bins.loc[bins["mode"] == "im-1", "mode_name"]) == {"Renamed mode"}


def test_a_renamed_mode_takes_the_name_of_the_batch_fetched_last(
    short_campaign, tmp_path
):
    client = FakeClient(short_campaign)
    _sync(client, tmp_path)
    client.mode_name = "Renamed mode"
    # Only the oldest batch is fetched again, as when its ledger is curated:
    # the newest batch acquired still holds the old name
    client.modified[client.batch_ids[0]] += pd.Timedelta(hours=1)
    # A day on: the refetch is the later fetch
    store = _sync(client, tmp_path, now="2026-08-02T00:00:00Z")

    bins = views.coverage(store).bins

    newest = store.table("samples", batches=[store.batch_ids()[-1]])
    assert set(newest["ionization_mode_name"]) == {"Campaign mode"}
    assert set(bins.loc[bins["mode"] == "im-1", "mode_name"]) == {"Renamed mode"}


def test_an_empty_coverage_has_the_mode_name_column(month):
    _, store = month

    bins = views.coverage(store, start="2030-01-01", end="2030-01-02").bins

    assert bins.empty
    assert list(bins.columns) == [
        "t",
        "polarity",
        "mode",
        "mode_name",
        "n_files",
        "n_unfiled",
        "n_samples",
    ]


def _by_group(bins: pd.DataFrame) -> dict[tuple, dict[str, int]]:
    """Coverage's bins summed per (polarity, mode)."""
    totals: dict[tuple, dict[str, int]] = {}
    for row in bins.itertuples():
        counts = totals.setdefault(
            (row.polarity, row.mode), {"n_files": 0, "n_unfiled": 0, "n_samples": 0}
        )
        for name in counts:
            counts[name] += getattr(row, name)
    return totals


def test_a_polarity_switching_file_counts_under_each_polarity(short_campaign, tmp_path):
    """The first file switches polarity, its first five samples positive;
    a file of the unfiled day is listed as switching too."""
    files = short_campaign.tables["files"]
    filed = files["in_batch"].to_numpy()
    client = FakeClient(short_campaign)
    switching = [files.index[filed][0], files.index[~filed][0]]
    client.files.loc[switching, "polarity"] = "+-"
    first = files.loc[switching[0], "sample_file_id"]
    for batch, rows in client.by_batch.items():
        positive = (rows["sample_file_id"] == first) & (rows["t0"] < 300)
        client.by_batch[batch] = rows.assign(
            polarity=rows["polarity"].mask(positive, "+")
        )
    n_samples = len(short_campaign.tables["samples"])

    found = views.coverage(_sync(client, tmp_path))

    assert _by_group(found.bins) == {
        ("+", "im-1"): {"n_files": 1, "n_unfiled": 0, "n_samples": 5},
        ("-", "im-1"): {
            "n_files": filed.sum(),
            "n_unfiled": 0,
            "n_samples": n_samples - 5,
        },
        ("+", None): {"n_files": 1, "n_unfiled": 1, "n_samples": 0},
        ("-", None): {
            "n_files": (~filed).sum(),
            "n_unfiled": (~filed).sum(),
            "n_samples": 0,
        },
    }
    assert found.n_files == len(files)
    # A scope of one polarity keeps a switching file, under its polarity
    negative = _sync(client, tmp_path / "negative", Scope("Campaign", polarity="-"))
    assert _by_group(views.coverage(negative).bins) == {
        ("-", "im-1"): {
            "n_files": filed.sum(),
            "n_unfiled": 0,
            "n_samples": n_samples - 5,
        },
        ("-", None): {
            "n_files": (~filed).sum(),
            "n_unfiled": (~filed).sum(),
            "n_samples": 0,
        },
    }


def test_a_file_across_the_windows_end_keeps_its_mode(month):
    """The window ends before the file's first sample is timed: the file is
    in the window, and its mode is still its samples'."""
    campaign, store = month
    files = store.table("files")
    began = files.loc[files["in_batch"].astype(bool), "datetime_utc"].min()

    found = views.coverage(store, start=began, end=began + pd.Timedelta(seconds=30))

    polarity = "-" if campaign.params.charge < 0 else "+"
    assert (found.n_files, found.n_samples) == (1, 0)
    assert _by_group(found.bins) == {
        (polarity, "im-1"): {"n_files": 1, "n_unfiled": 0, "n_samples": 0}
    }


# --- composition, spectrum, top ---------------------------------------------------


def test_composition_is_the_formulas_with_their_shares(month, values):
    campaign, store = month
    species = store.table("species").set_index("series_key")

    frame = views.composition(store, "week")

    assert frame["key"].str.startswith("formula:").all()
    shares = frame.groupby("period")["share"].sum()
    np.testing.assert_allclose(shares, 1.0)
    for column in ("o_c", "h_c", "osc", "n_c", "class", "mz"):
        want = frame["key"].map(species[column])
        assert (frame[column].to_numpy() == want.to_numpy()).all() or np.allclose(
            frame[column], want, equal_nan=True
        )
    first = frame[frame["period"] == frame["period"].min()].set_index("key")
    raw = values[values["t"] < pd.Timestamp("2026-06-08", tz=ZONE)]
    raw = raw[raw["key"].str.startswith("formula:")].dropna(subset=["value"])
    grouped = raw.groupby("key")["value"]
    np.testing.assert_allclose(first["mean"], grouped.mean().loc[first.index])
    np.testing.assert_allclose(first["median"], grouped.median().loc[first.index])


def test_composition_by_class_sums_its_formulas(month):
    _, store = month

    by_key = views.composition(store, "all")
    by_class = views.composition(store, "all", by="class").set_index("class")

    assert by_key["period"].nunique() == 1
    want = by_key.groupby("class")
    np.testing.assert_allclose(by_class["mean"], want["mean"].sum())
    np.testing.assert_allclose(by_class["share"], want["share"].sum())
    np.testing.assert_array_equal(by_class["n_keys"], want.size())
    assert set(by_class.index) <= {"CHO", "CHON", "CHOS"}
    by_carbon = views.composition(store, "all", by="n_c")
    np.testing.assert_allclose(by_carbon["share"].sum(), 1.0)


def test_composition_without_a_formula_has_the_columns_and_checks(
    month, short_campaign, tmp_path
):
    """A store synced from a server without batch ledgers holds no formula:
    its composition is empty, with the columns of one that has some."""
    _, full = month
    client = FakeClient(short_campaign)
    client.no_ledger = True
    store = _sync(client, tmp_path)
    start = short_campaign.truth.start + pd.Timedelta(days=1)

    assert not any(views._formula_keys(store))
    for by in (None, *views.COMPOSITION_BY):
        frame = views.composition(store, "week", by=by)
        assert frame.empty, by
        assert list(frame.columns) == list(
            views.composition(full, "week", by=by).columns
        ), by
        assert str(frame["period"].dt.tz) == ZONE
    end = start + pd.Timedelta(days=1)
    windowed = views.composition(store, "day", normalise="tic", start=start, end=end)
    assert windowed.attrs["start"] == start.isoformat()
    assert windowed.attrs["end"] == end.isoformat()
    assert windowed.attrs["normalise_name"] == "TIC"
    with pytest.raises(ValueError, match="normalise"):
        views.composition(store, "week", normalise="reagent")
    with pytest.raises(ValueError, match="holds no series"):
        views.composition(store, "week", normalise="ion:nothing")
    with pytest.raises(ValueError, match="normalise"):
        views.spectrum(store, normalise="reagent")
    assert views.spectrum(store, normalise="tic").attrs["normalise_name"] == "TIC"


def test_the_spectrum_of_an_episode_against_its_background(month):
    campaign, store = month
    episode = campaign.truth.episodes[0]
    day = pd.Timedelta(days=1)

    frame = views.spectrum(
        store, (episode.start, episode.end), (episode.start - day, episode.end - day)
    )

    assert frame["mz"].is_monotonic_increasing
    assert frame["key"].str.startswith("formula:").all()
    # The same hours of two days, so the cycles cancel; a key below
    # detection in some samples has a mean of the others, so only the keys
    # detected throughout compare
    whole = (frame["n"] == frame["n"].max()) & (frame["other_n"] == frame["n"].max())
    ratio = frame[whole].set_index("key")["ratio"]
    assert len(ratio) > 30
    assert set(episode.series_keys) & set(ratio.index)
    for key in ratio.index:
        if key in episode.series_keys:
            assert ratio[key] / episode.factor == pytest.approx(1.0, abs=0.03), key
        else:
            assert ratio[key] == pytest.approx(1.0, abs=0.03), key
    np.testing.assert_allclose(frame["diff"], frame["value"] - frame["other_value"])
    assert frame.attrs["period"] == [episode.start.isoformat(), episode.end.isoformat()]


def test_the_spectrum_of_every_sample_is_each_formulas_mean(month, values):
    _, store = month
    raw = values[values["key"].str.startswith("formula:")].dropna(subset=["value"])

    frame = views.spectrum(store).set_index("key")

    assert "other_value" not in frame
    want = raw.groupby("key")["value"]
    np.testing.assert_allclose(frame["value"], want.mean().loc[frame.index])
    np.testing.assert_array_equal(frame["n"], want.size().loc[frame.index])
    assert frame.attrs["other"] is None


def test_the_spectrum_leaves_out_a_formula_without_an_mz(short_campaign, tmp_path):
    store = _sync(short_campaign, tmp_path)
    species = store.table("species").set_index(
        store.table("species")["series_key"].astype(str)
    )
    key = sorted(k for k in species.index if k.startswith("formula:"))[0]
    # A batch-peak listing that gave no m/z for it
    for batch in store.batch_ids():
        path = store.path / "species" / f"{batch}.parquet"
        rows = store.table("species", batches=[batch])
        rows.loc[rows["series_key"].astype(str) == key, "mz"] = np.nan
        store_module._write(path, rows, "species", {"sample_batch_id": batch})

    frame = views.spectrum(store)

    assert key not in set(frame["key"])
    assert frame["mz"].notna().all()
    assert len(frame) > 0


def test_a_spectrum_across_the_run_change_counts_a_missing_side_as_zero(month):
    campaign, store = month
    change = campaign.truth.run_change
    day = pd.Timedelta(days=1)
    old, new = (set(keys) for keys in zip(*change.readings))

    after_, before_ = (change.at, change.at + day), (change.at - day, change.at)
    frame = views.spectrum(store, after_, before_).set_index("key")
    before = views.spectrum(store, before_)

    assert new <= set(frame.index) and old <= set(frame.index)
    assert frame.loc[sorted(new), "other_value"].isna().all()
    np.testing.assert_allclose(
        frame.loc[sorted(new), "diff"], frame.loc[sorted(new), "value"]
    )
    np.testing.assert_allclose(
        frame.loc[sorted(old), "diff"], -frame.loc[sorted(old), "other_value"]
    )
    assert frame.loc[sorted(new | old), "ratio"].isna().all()
    # A key with no value in the one period is no row
    assert not new & set(before["key"])
    # Either way round, the comparison is across the change, though
    # neither window holds its moment
    hour = pd.Timedelta(hours=1)
    later, earlier = (
        (change.at + hour, change.at + day),
        (change.at - day, change.at - hour),
    )
    for period, other in ((later, earlier), (earlier, later)):
        for keys in (None, sorted(old)):
            # The new run's batches read none of the old formulas, and are
            # still the batches compared with
            flags = views.spectrum(store, period, other, keys=keys).attrs
            assert flags["mixed_runs"], keys
            assert [c["at"] for c in flags["run_changes"]] == [change.at.isoformat()]
    assert not before.attrs["mixed_runs"] and before.attrs["run_changes"] == []
    same = views.spectrum(store, (change.at - 2 * day, change.at - day), before_)
    assert not same.attrs["mixed_runs"] and same.attrs["run_changes"] == []


def test_a_spectrum_needs_an_mz_for_each_key(month):
    campaign, store = month
    ion = sorted(k for k in campaign.truth.cycles if k.startswith("ion:"))[0]

    with pytest.raises(ValueError, match="No m/z"):
        views.spectrum(store, keys=[ion])


def test_top_lists_the_episode_species_as_changers(month):
    campaign, store = month
    episode = campaign.truth.episodes[0]
    # The day before the episode against the day it starts, in Helsinki
    day = _day(episode.start)

    frame = views.top(
        store,
        len(episode.series_keys),
        "ratio",
        period="day",
        start=day - pd.Timedelta(days=1),
        end=day + pd.Timedelta(days=1),
    )

    assert set(frame["key"]) == set(episode.series_keys)
    assert (frame["ratio"] > 1.5).all()
    assert list(frame["rank"]) == list(range(1, len(frame) + 1))
    assert frame.attrs["first_period"] == (day - pd.Timedelta(days=1)).isoformat()
    assert frame.attrs["last_period"] == day.isoformat()
    whole = views.top(
        store,
        100,
        "ratio",
        period="day",
        start=day - pd.Timedelta(days=1),
        end=day + pd.Timedelta(days=1),
    )
    others = whole[~whole["key"].isin(episode.series_keys)]
    assert (others["value"].between(0.9, 1.1)).all()


def test_top_ranks_a_fall_as_a_change(month):
    campaign, store = month
    episode = campaign.truth.episodes[0]
    # The day the episode starts against the day after, when it has ended
    day = _day(episode.start)

    frame = views.top(
        store,
        len(episode.series_keys),
        "ratio",
        period="day",
        start=day,
        end=day + pd.Timedelta(days=2),
    )

    assert set(frame["key"]) == set(episode.series_keys)
    assert (frame["ratio"] < 0.9).all()


def test_an_ion_matched_only_in_the_last_period_is_new(short_campaign, tmp_path):
    """An ion whose records are no-match in every batch but the last is read
    in them, as NaN, and new when it is first matched."""
    tables = dict(short_campaign.tables)
    ions = tables["ion_series"].copy()
    samples = tables["samples"]
    last = samples["sample_batch_id"] == samples["sample_batch_id"].iloc[-1]
    before = set(samples.loc[~last, "sample_item_id"].astype(str))
    key = sorted(short_campaign.truth.cycles)[-1]
    early = (ions["series_key"].astype(str) == key) & ions["sample_item_id"].astype(
        str
    ).isin(before)
    ions.loc[early, "intensity"] = np.nan
    ions.loc[early, "match_category"] = 0
    tables["ion_series"] = ions
    store = _sync(dataclasses.replace(short_campaign, tables=tables), tmp_path)

    frame = views.top(store, 10, "new", keys=[key, views.TIC], period="day", tz="UTC")

    assert key.startswith("ion:")
    assert list(frame["key"]) == [key]


def test_nothing_is_new_in_a_window_of_one_period(month):
    campaign, store = month
    day = _day(campaign.truth.run_change.at)

    frame = views.top(
        store, 10, "new", period="day", start=day, end=day + pd.Timedelta(days=1)
    )

    assert frame.empty
    assert frame.attrs["first_period"] == frame.attrs["last_period"]


def test_nothing_changes_in_a_window_of_one_period(month):
    _, store = month
    week = pd.Timestamp("2026-06-08", tz=ZONE)

    frame = views.top(
        store, 5, "ratio", period="week", start=week, end=week + pd.Timedelta(days=7)
    )

    assert frame.empty
    assert frame.attrs["first_period"] == frame.attrs["last_period"]


def test_top_by_median_is_the_brightest(month, values):
    campaign, store = month
    medians = values.dropna(subset=["value"]).groupby("key")["value"].median()
    # A target ion reads its batch peak's values: ties go by key
    want = (
        medians.rename("median")
        .reset_index()
        .sort_values(["median", "key"], ascending=[False, True])
        .head(5)
        .set_index("key")["median"]
    )

    frame = views.top(store, 5, "median")

    assert list(frame["key"]) == list(want.index)
    np.testing.assert_allclose(frame["value"], want)
    if campaign.truth.drift is not None:
        assert frame["key"].iloc[0] == campaign.truth.drift.series_key


def test_top_by_new_lists_the_formulas_the_new_run_reads(month):
    campaign, store = month
    change = campaign.truth.run_change
    day = _day(change.at)

    frame = views.top(
        store,
        10,
        "new",
        period="day",
        start=day - pd.Timedelta(days=1),
        end=day + pd.Timedelta(days=1),
    )

    assert set(frame["key"]) == {new for _, new in change.readings}
    assert frame["new"].all()
    assert frame["first"].isna().all()
    assert frame.attrs["run_changes"][0]["at"] == change.at.isoformat()


# --- time zones, attrs, runs ------------------------------------------------------


def test_every_frame_states_its_zone_cadence_and_normalisation(month):
    campaign, store = month
    key = sorted(campaign.truth.cycles)[0]
    frames = {
        "series": views.series(store, key, normalise="tic"),
        "diurnal": views.diurnal(store, key),
        "periods": views.periods(store, key, "day"),
        "coverage": views.coverage(store).bins,
        "gaps": views.coverage(store).gaps,
        "composition": views.composition(store, "week"),
        "spectrum": views.spectrum(store),
        "top": views.top(store, 3, "median"),
    }

    for name, frame in frames.items():
        assert ATTRS <= set(frame.attrs), name
        assert frame.attrs["tz"] == ZONE, name
        assert frame.attrs["provenance"] == store.meta["provenance"], name
    assert frames["series"].attrs["normalise"] == "tic"
    assert frames["series"].attrs["normalise_name"] == "TIC"
    assert frames["diurnal"].attrs["normalise"] == "none"
    assert frames["periods"].attrs["cadence"] == "day"


def test_the_zone_is_the_instruments_unless_one_is_named(month):
    _, store = month

    assert views.display_zone(store) == ZONE
    assert views.display_zone(store, "instrument") == ZONE
    assert views.display_zone(store, "UTC") == "UTC"
    frame = views.series(store, views.TIC, cadence="1D", tz="America/New_York")
    assert str(frame["t"].dt.tz) == "America/New_York"
    assert (frame["t"].dt.hour == 0).all()
    with pytest.raises(ValueError, match="no time zone"):
        views.series(store, views.TIC, tz="Mars/Olympus")


class _Meta:
    """A store's metadata and files listing, as :func:`views.display_zone`
    reads them."""

    def __init__(self, meta: dict, zones: list):
        self.meta = meta
        self.zones = zones

    def table(self, name: str) -> pd.DataFrame:
        assert name == "files"
        return pd.DataFrame({"acquisition_timezone": self.zones})


def test_the_instruments_zone_falls_back_to_its_files_then_utc():
    files = ["Asia/Tokyo", "Asia/Tokyo", "UTC"]

    assert views.display_zone(_Meta({"display_tz": "Europe/Oslo"}, files)) == (
        "Europe/Oslo"
    )
    assert views.display_zone(_Meta({}, files)) == "Asia/Tokyo"
    assert views.display_zone(_Meta({"timezone": "Europe/Paris"}, files)) == (
        "Europe/Paris"
    )
    assert views.display_zone(_Meta({}, [None])) == "UTC"
    assert views.display_zone(_Meta({"display_tz": "UTC"}, files), "instrument") == (
        "Asia/Tokyo"
    )


def test_daily_bins_follow_the_zones_clock_change(tmp_path):
    """Helsinki moves its clocks forward on 29 March 2026: that day is 23
    hours long, and its hours skip 03:00."""
    campaign = generate(
        dataclasses.replace(
            SHORT, start=pd.Timestamp("2026-03-27T00:00:00Z"), gap_day=0
        ),
        seed=3,
    )
    store = _sync(campaign, tmp_path)

    days = views.series(store, views.TIC, cadence="1D").set_index("t")
    hours = views.series(store, views.TIC, cadence="1h")["t"]

    change = pd.Timestamp("2026-03-29", tz=ZONE)
    assert days.loc[change, "n_samples"] == 23 * 60
    assert pd.Timestamp("2026-03-30", tz=ZONE) in days.index
    local = hours[hours.dt.date == change.date()]
    assert len(local) == 23
    assert 3 not in set(local.dt.hour)
    by_hour = views.diurnal(store, views.TIC).set_index("hour")["n"]
    assert by_hour[3] < by_hour[2]
    # Steps of hours stay on the zone's clock after the change
    six = views.series(store, views.TIC, cadence="6h").set_index("t")["n_samples"]
    assert set(six.index.hour) == {0, 6, 12, 18}
    assert six[change] == 5 * 60
    assert six[pd.Timestamp("2026-03-29T06:00", tz=ZONE)] == 6 * 60
    # A step that does not divide an hour keeps its length: on the clock,
    # 9 minutes from midnight would leave a bin of 3 at 04:00
    nine = views.series(store, views.TIC, cadence="9min")
    assert (nine["t"].diff().iloc[1:] == pd.Timedelta(minutes=9)).all()
    assert (nine["n_samples"].iloc[1:-1] == 9).all()
    # Nor do hours that do not divide a day
    five = views.series(store, views.TIC, cadence="5h")
    assert (five["n_samples"].iloc[1:-1] == 5 * 60).all()


def test_bins_follow_the_zone_when_its_clocks_go_back(tmp_path):
    """Helsinki moves its clocks back on 25 October 2026: that day is 25
    hours long, and 03:00 comes twice."""
    campaign = generate(
        dataclasses.replace(
            SHORT, start=pd.Timestamp("2026-10-23T00:00:00Z"), gap_day=0
        ),
        seed=3,
    )
    store = _sync(campaign, tmp_path)
    change = pd.Timestamp("2026-10-25", tz=ZONE)

    hours = views.series(store, views.TIC, cadence="1h")
    day = hours[hours["t"].dt.normalize() == change]
    six = views.series(store, views.TIC, cadence="6h").set_index("t")["n_samples"]
    days = views.series(store, views.TIC, cadence="1D").set_index("t")["n_samples"]

    assert len(day) == 25
    assert list(day["t"].dt.hour).count(3) == 2
    assert (day["n_samples"] == 60).all()
    assert set(six.index.hour) == {0, 6, 12, 18}
    assert six[change] == 7 * 60
    assert days[change] == 25 * 60
    nine = views.series(store, views.TIC, cadence="9min")
    assert (nine["n_samples"].iloc[1:-1] == 9).all()


@pytest.mark.parametrize(
    "start, spacing, cadence",
    [
        # Samples 8 and 9 minutes apart bin at 9: on the clock, a bin of 3
        # minutes at 04:00 when the clocks go forward, of 6 at 03:54 when
        # they go back
        ("2026-03-27", 8.6, "9min"),
        ("2026-10-23", 8.6, "9min"),
        # On the clock, the 2-hour bin from 02:00 to 04:00 on the day the
        # clocks go forward is an hour, which holds none of these samples
        ("2026-03-27", 120, "2h"),
    ],
)
def test_the_auto_cadence_leaves_no_bin_empty_across_a_clock_change(
    tmp_path, start, spacing, cadence
):
    campaign = generate(
        dataclasses.replace(SHORT, start=pd.Timestamp(start, tz="UTC"), gap_day=0),
        seed=3,
    )
    minute = _minutes(campaign)
    wanted = np.round(60 + np.arange(minute.max() // spacing) * spacing).astype(int)
    store = _sync(_keep(campaign, minute.isin(wanted)), tmp_path)

    frame = views.series(store, views.TIC)
    raw = views.series(store, views.TIC, cadence="raw")

    assert frame.attrs["cadence"] == cadence
    assert (frame["t"].diff().iloc[1:] == pd.Timedelta(cadence)).all()
    assert (frame["n_samples"] > 0).all()
    # Nor does a raw line break between them
    assert (raw["n_samples"] == 1).all()


def test_a_formulas_run_change_is_flagged(month):
    campaign, store = month
    change = campaign.truth.run_change
    formula = sorted(k for k in campaign.truth.cycles if k.startswith("formula:"))[0]
    ion = sorted(k for k in campaign.truth.cycles if k.startswith("ion:"))[0]

    flagged = views.series(store, formula, cadence="1D")
    before = views.series(store, formula, cadence="1D", end=change.at)
    ions = views.series(store, ion, cadence="1D")

    assert flagged.attrs["mixed_runs"]
    assert [c["at"] for c in flagged.attrs["run_changes"]] == [change.at.isoformat()]
    assert not before.attrs["mixed_runs"]
    assert before.attrs["run_changes"] == []
    after = views.series(
        store, formula, cadence="1D", start=change.at + pd.Timedelta(days=1)
    )
    assert not after.attrs["mixed_runs"]
    assert after.attrs["run_changes"] == []
    assert not ions.attrs["mixed_runs"]
    assert ions.attrs["run_changes"] == []


def test_a_run_change_of_the_formula_normalised_by_is_flagged(month):
    """Every value is divided by the formula, which a run reads: an ion's
    values over a run change are flagged when they are normalised by one."""
    campaign, store = month
    change = campaign.truth.run_change
    renamed = _renamed(campaign)
    formulas = sorted(k for k in campaign.truth.cycles if k.startswith("formula:"))
    kept = [k for k in formulas if k not in renamed][0]
    old = change.readings[0][0]
    ion = sorted(k for k in campaign.truth.cycles if k.startswith("ion:"))[0]
    day = pd.Timedelta(days=1)

    for formula in (kept, old):
        flags = views.series(
            store, ion, normalise=formula, start=change.at - day, end=change.at + day
        ).attrs
        assert flags["mixed_runs"], formula
        assert [c["at"] for c in flags["run_changes"]] == [change.at.isoformat()]
    before = views.periods(
        store, ion, "day", normalise=kept, start=change.at - day, end=change.at
    )
    assert not before.attrs["mixed_runs"] and before.attrs["run_changes"] == []


def test_a_run_change_of_the_other_polarity_is_not_the_formulas(
    short_campaign, tmp_path
):
    """A formula is read in one polarity only: a change of reading between
    the other polarity's batches is not flagged on its series."""
    client = FakeClient(short_campaign)
    first, middle, last = client.batch_ids
    client.runs[first] = f"run-2:{first}"
    client.runs[middle] = f"run-1:{middle}"
    client.runs[last] = f"run-2:{last}"
    client.batch_polarity[middle] = client.batch_polarity[last] = "+"
    store = _sync(client, tmp_path)
    # Two formulas every batch reads, whichever run
    renamed = _renamed(short_campaign)
    negative, positive = sorted(
        k
        for k in short_campaign.truth.cycles
        if k.startswith("formula:") and k not in renamed
    )[:2]
    ion = sorted(k for k in short_campaign.truth.cycles if k.startswith("ion:"))[0]
    # The negative batch reads one formula, the positive ones the other
    for batch in client.batch_ids:
        dropped = positive if batch == first else negative
        for table in ("peak_series", "species"):
            path = store.path / table / f"{batch}.parquet"
            rows = store.table(table, batches=[batch])
            rows = rows[rows["series_key"].astype(str) != dropped]
            store_module._write(path, rows, table, {"sample_batch_id": batch})
    change = store.status().run_changes

    assert [(c.sample_batch_id, c.polarity) for c in change] == [(last, "+")]
    for view in (
        views.series(store, negative),
        views.series(store, negative, cadence="1h", tz="UTC"),
        views.periods(store, negative, "day"),
        views.series(store, views.TIC, normalise=negative),
        # An ion is no run's reading
        views.series(store, views.TIC, normalise=ion),
    ):
        assert not view.attrs["mixed_runs"]
        assert view.attrs["run_changes"] == []
    rolled = views.series(store, positive, cadence="1h", tz="UTC")
    assert rolled.attrs["source"] == "rollups"
    for view in (
        rolled,
        views.series(store, positive),
        views.series(store, [negative, positive]),
        views.series(store, views.TIC, normalise=positive),
    ):
        assert view.attrs["mixed_runs"]
        assert view.attrs["run_changes"] == [change[0].to_dict()]


def test_a_window_draws_a_batch_by_its_samples_times(short_campaign, tmp_path):
    """A window that starts after the last file of a batch started, but
    before its last sample's middle, draws that batch: a run change to the
    next one is flagged, with the batch's samples on the chart."""
    store = _sync(short_campaign, tmp_path)
    change = store.status().run_changes[0]
    batches = store.status().batches.set_index("sample_batch_id")
    samples = store.table("samples")
    position = list(batches.index).index(change.sample_batch_id)
    earlier = batches.index[position - 1]
    last_start = batches.loc[earlier, "last_datetime_utc"]
    last_mid = samples.loc[
        samples["sample_batch_id"].astype(str) == earlier, "t_mid_utc"
    ].max()
    start = last_start + (last_mid - last_start) / 2
    end = change.at + pd.Timedelta(hours=1)
    formula = sorted(
        k for k in short_campaign.truth.cycles if k.startswith("formula:")
    )[0]

    assert last_start < start < last_mid
    frame = views.series(store, formula, cadence="raw", start=start, end=end)
    drawn = views.series(store, views.TIC, cadence="raw", start=start, end=end)

    assert (drawn["t"] < change.at).any()
    assert frame.attrs["mixed_runs"]
    assert frame.attrs["run_changes"] == [change.to_dict()]
    # Without the batch's last samples, there is no change to flag
    after = last_mid + pd.Timedelta(seconds=1)
    later = views.series(store, formula, cadence="raw", start=after, end=end)
    assert not later.attrs["mixed_runs"]
    assert later.attrs["run_changes"] == []


def test_keys_can_be_a_column_an_index_or_an_array(month):
    """A view takes the keys as ``store.table("species")`` lists them."""
    _, store = month
    column = store.table("species")["series_key"]
    column = column[column.astype(str).str.startswith("formula:")].iloc[:3]
    keys = list(column.astype(str))

    for given in (column, pd.Index(column), column.to_numpy()):
        pd.testing.assert_frame_equal(
            views.series(store, given, cadence="1D"),
            views.series(store, keys, cadence="1D"),
        )
        pd.testing.assert_frame_equal(
            views.top(store, 2, "median", keys=given),
            views.top(store, 2, "median", keys=keys),
        )
        pd.testing.assert_frame_equal(
            views.composition(store, "week", keys=given),
            views.composition(store, "week", keys=keys),
        )
        pd.testing.assert_frame_equal(
            views.spectrum(store, keys=given), views.spectrum(store, keys=keys)
        )


def test_a_key_the_store_does_not_hold_is_an_error(month):
    _, store = month

    with pytest.raises(ValueError, match="holds no series"):
        views.series(store, "ion:nothing")
    with pytest.raises(ValueError, match="holds no series"):
        views.series(store, views.TIC, normalise="ion:nothing")
    with pytest.raises(ValueError, match="No keys"):
        views.series(store, [])


@pytest.mark.parametrize(
    "call, message",
    [
        (
            lambda s: views.series(s, "tic", cadence="fortnightly"),
            "give 'auto', 'raw' or a pandas offset",
        ),
        # Coverage takes neither
        (
            lambda s: views.coverage(s, cadence="auto"),
            "cadence='auto' is none: give a pandas offset",
        ),
        (lambda s: views.series(s, "tic", stat="mode"), "stat"),
        (lambda s: views.series(s, "tic", band=(90, 10)), "band"),
        (lambda s: views.series(s, "tic", normalise="reagent"), "normalise"),
        (lambda s: views.diurnal(s, "tic", by="year"), "by="),
        (lambda s: views.periods(s, "tic", "year"), "period="),
        (lambda s: views.composition(s, "year"), "period="),
        (lambda s: views.composition(s, "all", by="o_c"), "by="),
        (lambda s: views.top(s, 0, "median"), "n="),
        (lambda s: views.top(s, 3, "loudest"), "by="),
        (lambda s: views.spectrum(s, ("2026-06-02", "2026-06-01")), "ends before"),
    ],
)
def test_a_wrong_argument_says_what_is_wrong(month, call, message):
    _, store = month

    with pytest.raises(ValueError, match=message):
        call(store)
