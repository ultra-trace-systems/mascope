"""
The composition snapshots of tier 1 (``docs/dev/visual_digest.md``,
section 3): ``spectrogram``, ``period_spectrum`` and ``van_krevelen``, on the
synthetic campaign synced into a campaign store.

The assertions are against the answers the campaign was built from
(:class:`fixtures.campaign.Truth`) and its own tables, and on the specs and
summaries; each chart renders on both backends.
"""

import dataclasses
import inspect
import io
import json
import re

import numpy as np
import pandas as pd
import pytest
from fixtures.campaign import Campaign, generate
from fixtures.server import FakeClient
from test_views import (  # noqa: F401 - short_campaign is a fixture, used by name
    SHORT_CLUSTERS,
    ZONE,
    _sync,
    short_campaign,
)

from mascope_viz import render
from mascope_viz.charts import Figure, registry
from mascope_viz.charts import common as chart_common
from mascope_viz.charts.spectrogram import spectrogram
from mascope_viz.charts.spectrum import period_spectrum
from mascope_viz.charts.van_krevelen import van_krevelen
from mascope_viz.data import store as store_module
from mascope_viz.data import views
from mascope_viz.data.store import CampaignStore


DAY = pd.Timedelta(days=1)


@pytest.fixture(scope="module")
def month(synced) -> tuple[Campaign, CampaignStore]:
    campaign, _, report = synced
    return campaign, report.store


@pytest.fixture(scope="module")
def clusters(cluster_campaign, tmp_path_factory) -> tuple[Campaign, CampaignStore]:
    """The halide month with its reagent over the bare ion and its water
    cluster: MAS-1094's two-reagent parameter set."""
    store = _sync(cluster_campaign, tmp_path_factory.mktemp("clusters"))
    return cluster_campaign, store


def _drawn(fig: Figure) -> None:
    """*fig* renders on both backends in both themes, and reads back from
    its document to an equal figure."""
    for theme in ("light", "dark"):
        drawn = render.matplotlib(fig, theme=theme)
        buffer = io.BytesIO()
        drawn.savefig(buffer, format="png")
        assert buffer.getvalue().startswith(b"\x89PNG")
        json.loads(render.plotly(fig, theme=theme).to_json())
    document = fig.to_json()
    assert Figure.from_json(document) == fig
    json.loads(document)


def _formula_keys(keys) -> list[str]:
    return sorted(k for k in keys if k.startswith("formula:"))


def _signal(campaign: Campaign) -> str:
    return "peak area" if campaign.params.instrument_type == "TOF" else "peak height"


# --- spectrogram ------------------------------------------------------------------


def test_the_spectrograms_largest_columns_fall_in_the_episodes(month):
    """The ten largest hourly column sums lie in the episodes, the largest
    in the first, whose factor is the larger; in the halide month all ten
    lie in it."""
    campaign, store = month
    episodes = campaign.truth.episodes

    fig = spectrogram(store)

    columns = fig.summary["top_columns"]
    assert len(columns) == 10
    for column in columns:
        start, end = pd.Timestamp(column["t"]), pd.Timestamp(column["end"])
        assert any(e.start <= start and end <= e.end for e in episodes), column
        assert column["t"].endswith("+03:00")
    first = episodes[0]
    assert first.start <= pd.Timestamp(columns[0]["t"]) < first.end
    if campaign.params.name == "halide_negative":
        assert all(first.start <= pd.Timestamp(c["t"]) < first.end for c in columns)
    totals = [column["total"] for column in columns]
    assert totals == sorted(totals, reverse=True)
    _drawn(fig)


def test_a_spectrogram_draws_its_gap_day_as_a_gap_and_says_what_it_shows(month):
    campaign, store = month
    gap = campaign.truth.gap

    fig = spectrogram(store)

    data = fig.data
    summary = fig.summary
    gaps = data[data["gap"]]
    assert summary["n_gap_bins"] == 24
    assert len(gaps) == 24 * summary["n_rows"]
    assert ((gaps["start"] >= gap.start) & (gaps["start"] < gap.end)).all()
    assert gaps["value"].isna().all()
    # Every row and every bin of the grid has its place, so no cell
    # stretches over one
    assert data["mz"].nunique() == summary["n_rows"]
    assert data["start"].nunique() == summary["n_bins"]
    assert (data.loc[~data["gap"] & data["value"].notna(), "value"] > 0).all()
    # A cell sits at the middle of its bin
    assert ((data["t"] - data["start"]) == pd.Timedelta(minutes=30)).all()
    panel = fig.panels[0]
    assert panel.primitive == "heatmap"
    assert panel.encodings == {"x": "t", "y": "mz", "value": "value", "gap": "gap"}
    assert panel.options["mode"] == "marks"
    assert "assigned peaks" in fig.title
    assert "assigned peaks only" in fig.subtitle
    assert "1h means" in fig.subtitle and "rows of 1 Th" in fig.subtitle
    assert chart_common.ASSIGNED_ONLY in fig.notes
    assert panel.axes.y.label == "m/z (ion, adduct included)"
    assert panel.axes.colour.label == f"{_signal(campaign).capitalize()}, bin mean"
    assert fig.context.cadence == "1h" and fig.context.stat == "mean"


def test_the_spectrogram_colours_between_its_cells_p1_and_p99(month):
    _, store = month
    cells = views.spectrogram(store).cells["value"].to_numpy()
    low, high = np.percentile(cells[cells > 0], [1, 99])

    fig = spectrogram(store)

    colour = fig.panels[0].axes.colour
    assert colour.scale == "log"
    assert colour.range == pytest.approx((low, high))
    assert (fig.summary["colour_low"], fig.summary["colour_high"]) == pytest.approx(
        (low, high)
    )
    assert any("1st to the 99th percentile" in note for note in fig.notes)


def test_the_spectrogram_marks_the_run_change_on_its_time_axis(month):
    campaign, store = month
    change = campaign.truth.run_change

    fig = spectrogram(store)
    before = spectrogram(store, end=change.at)

    (rule,) = fig.panels[0].options["rules"]
    assert rule["at"] == change.at
    # The rule names the run the formulas are read by after it
    (summary_change,) = fig.summary["run_changes"]
    assert rule["label"] == f"engine run: {summary_change['after']}"
    assert fig.summary["mixed_runs"]
    assert [c["at"] for c in fig.summary["run_changes"]] == [
        change.at.tz_convert(ZONE).isoformat()
    ]
    (note,) = [n for n in fig.notes if n.startswith("The engine run that reads")]
    assert "two readings, not one series" in note
    assert fig.context.mixed_runs
    assert not before.summary["mixed_runs"]
    assert before.panels[0].options["rules"] == []
    assert not any("engine run" in note for note in before.notes)
    # Without a gap day to hold them, the rows without a value still have
    # their place
    assert before.summary["n_gap_bins"] == 0
    assert before.data["mz"].nunique() == before.summary["n_rows"]
    assert before.data["value"].isna().any()


def test_the_spectrogram_summary_names_its_brightest_rows(month):
    _, store = month
    rows = views.spectrogram(store).rows

    fig = spectrogram(store, top_n=5)

    listed = fig.summary["top_rows"]
    want = rows.dropna(subset=["total"]).nlargest(5, "total")
    assert [r["mz"] for r in listed] == list(want["mz"])
    for entry, row in zip(listed, want.itertuples()):
        assert entry["species"] == {
            "key": row.key,
            "name": row.name,
            "formula": row.formula,
        }
        assert 0 < entry["share"] <= 1
        assert entry["presence"] == pytest.approx(row.n / row.n_samples)
    assert fig.summary["n_rows"] == len(rows)
    assert fig.summary["mz_width"] == 1.0 and not fig.summary["widened"]
    assert len(fig.summary["top_columns"]) == 5


def test_an_hour_with_samples_and_nothing_detected_is_blank_not_a_gap(
    short_campaign,  # noqa: F811
    tmp_path,
):
    """Two kinds of empty: an hour without samples is a gap; an hour whose
    samples hold no detected peak is blank, and still has its place."""
    tables = dict(short_campaign.tables)
    samples = tables["samples"]
    hour = pd.Timestamp("2026-06-02T10:00Z")
    quiet = samples["sample_item_id"][
        (samples["t_mid_utc"] >= hour)
        & (samples["t_mid_utc"] < hour + pd.Timedelta(hours=1))
    ].astype(str)
    peaks = tables["peak_series"].copy()
    peaks.loc[peaks["sample_item_id"].astype(str).isin(quiet), "intensity"] = np.nan
    tables["peak_series"] = peaks
    store = _sync(dataclasses.replace(short_campaign, tables=tables), tmp_path)

    grid = views.spectrogram(store)
    fig = spectrogram(store)

    bin_ = grid.bins[grid.bins["t"] == hour]
    assert bin_["n_samples"].iloc[0] == len(quiet) == 60
    assert np.isnan(bin_["total"].iloc[0])
    assert not (grid.cells["t"] == hour).any()
    drawn = fig.data[fig.data["start"] == hour]
    assert len(drawn) >= 1 and not drawn["gap"].any()
    assert drawn["value"].isna().all()
    assert fig.data["start"].nunique() == fig.summary["n_bins"]
    _drawn(fig)


def test_a_widened_spectrogram_says_so(month):
    _, store = month
    rows = len(views.spectrogram(store).rows)

    fig = spectrogram(store, max_rows=rows // 3)

    width = fig.summary["mz_width"]
    assert fig.summary["widened"] and width > 1
    assert fig.summary["mz_width_asked"] == 1.0
    assert f"rows of {width:g} Th" in fig.subtitle
    assert any(
        f"{width:g} Th wide, not the 1 Th asked for" in note for note in fig.notes
    )


def test_a_normalised_spectrogram_labels_its_signal(month):
    _, store = month

    fig = spectrogram(store, normalise="tic", cadence="1D")

    assert fig.panels[0].axes.colour.label == "signal / TIC, bin mean"
    assert "normalised to TIC" in fig.subtitle
    assert fig.summary["normalise"] == "tic"
    assert fig.summary["cadence"] == "1D"
    _drawn(fig)


# --- period_spectrum ------------------------------------------------------------------


def _normaliser(campaign: Campaign) -> str:
    """The reagent ion, where the chemistry has one; else the TIC."""
    drift = campaign.truth.drift
    return drift.series_key if drift else "tic"


def _episode_spectrum(store: CampaignStore, campaign: Campaign, **kwargs) -> Figure:
    episode = campaign.truth.episodes[0]
    return period_spectrum(
        store,
        start=episode.start,
        end=episode.end,
        other_start=episode.start - DAY,
        other_end=episode.end - DAY,
        **kwargs,
    )


def test_the_difference_spectrum_of_an_episode_is_its_species(month):
    """An episode against the same hours of the day before, normalised by
    the reagent ion: the increases are the episode's species, at its
    factor, and they hold nearly all of the difference."""
    campaign, store = month
    episode = campaign.truth.episodes[0]
    species = _formula_keys(episode.series_keys)

    fig = _episode_spectrum(store, campaign, normalise=_normaliser(campaign))

    increases = fig.summary["increases"]
    assert {i["species"]["key"] for i in increases[: len(species)]} == set(species)
    for entry in increases[: len(species)]:
        assert entry["ratio"] == pytest.approx(episode.factor, rel=0.1)
    difference = fig.data[fig.data["part"] == "difference"]
    size = difference["diff"].abs()
    assert size[difference["key"].isin(species)].sum() / size.sum() > 0.9
    assert fig.title == "Difference spectrum"
    assert len(fig.panels) == 2 and fig.share_x
    assert fig.panels[1].options["by_sign"]
    _drawn(fig)


def test_under_the_reagent_sum_the_difference_is_the_episodes_and_names_both(
    clusters,
):
    campaign, store = clusters
    episode = campaign.truth.episodes[0]
    species = _formula_keys(episode.series_keys)
    reagents = list(campaign.truth.shares.series_keys)

    fig = _episode_spectrum(store, campaign, normalise=reagents)

    increases = fig.summary["increases"]
    assert {i["species"]["key"] for i in increases[: len(species)]} == set(species)
    assert "normalised to reagent ion + reagent water cluster" in fig.subtitle
    assert fig.summary["normalise_members"] == reagents
    assert fig.summary["normalise_complete"] == 1.0
    assert fig.panels[0].axes.y.label.startswith(
        "Mean signal / reagent ion + reagent water cluster"
    )
    assert not any("partial sum" in note for note in fig.notes)
    # Over the stretch the cluster is below detection, the sum is partial
    lost = campaign.truth.shares.lost
    partial = period_spectrum(
        store, start=lost[0] - DAY / 4, end=lost[1], normalise=reagents
    )
    share = 1 - partial.summary["normalise_complete"]
    assert 0 < share < 1
    (note,) = [n for n in partial.notes if "partial sum" in n]
    assert note == (
        f"The denominator was a partial sum in {share:.1%} of the samples: "
        "one of its ions had no value there, and the others divided."
    )
    # By the cluster alone, those samples have no value at all
    alone = period_spectrum(
        store, start=lost[0] - DAY / 4, end=lost[1], normalise=reagents[1]
    )
    assert any(
        "reagent water cluster had no value, so those samples have none" in n
        for n in alone.notes
    )


def test_a_stick_sits_at_its_ion_mz_labelled_with_its_neutral_formula(month):
    campaign, store = month
    species = campaign.tables["species"].set_index("series_key")
    # A night in which a peak is seen in about a third of the samples
    night = pd.Timestamp("2026-06-03T00:00", tz="UTC")

    fig = period_spectrum(store, start=night, end=night + DAY / 3, top_n=60)

    data = fig.data
    assert len(fig.panels) == 1 and fig.title == "Period spectrum"
    np.testing.assert_allclose(data["mz"], data["key"].map(species["mz"]))
    assert (data["label"] == data["formula"]).all()
    assert (data["formula"] == data["key"].map(species["formula"])).all()
    assert (data["ion_formula"] == data["key"].map(species["ion_formula"])).all()
    assert (data["ion_formula"] != data["formula"]).all()
    presence = data["n"] / data["n_samples"]
    np.testing.assert_allclose(data["presence"], presence)
    # Every stick is labelled at this top_n: those under half are marked
    assert (data["mark"] == (presence < 0.5)).all()
    assert data["mark"].any() and not data["mark"].all()
    seen = data.loc[data["mark"]].iloc[0]
    assert f"({seen['n']} of {seen['n_samples']})" in seen["hover"]
    # Only a labelled stick is marked: at a small top_n the faint peak seen
    # in a third of the samples is neither
    few = period_spectrum(store, start=night, end=night + DAY / 3, top_n=3).data
    faint = few["presence"] < 0.5
    assert faint.any() and few.loc[faint, "label"].isna().all()
    assert not few["mark"].any()
    assert few["label"].notna().sum() == 3
    assert fig.panels[0].encodings["hover"] == "hover"
    assert fig.panels[0].options["mark_label"] == "seen in under half of the samples"
    assert fig.panels[0].axes.x.label == "m/z (ion, adduct included)"
    assert "period means" in fig.subtitle
    # A window given in UTC is summarised in the display zone, and the
    # summary's window is the period's, as the figure's context says
    bounds = [t.tz_convert(ZONE).isoformat() for t in (night, night + DAY / 3)]
    assert bounds[0].endswith("+03:00")
    assert fig.summary["period"] == bounds
    assert [fig.summary["start"], fig.summary["end"]] == bounds
    context = (pd.Timestamp(fig.context.start), pd.Timestamp(fig.context.end))
    assert context == (night, night + DAY / 3)
    _drawn(fig)


def test_the_top_sticks_are_labelled_and_listed(month):
    _, store = month

    fig = period_spectrum(store, top_n=4)

    data = fig.data
    tallest = data.nlargest(4, "value")
    assert set(data.loc[data["label"].notna(), "key"]) == set(tallest["key"])
    listed = fig.summary["top"]
    assert [e["species"]["key"] for e in listed] == list(tallest["key"])
    assert [e["value"] for e in listed] == pytest.approx(list(tallest["value"]))
    assert fig.summary["n_peaks"] == len(data)
    assert fig.summary["other"] is None and "increases" not in fig.summary
    json.dumps(fig.summary, allow_nan=False)


def test_the_mirror_draws_the_other_period_below(month):
    campaign, store = month

    fig = _episode_spectrum(store, campaign, top_n=3)

    mirror = fig.frame(fig.panels[0])
    up, down = mirror[mirror["side"] == "period"], mirror[mirror["side"] == "other"]
    np.testing.assert_allclose(up["y"], up["value"])
    np.testing.assert_allclose(down["y"], -down["other_value"])
    assert set(up.loc[up["label"].notna(), "key"]) == set(
        up.nlargest(3, "value")["key"]
    )
    assert set(down.loc[down["label"].notna(), "key"]) == set(
        down.nlargest(3, "other_value")["key"]
    )
    assert fig.panels[0].options["top_n"] >= len(mirror)
    # A labelled stick seen in under half of its own period's samples is
    # marked, on either side
    own = np.where(
        mirror["side"] == "period", mirror["presence"], mirror["other_presence"]
    )
    np.testing.assert_array_equal(
        mirror["mark"], mirror["label"].notna().to_numpy() & (own < 0.5)
    )
    # A morning, in which a peak of the fixture is seen in few samples,
    # against the afternoon, in which it is seen in all: every stick
    # labelled, and each side marked by its own period's presence
    morning = pd.Timestamp("2026-06-02T06:00", tz="UTC")
    wide = period_spectrum(
        store,
        start=morning,
        end=morning + DAY / 12,
        other_start=morning + DAY / 3,
        other_end=morning + DAY * 5 / 12,
        top_n=60,
    )
    mirrored = wide.frame(wide.panels[0])
    own = np.where(
        mirrored["side"] == "period", mirrored["presence"], mirrored["other_presence"]
    )
    np.testing.assert_array_equal(mirrored["mark"], own < 0.5)
    sides = mirrored[mirrored["label"].notna()].pivot(
        index="key", columns="side", values="mark"
    )
    crossing = sides[sides["period"] != sides["other"]]
    assert len(crossing) and crossing["period"].all()
    # On plotly, a stick's hover says which period it is, its name uncut:
    # both periods of this pair start "2026-06-02 ..."
    names = {
        t.name: (t.hoverinfo, t.hoverlabel.namelength)
        for t in render.plotly(wide).data
        if t.mode == "lines"
    }
    assert set(names) >= set(mirrored["side_name"])
    assert {names[name] for name in mirrored["side_name"]} == {("x+y+text+name", -1)}
    assert fig.panels[0].options["colours"] == {
        "period": "series.1",
        "other": "series.2",
    }
    assert [e["species"]["key"] for e in fig.summary["other_top"]] == list(
        down.nlargest(3, "other_value")["key"]
    )
    names = set(mirror["side_name"])
    assert names == {
        "2026-06-07 12:00 to 2026-06-08 06:00",
        "2026-06-06 12:00 to 2026-06-07 06:00",
    }
    assert "2026-06-07 12:00 to 2026-06-08 06:00 against 2026-06-06" in fig.subtitle


def test_a_peak_in_one_period_only_is_marked_and_counts_zero(month):
    campaign, store = month
    change = campaign.truth.run_change
    old, new = (sorted(keys) for keys in zip(*change.readings))

    fig = period_spectrum(
        store,
        start=change.at,
        end=change.at + DAY,
        other_start=change.at - DAY,
        other_end=change.at,
        top_n=60,
    )

    difference = fig.frame(fig.panels[1]).set_index("key")
    assert (difference.loc[new, "only_in"] == "period").all()
    assert (difference.loc[old, "only_in"] == "other").all()
    assert difference.loc[new + old, "mark"].all()
    assert difference["mark"].sum() == len(new) + len(old)
    np.testing.assert_allclose(difference.loc[new, "y"], difference.loc[new, "value"])
    np.testing.assert_allclose(
        difference.loc[old, "y"], -difference.loc[old, "other_value"]
    )
    assert fig.panels[1].options["mark_label"] == "only in one period"
    listed = {
        e["species"]["key"]: e
        for e in fig.summary["increases"] + fig.summary["decreases"]
    }
    for key in new:
        assert listed[key]["only_in"] == "period" and listed[key]["ratio"] is None
    for key in old:
        assert listed[key]["only_in"] == "other" and listed[key]["ratio"] is None
    # The windows join two engine runs: flagged, never silently
    assert fig.summary["mixed_runs"]
    assert any("engine run" in note for note in fig.notes)
    json.dumps(fig.summary, allow_nan=False)


def test_increases_and_decreases_are_listed_apart_with_their_ratios(month):
    campaign, store = month

    fig = _episode_spectrum(store, campaign, top_n=5)

    up, down = fig.summary["increases"], fig.summary["decreases"]
    assert len(up) == len(down) == 5
    assert all(e["diff"] > 0 for e in up) and all(e["diff"] < 0 for e in down)
    assert [e["diff"] for e in up] == sorted((e["diff"] for e in up), reverse=True)
    assert [e["diff"] for e in down] == sorted(e["diff"] for e in down)
    frame = fig.frame(fig.panels[1]).set_index("key")
    for entry in up + down:
        assert entry["ratio"] == pytest.approx(entry["value"] / entry["other_value"])
        key = entry["species"]["key"]
        assert entry["presence"] == pytest.approx(frame.loc[key, "presence"])
        assert entry["other_presence"] == pytest.approx(
            frame.loc[key, "other_presence"]
        )
        assert entry["diff"] == pytest.approx(entry["value"] - entry["other_value"])
    labelled = fig.frame(fig.panels[1])
    assert fig.panels[1].options["top_n"] == 5
    assert labelled["label"].notna().all()


def test_raw_values_of_periods_far_apart_carry_a_drift_note(month):
    campaign, store = month
    episode = campaign.truth.episodes[0]
    week = pd.Timedelta(days=7)

    far = period_spectrum(
        store,
        start=episode.start,
        end=episode.end,
        other_start=episode.start + week,
        other_end=episode.end + week,
    )
    normalised = period_spectrum(
        store,
        start=episode.start,
        end=episode.end,
        other_start=episode.start + week,
        other_end=episode.end + week,
        normalise="tic",
    )
    near = _episode_spectrum(store, campaign)

    assert any("7.0 days apart" in note for note in far.notes)
    assert not any("days apart" in note for note in normalised.notes)
    assert not any("days apart" in note for note in near.notes)


# --- van_krevelen ---------------------------------------------------------------------


_ATOM = re.compile(r"([A-Z][a-z]?)(\d*)")


def _ratios(formula: str) -> tuple[float, float]:
    counts = {e: int(n or 1) for e, n in _ATOM.findall(formula)}
    return counts.get("O", 0) / counts["C"], counts["H"] / counts["C"]


def _tier_of(campaign: Campaign) -> dict[str, str]:
    rows = campaign.tables["peak_series"].dropna(subset=["intensity"])
    first = rows.drop_duplicates("series_key")
    return dict(zip(first["series_key"].astype(str), first["tier"].astype(str)))


def test_the_van_krevelen_places_each_formula_at_its_neutral_ratios(month):
    """Over a window inside one run: the fifty formulas, of which five are
    candidates, so forty-five points by default, each at its neutral
    formula's O/C and H/C, and the classes counted."""
    campaign, store = month
    tier = _tier_of(campaign)

    fig = van_krevelen(
        store, start=campaign.truth.start, end=campaign.truth.run_change.at
    )

    data = fig.data
    summary = fig.summary
    assert summary["n_formulas"] == campaign.params.n_species == 50
    assert summary["by_tier"] == {"assigned": 45, "candidate": 5}
    assert summary["n_points"] == len(data) == 45
    assert (data["key"].map(tier) == "assigned").all()
    for row in data.itertuples():
        assert (row.o_c, row.h_c) == pytest.approx(_ratios(row.formula)), row.formula
        assert row.formula != row.ion_formula
    species = campaign.tables["species"].set_index("series_key")
    classes = data["key"].map(species["class"]).value_counts()
    assert summary["by_class"] == {
        name: int(classes.get(name, 0)) for name in ("CHO", "CHON", "CHOS", "CHONS")
    } | {"other": 0}
    assert not summary["mixed_runs"]
    assert not data["hollow"].any() and summary["n_hollow"] == 0
    assert any("neutral formula" in note for note in fig.notes)
    assert any("Not drawn: 5 of tier candidate" in note for note in fig.notes)
    _drawn(fig)


def test_over_the_whole_month_the_run_change_adds_its_readings(month):
    campaign, store = month
    tier = _tier_of(campaign)
    keys = _formula_keys(tier)

    fig = van_krevelen(store)

    assert fig.summary["n_formulas"] == len(keys) == 53
    assert fig.summary["n_points"] == sum(tier[k] == "assigned" for k in keys)
    assert fig.summary["mixed_runs"]
    assert any("engine run" in note for note in fig.notes)


def test_candidates_are_drawn_hollow_and_counted_apart_when_asked_for(month):
    campaign, store = month
    tier = _tier_of(campaign)

    fig = van_krevelen(
        store,
        tiers=["assigned", "candidate"],
        start=campaign.truth.start,
        end=campaign.truth.run_change.at,
    )

    data = fig.data
    assert len(data) == 50 and fig.summary["n_hollow"] == 5
    assert (data["hollow"] == (data["key"].map(tier) == "candidate")).all()
    assert fig.panels[0].encodings["hollow"] == "hollow"
    assert fig.panels[0].options["hollow_label"] == "candidate, not an identification"
    assert "tier assigned and candidate" in fig.subtitle
    assert not any("Not drawn" in note for note in fig.notes)
    with pytest.raises(ValueError, match="a tier is one of"):
        van_krevelen(store, tiers=["identified"])
    _drawn(fig)


def test_the_hollow_legend_names_the_tiers_drawn_hollow(
    short_campaign,  # noqa: F811
    tmp_path,
):
    """A tier weaker than a candidate is not called one: the legend of the
    hollow points names the tiers they are."""
    tables = dict(short_campaign.tables)
    peaks = tables["peak_series"].copy()
    keys = peaks["series_key"].astype(str)
    tier = peaks["tier"].astype(object)
    assert set(tier.dropna()) == {"assigned"}
    candidate, weaker = _formula_keys(set(keys))[:2]
    tier[(keys == candidate) & tier.notna()] = "candidate"
    tier[(keys == weaker) & tier.notna()] = "below_assignability"
    peaks["tier"] = pd.Categorical(tier, categories=views.TIERS)
    tables["peak_series"] = peaks
    store = _sync(dataclasses.replace(short_campaign, tables=tables), tmp_path)

    def label(tiers):
        fig = van_krevelen(store, tiers=tiers)
        return fig, fig.panels[0].options["hollow_label"]

    fig, text = label(["assigned", "below_assignability"])
    assert set(fig.data.loc[fig.data["hollow"], "key"]) == {weaker}
    assert text == "below assignability, not an identification"
    fig, text = label(["assigned", "candidate", "below_assignability"])
    assert set(fig.data.loc[fig.data["hollow"], "key"]) == {candidate, weaker}
    assert text == "candidate or below assignability, not an identification"
    _, text = label(["candidate"])
    assert text == "candidate, not an identification"
    # No point drawn hollow, no hollow entry in the legend
    fig, text = label(["assigned", "unassigned"])
    assert not fig.data["hollow"].any() and text == ""
    _drawn(fig)


def test_the_weighted_and_the_bulk_means_are_each_named(month):
    _, store = month

    fig = van_krevelen(store)

    data = fig.data
    w = data["median"]
    summary = fig.summary
    assert summary["oc_weighted"] == pytest.approx((w * data["o_c"]).sum() / w.sum())
    assert summary["hc_weighted"] == pytest.approx((w * data["h_c"]).sum() / w.sum())
    assert summary["oc_bulk"] == pytest.approx(
        (w * data["n_o"]).sum() / (w * data["n_c"]).sum()
    )
    assert summary["hc_bulk"] == pytest.approx(
        (w * data["n_h"]).sum() / (w * data["n_c"]).sum()
    )
    assert summary["oc_bulk"] != pytest.approx(summary["oc_weighted"])
    largest = data.nlargest(10, "median")
    assert [e["species"]["key"] for e in summary["top"]] == list(largest["key"])
    assert fig.panels[0].options["top_n"] == 10


def test_formulas_sharing_a_point_are_counted(month):
    campaign, store = month

    fig = van_krevelen(store, tiers=["assigned", "candidate"])

    data = fig.data
    places = data.groupby([data["o_c"].round(9), data["h_c"].round(9)])["key"]
    shared = [sorted(keys) for _, keys in places if len(keys) > 1]
    listed = [
        sorted(s["key"] for s in point["species"])
        for point in fig.summary["shared_points"]
    ]
    assert shared and sorted(listed) == sorted(shared)
    assert fig.summary["n_shared_points"] == len(shared)
    assert any("more than one formula" in note for note in fig.notes)


# The colours and sizes are chemistry-blind: one campaign holds them
@pytest.mark.parametrize("campaign", ["halide_negative"], indirect=True)
def test_a_van_krevelen_sizes_by_the_median_signal_and_colours_as_asked(month):
    campaign, store = month
    signal = _signal(campaign)

    by_carbon = van_krevelen(store)
    by_class = van_krevelen(store, colour="class")
    by_amplitude = van_krevelen(store, colour="amplitude")
    normalised = van_krevelen(store, colour="amplitude", normalise="tic")

    panel = by_carbon.panels[0]
    assert panel.primitive == "scatter"
    assert panel.encodings["size"] == "median" and panel.encodings["colour"] == "n_c"
    assert panel.axes.size.label == f"Median {signal}"
    assert panel.axes.colour.label == "Carbon number"
    assert any("Signal is not abundance" in note for note in by_carbon.notes)
    assert by_class.panels[0].encodings["colour"] == "group"
    assert by_class.panels[0].options["colours"]["CHON"] == "series.2"
    assert list(by_class.data["group"].cat.categories) == [
        "CHO",
        "CHON",
        "CHOS",
        "CHONS",
        "other",
    ]
    cv = views.amplitude(store, list(by_amplitude.data["key"])).set_index("key")["cv"]
    np.testing.assert_allclose(
        by_amplitude.data["cv"], by_amplitude.data["key"].map(cv)
    )
    assert by_amplitude.panels[0].encodings["colour"] == "cv"
    assert any("includes the instrument's drift" in n for n in by_amplitude.notes)
    assert not any("includes the instrument's drift" in n for n in normalised.notes)
    assert normalised.panels[0].axes.size.label == "Median signal / TIC"
    assert "normalised to TIC" in normalised.subtitle
    assert "window medians" in by_carbon.subtitle
    for fig in (by_class, by_amplitude):
        _drawn(fig)
    with pytest.raises(ValueError, match="colour="):
        van_krevelen(store, colour="o_c")


def test_a_formula_without_carbon_is_left_out_and_counted(
    short_campaign,  # noqa: F811
    tmp_path,
):
    store = _sync(short_campaign, tmp_path)
    species = store.table("species")
    key, chons, halide = _formula_keys(species["series_key"].astype(str))[:3]
    # A batch-peak listing that names an inorganic formula, one of every
    # element the classes count, and one with another element
    for batch in store.batch_ids():
        path = store.path / "species" / f"{batch}.parquet"
        rows = store.table("species", batches=[batch])
        at = rows["series_key"].astype(str) == key
        rows.loc[at, ["n_c", "n_h"]] = 0
        rows.loc[at, ["o_c", "h_c", "osc"]] = np.nan
        rows.loc[at, "class"] = "inorganic"
        rows.loc[rows["series_key"].astype(str) == chons, "class"] = "CHONS"
        rows.loc[rows["series_key"].astype(str) == halide, "class"] = "CHOX"
        store_module._write(path, rows, "species", {"sample_batch_id": batch})

    fig = van_krevelen(store, tiers=["assigned", "candidate"], colour="class")

    assert fig.summary["n_without_carbon"] == 1
    assert key not in set(fig.data["key"])
    assert fig.summary["n_points"] == fig.summary["n_formulas"] - 1
    assert any("1 formula without carbon has no place" in n for n in fig.notes)
    group = fig.data.set_index("key")["group"]
    assert (group.loc[chons], group.loc[halide]) == ("CHONS", "other")
    assert fig.summary["by_class"]["CHONS"] == fig.summary["by_class"]["other"] == 1
    assert sum(fig.summary["by_class"].values()) == fig.summary["n_points"]


# --- every chart ---------------------------------------------------------------------


IDS = ("spectrogram", "period_spectrum", "van_krevelen")


@pytest.mark.parametrize("chart", IDS)
def test_each_chart_is_registered_with_the_parameters_it_takes(chart):
    kind = registry.get(chart)

    assert kind.tier == 1 and kind.id == chart
    signature = inspect.signature(kind.build)
    taken = [p for p in signature.parameters.values() if p.name != "store"]
    assert [p.name for p in kind.params] == [p.name for p in taken]
    for param, given in zip(kind.params, taken):
        default = given.default
        assert param.default == (
            list(default) if isinstance(default, tuple) else default
        )
    json.dumps(kind.to_dict())
    assert {"normalise", "tz", "mixed_runs", "run_changes"} <= set(kind.summary)


@pytest.mark.parametrize("chart", IDS)
def test_each_summary_carries_its_fields_and_the_views_context(month, chart):
    _, store = month
    kind = registry.get(chart)

    fig = kind.build(store, normalise="tic")

    assert set(fig.summary) <= set(kind.summary)
    for field in (*chart_common.FACE_FIELDS, *chart_common.NORMALISER_FIELDS, "stat"):
        assert field in fig.summary, field
    assert fig.summary["tz"] == ZONE and fig.summary["normalise"] == "tic"
    assert fig.summary["normalise_name"] == "TIC"
    json.dumps(fig.summary, allow_nan=False)
    assert fig.id == chart


@pytest.mark.parametrize("chart", IDS)
def test_each_chart_says_where_its_normaliser_was_not_whole(clusters, chart):
    """Over the stretch the reagent's cluster is below detection, the sum
    of the two ions is partial, and by the cluster alone those samples
    have no value at all: each chart's notes say which, and in what share
    of the samples."""
    campaign, store = clusters
    reagents = list(campaign.truth.shares.series_keys)
    lost = campaign.truth.shares.lost
    kind = registry.get(chart)
    window = {"start": lost[0] - DAY / 4, "end": lost[1]}

    summed = kind.build(store, normalise=reagents, **window)
    alone = kind.build(store, normalise=reagents[1], **window)

    share = 1 - summed.summary["normalise_complete"]
    assert 0 < share < 1
    assert (
        f"The denominator was a partial sum in {share:.1%} of the samples: "
        "one of its ions had no value there, and the others divided."
    ) in summed.notes
    missing = 1 - alone.summary["normalise_complete"]
    assert 0 < missing < 1
    assert (
        f"In {missing:.1%} of the samples reagent water cluster had no value, "
        "so those samples have none either."
    ) in alone.notes
    assert not any("partial sum" in note for note in alone.notes)


def _summary_species(fig) -> list[dict]:
    """Every species a composition chart's summary names."""
    summary = fig.summary
    if fig.id == "spectrogram":
        return [row["species"] for row in summary["top_rows"]]
    if fig.id == "period_spectrum":
        return [row["species"] for row in summary["top"]]
    return [row["species"] for row in summary["top"]] + [
        one for point in summary["shared_points"] for one in point["species"]
    ]


@pytest.mark.parametrize("campaign", ["halide_negative"], indirect=True)
@pytest.mark.parametrize("chart", IDS)
def test_a_species_without_a_name_is_named_by_its_formula_then_its_key(
    month, chart, monkeypatch
):
    """A summary names a species as every chart does: by its name, else its
    formula, else its key. A store's row may hold a blank name, as one
    synced before blank names were dropped does."""
    campaign, store = month
    kind = registry.get(chart)
    # A day of it is enough, and cheaper to build three times
    day = {"start": campaign.truth.start + DAY, "end": campaign.truth.start + 2 * DAY}
    named = _summary_species(kind.build(store, **day))
    assert named and all(one["name"] == one["formula"] for one in named)
    table = store.table

    def planted(**columns):
        def read(which, **kw):
            rows = table(which, **kw)
            if which == "species":
                formula = rows["series_key"].astype(str).str.startswith("formula:")
                rows = rows.copy()
                for column, value in columns.items():
                    rows.loc[formula, column] = value
            return rows

        monkeypatch.setattr(store, "table", read)

    planted(name=" ")
    unnamed = _summary_species(kind.build(store, **day))
    assert [one["name"] for one in unnamed] == [one["formula"] for one in named]
    planted(name=None, formula=None)
    keyed = _summary_species(kind.build(store, **day))
    assert [(one["name"], one["formula"]) for one in keyed] == [
        (one["key"], None) for one in named
    ]


#: The window before the campaign, which holds no sample.
BEFORE = (
    pd.Timestamp("2026-05-01T00:00", tz=ZONE),
    pd.Timestamp("2026-05-02T00:00", tz=ZONE),
)


def _empty(fig: Figure) -> list[str]:
    """The notes of *fig* that say it has nothing, or nothing for a
    window, to draw."""
    return [
        n for n in fig.notes if n.startswith(("Nothing to draw", "No assigned peak"))
    ]


def _builds(chart: str, **kwargs) -> list:
    """The ways to build *chart*: the period spectrum alone and against
    another period, the others as they are."""
    kind = registry.get(chart)
    if chart != "period_spectrum":
        return [lambda store: kind.build(store, **kwargs)]
    return [
        lambda store: kind.build(store, **kwargs),
        lambda store: kind.build(
            store, other_start=BEFORE[0] - DAY, other_end=BEFORE[1] - DAY, **kwargs
        ),
    ]


@pytest.mark.parametrize("chart", IDS)
def test_each_chart_of_a_store_without_a_ledger_draws_empty_and_says_why(
    chart,
    short_campaign,  # noqa: F811
    tmp_path,
):
    """The demo bundle ships no batch ledger: the store holds no assigned
    peak, and one note says that no assignment pass has run, whatever the
    window."""
    client = FakeClient(short_campaign)
    client.no_ledger = True
    store = _sync(client, tmp_path)

    for build in _builds(chart):
        fig = build(store)

        assert fig.data.empty
        assert _empty(fig) == [chart_common.NO_LEDGER]
        assert fig.notes[0] == chart_common.NO_LEDGER
        _drawn(fig)


@pytest.mark.parametrize("chart", IDS)
def test_each_chart_of_a_store_whose_runs_left_no_assigned_peak_says_so(
    chart,
    short_campaign,  # noqa: F811
    tmp_path,
):
    client = FakeClient(short_campaign)
    for batch, points in client.points.items():
        client.points[batch] = points.iloc[:0]
    store = _sync(client, tmp_path)
    assert store.status().batches["reading"].notna().all()

    for build in _builds(chart):
        fig = build(store)

        assert fig.data.empty
        assert _empty(fig) == [chart_common.NO_ASSIGNED_PEAK]
        _drawn(fig)


@pytest.mark.parametrize("chart", IDS)
def test_each_chart_of_a_window_without_an_assigned_peak_names_the_window(month, chart):
    """A window that holds no sample: the note names it, and says the store
    holds assigned peaks outside it. A window with some says nothing of
    the kind."""
    campaign, store = month
    kind = registry.get(chart)
    episode = campaign.truth.episodes[0]

    fig = kind.build(store, start=BEFORE[0], end=BEFORE[1])

    assert fig.data.empty or chart == "spectrogram"
    assert _empty(fig) == [
        "No assigned peak has a value in 2026-05-01 00:00 to 2026-05-02 00:00: "
        "the store holds some outside it."
    ]
    assert fig.notes[0] == _empty(fig)[0]
    _drawn(fig)
    assert not _empty(kind.build(store, start=episode.start, end=episode.end))


def test_a_period_spectrum_names_the_one_period_without_an_assigned_peak(month):
    """Against a window with no sample, the mirror draws the period alone
    and a note names the other; either way round."""
    campaign, store = month
    episode = campaign.truth.episodes[0]
    names = {
        "episode": chart_common.window_name([episode.start, episode.end], ZONE),
        "before": "2026-05-01 00:00 to 2026-05-02 00:00",
    }

    fig = period_spectrum(
        store,
        start=episode.start,
        end=episode.end,
        other_start=BEFORE[0],
        other_end=BEFORE[1],
    )
    swapped = period_spectrum(
        store,
        start=BEFORE[0],
        end=BEFORE[1],
        other_start=episode.start,
        other_end=episode.end,
    )

    for drawn, side in ((fig, "period"), (swapped, "other")):
        mirror = drawn.data[drawn.data["part"] == "mirror"]
        assert set(mirror["side"]) == {side}
        assert _empty(drawn) == [
            f"No assigned peak has a value in {names['before']}: the store holds "
            "some outside it."
        ]
        assert names["episode"] not in _empty(drawn)[0]
        _drawn(drawn)


def test_unnamed_reagent_ions_are_named_by_their_formulas(tmp_path):
    """The server names a target ion without a compound name ``""``: the
    subtitle and the signal axis name the reagent ions by their formulas,
    not by a blank."""
    campaign = generate(SHORT_CLUSTERS, seed=2)
    reagents = list(campaign.truth.shares.series_keys)
    client = FakeClient(campaign)
    for batch, ions in client.ions.items():
        client.ions[batch] = ions.assign(target_compound_name="")
    store = _sync(client, tmp_path)

    figures = {
        "spectrogram": spectrogram(store, normalise=reagents),
        "period_spectrum": period_spectrum(store, normalise=reagents),
        "van_krevelen": van_krevelen(store, normalise=reagents),
    }

    labels = {
        "spectrogram": (
            figures["spectrogram"].panels[0].axes.colour.label,
            "signal / I + H2IO, bin mean",
        ),
        "period_spectrum": (
            figures["period_spectrum"].panels[0].axes.y.label,
            "Mean signal / I + H2IO",
        ),
        "van_krevelen": (
            figures["van_krevelen"].panels[0].axes.size.label,
            "Median signal / I + H2IO",
        ),
    }
    for chart, fig in figures.items():
        assert "normalised to I + H2IO" in fig.subtitle, chart
        assert labels[chart][0] == labels[chart][1], chart
        assert fig.summary["normalise_name"] == "I + H2IO"
        assert fig.summary["normalise_members"] == reagents


def test_a_run_change_of_both_polarities_draws_one_rule(
    short_campaign,  # noqa: F811
    tmp_path,
):
    """A ``+-`` batch whose engine run changes gives a change per polarity
    at one moment: the spectrogram draws one rule, each polarity's change
    has its note at that moment, and the summary keeps both."""
    client = FakeClient(short_campaign)
    first, middle, last = client.batch_ids
    client.runs[first] = f"run-1:{first}"
    client.runs[middle] = f"run-2:{middle}"
    client.runs[last] = f"run-2:{last}"
    for batch in client.batch_ids:
        client.batch_polarity[batch] = "+-"
    store = _sync(client, tmp_path)
    changes = store.status().run_changes
    assert sorted(c.polarity for c in changes) == ["+", "-"]
    assert len({c.at for c in changes}) == 1
    at = chart_common.wall(changes[0].at, ZONE)

    for fig in (spectrogram(store), period_spectrum(store), van_krevelen(store)):
        assert len(fig.summary["run_changes"]) == 2
        notes = [n for n in fig.notes if n.startswith("The engine run that reads")]
        assert sorted(n.split("(")[1].split(" ")[0] for n in notes) == [
            "negative",
            "positive",
        ]
        assert all(f"changes at {at} (" in n for n in notes)
    fig = spectrogram(store)
    afters = {change["after"] for change in fig.summary["run_changes"]}
    assert len(afters) == 1
    rules = fig.panels[0].options["rules"]
    assert [rule["label"] for rule in rules] == [f"engine run: {afters.pop()}"]


class _Samples:
    """A store whose samples are of the instrument types given."""

    def __init__(self, *types):
        self.types = types

    def table(self, name):
        assert name == "samples"
        return pd.DataFrame({"instrument_type": list(self.types)})


@pytest.mark.parametrize(
    "types, variable",
    [
        (("TOF", "tof"), "peak area"),
        (("Orbitrap", "orbi"), "peak height"),
        (("tof", "orbi"), "peak area or peak height"),
        ((None,), None),
    ],
)
def test_the_signal_is_named_by_the_instrument_type(types, variable):
    store = _Samples(*types)

    assert chart_common.intensity_variable(store) == variable
    assert chart_common.value_label(store, {"normalise": "none"}, "Mean") == (
        f"Mean {variable or 'signal'}"
    )
    raw = chart_common.value_label(store, {"normalise": "none"})
    assert raw == (variable or "signal").capitalize()
    divided = {"normalise": ["ion:a", "ion:b"], "normalise_name": "I- + IH2O-"}
    assert chart_common.value_label(store, divided) == "signal / I- + IH2O-"
    assert chart_common.value_label(store, divided, "Median") == (
        "Median signal / I- + IH2O-"
    )


@pytest.mark.parametrize(
    "call, message",
    [
        # The chart's own check, before the spec's ("...: option top_n=-1")
        (lambda s: period_spectrum(s, top_n=-1), "^top_n=-1 is negative"),
        (lambda s: period_spectrum(s, top_n=2.5), "^top_n=2.5 is no count"),
        (lambda s: van_krevelen(s, top_n=-1), "^top_n=-1 is negative"),
        (lambda s: van_krevelen(s, top_n=2.5), "^top_n=2.5 is no count"),
        (lambda s: van_krevelen(s, tiers=[]), "tiers is empty"),
        (lambda s: spectrogram(s, mz_width=-1), "mz_width"),
        (lambda s: spectrogram(s, top_n=-1), "^top_n=-1 is negative"),
        (lambda s: spectrogram(s, top_n=2.5), "^top_n=2.5 is no count"),
        (lambda s: spectrogram(s, top_n=True), "^top_n=True is no count"),
    ],
)
def test_a_wrong_argument_to_a_chart_says_what_is_wrong(month, call, message):
    _, store = month

    with pytest.raises(ValueError, match=message):
        call(store)


def test_a_count_a_chart_takes_is_a_plain_int():
    """A NumPy integer is a count too, and comes back a plain int, as a
    JSON summary takes it."""
    count = chart_common.check_count("top_n", np.int64(3))

    assert count == 3 and type(count) is int


def test_a_colour_scale_of_one_value_fits_its_data():
    from mascope_viz.charts.spectrogram import _limits

    assert _limits(np.array([5.0, 5.0, np.nan])) is None
    assert _limits(np.array([0.0, -1.0])) is None
    assert _limits(np.arange(1.0, 102.0)) == pytest.approx((2.0, 100.0))
