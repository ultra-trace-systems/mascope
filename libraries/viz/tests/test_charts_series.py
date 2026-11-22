"""
The species charts, on the synthetic campaign: ``species_series``,
``diurnal`` and ``period_boxes``, and the ``species`` parameter they share.

The assertions are against the answers the campaign was built from
(:class:`fixtures.campaign.Truth`) and against the views the charts draw
from, not against what the charts gave last time. ``month`` (each
chemistry's month, synced once per session) carries most of them;
``clusters`` is the halide month with its reagent over two ions, and
``year`` a year of one sample a minute for the point budget.
"""

import dataclasses
import json
import warnings

import numpy as np
import pandas as pd
import pytest
from fixtures.campaign import POSITIVE, Campaign, generate
from fixtures.server import SCOPE, FakeClient

from mascope_viz import render
from mascope_viz.charts import Figure, common, registry
from mascope_viz.charts import diurnal as diurnal_module
from mascope_viz.charts import series as series_chart
from mascope_viz.charts.diurnal import diurnal, hours
from mascope_viz.charts.periods import THIN, period_boxes
from mascope_viz.charts.series import species_series
from mascope_viz.charts.species import resolve
from mascope_viz.data import store as store_module
from mascope_viz.data import views
from mascope_viz.data.store import CampaignStore, sync
from mascope_viz.errors import InvalidChart, UnknownSpecies


ZONE = "Europe/Helsinki"

KINDS = ("species_series", "diurnal", "period_boxes")


@pytest.fixture(scope="module")
def month(synced) -> tuple[Campaign, CampaignStore]:
    campaign, _, report = synced
    return campaign, report.store


def _sync(campaign: Campaign, root) -> CampaignStore:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_module, "_now", lambda: pd.Timestamp("2027-08-01T00:00Z"))
        patch.setattr(store_module.time, "sleep", lambda seconds: None)
        return sync(FakeClient(campaign), SCOPE, root=root).store


@pytest.fixture(scope="module")
def clusters(cluster_campaign, tmp_path_factory) -> tuple[Campaign, CampaignStore]:
    return cluster_campaign, _sync(cluster_campaign, tmp_path_factory.mktemp("c"))


#: A year of one sample a minute, six species of which one is a target ion:
#: the positive chemistry, whose signal does not follow a falling reagent.
YEAR = dataclasses.replace(
    POSITIVE,
    days=365,
    n_species=6,
    n_targets=1,
    episodes=(),
    n_renamed=0,
    gap_day=200,
    run_change_day=180,
)


@pytest.fixture(scope="module")
def year(tmp_path_factory) -> tuple[Campaign, CampaignStore]:
    campaign = generate(YEAR, seed=0)
    return campaign, _sync(campaign, tmp_path_factory.mktemp("year"))


def _cycles(campaign: Campaign, kind: str, prefix: str = "ion:") -> list[str]:
    return sorted(
        key
        for key, cycle in campaign.truth.cycles.items()
        if cycle.kind == kind and key.startswith(prefix)
    )


def _species(store: CampaignStore) -> pd.DataFrame:
    species = store.table("species")
    return species.assign(series_key=species["series_key"].astype(str))


def _formula_only(campaign: Campaign) -> str:
    """A formula key no target ion holds."""
    held = _target_formulas(campaign)
    return next(
        key
        for key in sorted(campaign.truth.cycles)
        if key.startswith("formula:") and key not in held
    )


def _target_formulas(campaign: Campaign) -> set[str]:
    """The formula keys of the target ions' own formulas."""
    species = campaign.tables["species"]
    ions = species[species["series_key"].str.startswith("ion:")]
    held = {(f, m) for f, m in zip(ions["formula"], ions["mechanism"])}
    formulas = species[species["series_key"].str.startswith("formula:")]
    return {
        key
        for key, f, m in zip(
            formulas["series_key"], formulas["formula"], formulas["mechanism"]
        )
        if (f, m) in held
    }


def _seldom(campaign: Campaign) -> str:
    """The target ion detected least often: a night species below detection
    through much of the day."""
    ions = campaign.tables["ion_series"]
    missing = ions["intensity"].isna().groupby(ions["series_key"].astype(str)).mean()
    return str(missing.idxmax())


def _rows(fig: Figure, key: str) -> pd.DataFrame:
    return fig.data[fig.data["key"] == key].reset_index(drop=True)


def _entry(fig: Figure, key: str) -> dict:
    return next(e for e in fig.summary["species"] if e["key"] == key)


# --- the species parameter --------------------------------------------------------


class _Species:
    """A store holding nothing but a ``species`` table."""

    def __init__(self, rows: list[dict]):
        self.frame = pd.DataFrame(
            rows, columns=["series_key", "name", "formula", "ion_formula", "mechanism"]
        )

    def table(self, name: str, **_) -> pd.DataFrame:
        assert name == "species"
        return self.frame


#: One compound as a target ion and as an assigned formula under two
#: mechanisms, a formula only the engine assigns, and a target ion whose
#: formula the engine reads under a mechanism that sorts before the ion's.
SPECIES = _Species(
    [
        {
            "series_key": "ion:t1",
            "name": "pinic acid",
            "formula": "C9H14O4",
            "ion_formula": "C9H14O4I-",
            "mechanism": "[M+I]-",
        },
        {
            "series_key": "formula:C9H14O4|[M+I]-",
            "name": "C9H14O4",
            "formula": "C9H14O4",
            "ion_formula": "C9H14O4I-",
            "mechanism": "[M+I]-",
        },
        {
            "series_key": "formula:C9H14O4|[M-H]-",
            "name": "C9H14O4",
            "formula": "C9H14O4",
            "ion_formula": "C9H13O4-",
            "mechanism": "[M-H]-",
        },
        {
            "series_key": "formula:C5H8O3|[M+I]-",
            "name": "C5H8O3",
            "formula": "C5H8O3",
            "ion_formula": "C5H8O3I-",
            "mechanism": "[M+I]-",
        },
        {
            "series_key": "ion:t2",
            "name": "levoglucosan",
            "formula": "C6H10O5",
            "ion_formula": "C6H10O5I-",
            "mechanism": "[M+I]-",
        },
        {
            "series_key": "formula:C6H10O5|[M+H]+",
            "name": "C6H10O5",
            "formula": "C6H10O5",
            "ion_formula": "C6H11O5+",
            "mechanism": "[M+H]+",
        },
    ]
)


def test_a_species_is_a_key_a_name_or_a_formula():
    def keys(species) -> list[str]:
        return resolve(SPECIES, species).keys

    assert keys("formula:C5H8O3|[M+I]-") == ["formula:C5H8O3|[M+I]-"]
    assert keys("C5H8O3") == ["formula:C5H8O3|[M+I]-"]
    assert keys("C5H8O3I-") == ["formula:C5H8O3|[M+I]-"]
    assert keys("pinic acid") == ["ion:t1"]
    # A neutral formula the ion holds by formula alone, and the engine
    # under another mechanism: the ion first, whatever its mechanism
    assert keys("C6H10O5") == ["ion:t2", "formula:C6H10O5|[M+H]+"]
    assert keys("levoglucosan") == ["ion:t2"]
    assert keys("tic") == ["tic"]
    # A key is taken as it is, even one a target ion would hold
    assert keys("formula:C9H14O4|[M+I]-") == ["formula:C9H14O4|[M+I]-"]
    # A list keeps its order and names each species once
    assert keys(["C5H8O3", "pinic acid", "formula:C5H8O3|[M+I]-"]) == [
        "formula:C5H8O3|[M+I]-",
        "ion:t1",
    ]


def test_a_species_without_a_name_is_named_by_its_formula_or_key():
    bare = _Species(
        [
            {"series_key": "formula:C2H4|[M+H]+", "name": None, "formula": "C2H4"},
            {"series_key": "ion:x", "name": None, "formula": None},
        ]
    )

    found = resolve(bare, ["formula:C2H4|[M+H]+", "ion:x", "tic"])

    assert [s.name for s in found.species] == ["C2H4", "ion:x", "TIC"]
    assert [s.to_dict() for s in found.species][2] == {
        "key": "tic",
        "name": "TIC",
        "formula": None,
    }


def test_top_n_with_nothing_but_the_normaliser_to_rank_is_refused():
    with pytest.raises(InvalidChart, match="no species to rank"):
        # The ion is the normaliser, and its formula's key is the ion
        resolve(
            _Species(SPECIES.frame.to_dict("records")[:2]),
            "top:3",
            normalise="ion:t1",
        )


def test_a_formula_a_target_ion_holds_is_the_ion_and_one_per_mechanism():
    """The ion's key survives a run change; the formula under another
    mechanism is a series of its own, the mechanism in its name."""
    found = resolve(SPECIES, "C9H14O4")

    assert found.keys == ["ion:t1", "formula:C9H14O4|[M-H]-"]
    assert [s.name for s in found.species] == [
        "pinic acid [M+I]-",
        "C9H14O4 [M-H]-",
    ]
    # The ion formula names the one mechanism, the target ion's
    assert resolve(SPECIES, "C9H14O4I-").keys == ["ion:t1"]
    assert resolve(SPECIES, "C9H13O4-").keys == ["formula:C9H14O4|[M-H]-"]
    # Alone, a species needs no mechanism to tell it from another
    assert [s.name for s in resolve(SPECIES, "pinic acid").species] == ["pinic acid"]


def test_a_species_is_named_by_its_key_when_names_and_mechanisms_tie():
    found = resolve(SPECIES, ["ion:t1", "formula:C9H14O4|[M+I]-"])

    # One mechanism, two names: the names tell them apart
    assert [s.name for s in found.species] == ["pinic acid", "C9H14O4"]
    same = _Species(
        [
            {"series_key": "ion:a", "name": "x", "formula": None, "mechanism": None},
            {"series_key": "ion:b", "name": "x", "formula": None, "mechanism": None},
        ]
    )
    assert [s.name for s in resolve(same, ["ion:a", "ion:b"]).species] == [
        "x (ion:a)",
        "x (ion:b)",
    ]


def test_a_species_the_store_does_not_hold_names_the_nearest():
    with pytest.raises(UnknownSpecies) as caught:
        resolve(SPECIES, "c5h8o3")
    assert caught.value.near[0] == "C5H8O3"
    assert caught.value.to_dict()["error"] == "unknown_species"
    assert "case counts" in str(caught.value)

    with pytest.raises(UnknownSpecies) as caught:
        resolve(SPECIES, "pinic acd")
    assert "pinic acid" in caught.value.near
    with pytest.raises(UnknownSpecies) as caught:
        resolve(SPECIES, "ion:t3")
    assert "ion:t1" in caught.value.near


@pytest.mark.parametrize("given", [[], "", ["C5H8O3", 3], 3, "top:0"])
def test_a_species_parameter_that_names_nothing_is_refused(given):
    with pytest.raises(InvalidChart):
        resolve(SPECIES, given)


def test_top_n_ranks_the_species_once_each_under_the_charts_normaliser(month):
    campaign, store = month
    held = _target_formulas(campaign)
    normalise = campaign.truth.drift.series_key if campaign.truth.drift else "tic"

    found = resolve(store, "top:6", normalise=normalise)

    # The ranking of views.top, over every species but the formulas the
    # target ions hold and the normaliser itself
    keys = sorted(set(_species(store)["series_key"]) - held - {normalise})
    want = views.top(store, 6, "median", keys=keys, normalise=normalise)
    assert found.keys == list(want["key"])
    assert not set(found.keys) & held
    assert normalise not in found.keys
    assert found.given == "top:6"
    # Ranked over the chart's own window
    start, end = "2026-06-07 12:00", "2026-06-08 06:00"
    episode = resolve(store, "top:6", normalise=normalise, start=start, end=end)
    want = views.top(
        store, 6, "median", keys=keys, normalise=normalise, start=start, end=end
    )
    assert episode.keys == list(want["key"])
    assert episode.keys != found.keys
    # And a list may hold it beside named species, each once
    mixed = resolve(store, [found.keys[1], "top:2"], normalise=normalise)
    assert mixed.keys == [found.keys[1], found.keys[0]]
    assert mixed.given == [found.keys[1], "top:2"]


def test_top_n_ranks_over_the_charts_zone_and_window(month, monkeypatch):
    campaign, store = month
    asked = []
    real = views.top

    def top(*args, **kwargs):
        asked.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(views, "top", top)
    read = {"normalise": "tic", "tz": "UTC", "start": "2026-06-02", "end": "2026-06-09"}

    resolve(store, "top:2", **read)

    (kwargs,) = asked
    assert {name: kwargs[name] for name in read} == read
    with pytest.raises(InvalidChart, match="no species has a value"):
        resolve(store, "top:2", start="2026-06-21 06:00", end="2026-06-21 12:00")


# --- species_series ---------------------------------------------------------------


def test_species_series_at_auto_cadence_leaves_no_bin_empty_but_the_gap(month):
    campaign, store = month
    gap = campaign.truth.gap
    key = _cycles(campaign, "day")[0]

    fig = species_series(store, key)

    assert fig.context.cadence == "1min"
    assert fig.summary["auto"] is True and fig.summary["coarsened_from"] is None
    rows = _rows(fig, key)
    in_gap = ((rows["t"] >= gap.start) & (rows["t"] < gap.end)).to_numpy()
    sampled = rows["n_samples"].to_numpy() > 0
    assert sampled[~in_gap].all()
    assert not sampled[in_gap].any()
    assert rows.loc[in_gap, "value"].isna().all()
    # A bin of one sample has no band to draw
    assert "lo" not in fig.panels[0].encodings
    assert "p10-p90" not in fig.subtitle

    # A break across the gap on both backends
    drawn = render.matplotlib(fig)
    lines = [ln for ln in drawn.axes[0].get_lines() if len(ln.get_xdata()) > 2]
    assert len(lines) == 1
    pieces = [
        code for _, code in lines[0].get_path().iter_segments(remove_nans=True)
    ].count(1)
    assert pieces == 2
    plotted = render.plotly(fig)
    trace = next(t for t in plotted.data if t.mode == "lines")
    assert not trace.connectgaps
    assert sum(v is None for v in trace.y) == int(in_gap.sum())


def test_species_series_reports_the_episodes_as_the_maxima(month):
    """Each episode species' hourly maximum falls inside its episode, at
    no normalisation."""
    campaign, store = month
    for episode in campaign.truth.episodes:
        keys = list(episode.series_keys)

        fig = species_series(store, keys, cadence="1h")

        assert fig.context.cadence == "1h"
        for key in keys:
            entry = _entry(fig, key)
            at = pd.Timestamp(entry["max_time"])
            assert episode.start <= at < episode.end, key
            assert entry["max"] == _rows(fig, key)["value"].max()
            assert entry["max_time"].endswith("+03:00")


def test_species_series_summary_is_of_the_plotted_bins(month):
    campaign, store = month
    gap = campaign.truth.gap
    sparse = _seldom(campaign)
    flat = _cycles(campaign, "flat")[0]

    fig = species_series(store, [flat, sparse], cadence="1h")

    assert fig.summary["auto"] is False
    assert fig.summary["stat"] == "median" and fig.summary["band"] == [10, 90]
    for key in (flat, sparse):
        rows = _rows(fig, key)
        entry = _entry(fig, key)
        values = rows["value"].dropna()
        assert entry["median"] == pytest.approx(values.median())
        sampled = rows["n_samples"] > 0
        assert entry["fraction_present"] == pytest.approx(
            (rows["value"].notna() & sampled).sum() / sampled.sum()
        )
        assert entry["presence"] == pytest.approx(
            rows["n"].sum() / rows["n_samples"].sum()
        )
        assert entry["n_bins"] == int(sampled.sum())
        assert entry["n_present"] == int((rows["value"].notna() & sampled).sum())
        # The gap's bins have no samples, so they do not count against it
        gap_bins = (rows["t"] >= gap.start) & (rows["t"] < gap.end)
        assert gap_bins.sum() >= 24 and not sampled[gap_bins].any()
    assert _entry(fig, flat)["fraction_present"] == 1.0
    assert _entry(fig, flat)["presence"] == 1.0
    assert _entry(fig, sparse)["presence"] < 0.95
    # A species seldom detected is named as reading high
    for key in (flat, sparse):
        entry = _entry(fig, key)
        named = any(entry["name"] in note and "read high" in note for note in fig.notes)
        assert named == (entry["presence"] < common.SELDOM), key


def test_a_species_no_batch_of_the_window_reads_has_no_presence(month):
    """A formula the second run reads, in a week before the run changed:
    drawn empty beside a species that has values, its numbers null."""
    campaign, store = month
    _, new = campaign.truth.run_change.readings[0]
    ion = _cycles(campaign, "day")[0]

    fig = species_series(store, [ion, new], cadence="1h", end="2026-06-09")

    entry = _entry(fig, new)
    assert entry["presence"] is None and entry["fraction_present"] is None
    assert entry["median"] is None and entry["max_time"] is None
    assert entry["n_bins"] == 0
    assert _entry(fig, ion)["presence"] == 1.0
    assert not any("read high" in note for note in fig.notes)
    # Its empty panel draws, on both backends
    assert len(render.matplotlib(fig).axes) >= 2
    render.plotly(fig)


@pytest.mark.parametrize("kind", ["diurnal", "period_boxes"])
def test_a_species_without_a_value_has_null_numbers_in_any_chart(month, kind):
    campaign, store = month
    _, new = campaign.truth.run_change.readings[0]
    ion = _cycles(campaign, "day")[0]

    fig = registry.get(kind).build(store, species=[ion, new], end="2026-06-09")

    entry = _entry(fig, new)
    assert entry["presence"] is None
    if kind == "diurnal":
        assert entry["hour_max"] is None and entry["day_night_ratio"] is None
    else:
        assert entry["highest_median"] is None
        assert all(p["median"] is None for p in entry["periods"])
    render.matplotlib(fig)
    render.plotly(fig)


def test_a_run_change_is_marked_on_a_formulas_panel_and_not_an_ions(month):
    campaign, store = month
    change = campaign.truth.run_change
    formula = _formula_only(campaign)
    ion = _cycles(campaign, "day")[0]

    fig = species_series(store, [formula, ion], cadence="1h")

    by_title = {panel.title: panel for panel in fig.panels}
    rules = by_title[_entry(fig, formula)["name"]].options["rules"]
    assert [r["at"] for r in rules] == [change.at]
    # The rule names the reading after the change, as the other charts' do
    assert rules[0]["label"] == f"engine run: {fig.summary['run_changes'][0]['after']}"
    assert by_title[_entry(fig, ion)["name"]].options["rules"] == []
    assert fig.summary["mixed_runs"]
    assert [c["at"] for c in fig.summary["run_changes"]] == [
        change.at.tz_convert(ZONE).isoformat()
    ]
    (note,) = [note for note in fig.notes if "two readings" in note]
    assert f"changes at 2026-06-16 03:00 ({ZONE})" in note
    # On an ion alone, nothing changes
    alone = species_series(store, ion, cadence="1h")
    assert not alone.summary["mixed_runs"]
    assert alone.summary["run_changes"] == []
    assert alone.panels[0].options["rules"] == []
    assert not any("two readings" in note for note in alone.notes)


def test_a_run_change_moves_every_panel_divided_by_a_formula(month):
    campaign, store = month
    formula = _formula_only(campaign)
    ion = _cycles(campaign, "day")[0]

    fig = species_series(store, ion, cadence="1h", normalise=formula)

    assert fig.summary["mixed_runs"]
    assert [r["at"] for r in fig.panels[0].options["rules"]] == [
        campaign.truth.run_change.at
    ]


def test_species_series_panels_or_overlay(month):
    campaign, store = month
    keys = _cycles(campaign, "day")[:3]

    panels = species_series(store, keys, cadence="1h")
    overlay = species_series(store, keys, cadence="1h", layout="overlay")

    assert [p.where["key"] for p in panels.panels] == [[k] for k in keys]
    assert [p.title for p in panels.panels] == [_entry(panels, k)["name"] for k in keys]
    assert panels.share_x
    assert panels.columns == 1 and panels.summary["layout"] == "panels"
    # With no window, the summary spans the first sample to the last
    times = campaign.tables["samples"]["t_mid_utc"]
    assert panels.summary["start"] == common.at(times.min(), ZONE)
    assert panels.summary["end"] == common.at(times.max(), ZONE)
    assert (
        species_series(
            store, _cycles(campaign, "night", "formula:")[:5], cadence="1d"
        ).columns
        == 2
    )
    assert overlay.summary["layout"] == "overlay"
    assert len(overlay.panels) == 1
    assert overlay.panels[0].where["key"] == keys
    assert overlay.panels[0].encodings["group"] == "key"
    assert overlay.panels[0].encodings["name"] == "name"
    # The band at a cadence that bins several samples
    assert overlay.panels[0].encodings["lo"] == "lo"
    assert "1h medians, p10-p90 band" in overlay.subtitle
    assert overlay.summary["band"] == [10, 90]
    assert overlay.panels[0].axes.y.scale == "log"
    assert species_series(store, keys, y="linear").panels[0].axes.y.scale == "linear"


def test_species_series_refuses_more_species_than_a_layout_holds(month):
    campaign, store = month
    keys = sorted(campaign.truth.cycles)

    with pytest.raises(InvalidChart, match="overlay"):
        species_series(store, keys[:9], layout="overlay", cadence="1d")
    assert (
        len(
            species_series(store, keys[:8], layout="overlay", cadence="1d")
            .panels[0]
            .where["key"]
        )
        == 8
    )
    with pytest.raises(InvalidChart, match="panels"):
        species_series(store, keys[:25], cadence="1d")
    assert len(species_series(store, keys[:24], cadence="1d").panels) == 24


@pytest.mark.parametrize(
    "kwargs",
    [
        {"layout": "stacked"},
        {"cadence": "5min"},
        {"y": "symlog"},
        {"budget": 0},
        {"budget": 1.5},
        {"events": [{"start": "2026-06-07"}]},
        {"events": {"start": "2026-06-07", "end": "2026-06-08"}},
        {"events": [{"start": "2026-06-07", "end": "2026-06-08", "kind": "gap"}]},
    ],
)
def test_species_series_refuses_what_it_cannot_draw(month, kwargs):
    campaign, store = month

    with pytest.raises(InvalidChart):
        species_series(store, _cycles(campaign, "day")[0], **kwargs)


def test_species_series_coarsens_past_its_budget_and_says_so(month):
    campaign, store = month
    keys = _cycles(campaign, "day")[:2]

    # Two species at every sample are some 86,000 marks
    fig = species_series(store, keys, cadence="raw")
    assert fig.summary["coarsened_from"] == "raw"
    assert fig.context.cadence == "10min"
    assert "coarsened from raw to 10min to keep within 50,000 marks" in fig.subtitle
    assert fig.summary["marks"] == len(fig.data) <= series_chart.BUDGET
    # Within its budget, it draws every sample, with no band
    one = species_series(store, keys[0], cadence="raw", budget=60_000)
    assert one.summary["coarsened_from"] is None
    assert one.context.cadence == "raw"
    assert one.summary["band"] is None and "lo" not in one.panels[0].encodings
    assert "every sample" in one.subtitle
    # Past a day's bins it refuses, rather than drop points
    with pytest.raises(InvalidChart, match="budget"):
        species_series(store, keys, cadence="1h", budget=40)
    daily = species_series(store, keys, cadence="1h", budget=1_000)
    assert daily.context.cadence == "1D"
    assert daily.summary["coarsened_from"] == "1h"
    # A budget of exactly the marks holds them, and one mark fewer does not
    marks = species_series(store, keys, cadence="1h").summary["marks"]
    exact = species_series(store, keys, cadence="1h", budget=marks)
    assert exact.context.cadence == "1h" and exact.summary["coarsened_from"] is None
    short = species_series(store, keys, cadence="1h", budget=marks - 1)
    assert short.summary["coarsened_from"] == "1h"
    # Coarsened twice, it says the cadence asked for
    twice = species_series(store, keys, cadence="raw", budget=5_000)
    assert twice.context.cadence == "1h"
    assert twice.summary["coarsened_from"] == "raw"
    assert twice.summary["budget"] == 5_000


def test_the_budget_coarsens_to_a_longer_cadence_each_step():
    """Each step bins at the next longer cadence, never at the one it has:
    a step to the same cadence would bin the same frame for ever."""
    steps = [series_chart._coarser(c) for c in ("raw", "1min", "10min", "1h", "1D")]

    assert steps == ["10min", "10min", "1h", "1D", None]


def test_species_series_by_day_is_the_views_day(month):
    campaign, store = month
    key = _cycles(campaign, "day")[0]

    with warnings.catch_warnings():
        # pandas deprecates the lower-case day
        warnings.simplefilter("error")
        fig = species_series(store, key, cadence="1d")

    assert fig.context.cadence == "1D"
    assert len(fig.data) == 31


def test_species_series_shades_the_events_as_given(month):
    campaign, store = month
    episode = campaign.truth.episodes[0]
    events = [{"start": episode.start, "end": episode.end, "label": "episode 1"}]

    fig = species_series(store, list(episode.series_keys[:2]), events=events)

    for panel in fig.panels:
        (span,) = panel.options["spans"]
        assert span["start"] == episode.start and span["end"] == episode.end
        assert span["label"] == "episode 1" and span["kind"] == "event"
    assert (
        species_series(store, episode.series_keys[0], events=None)
        .panels[0]
        .options["spans"]
        == []
    )
    # A time without a zone is the display zone's
    local = species_series(
        store,
        episode.series_keys[0],
        events=[{"start": "2026-06-07 12:00", "end": "2026-06-08 06:00"}],
    )
    assert local.panels[0].options["spans"][0]["start"] == episode.start


def test_the_y_axis_names_the_signal_not_a_concentration(month):
    campaign, store = month
    key = _cycles(campaign, "day")[0]
    want = {"TOF": "peak area", "Orbitrap": "peak height"}[
        campaign.params.instrument_type
    ]

    assert species_series(store, key).panels[0].axes.y.label == want
    assert diurnal(store, key).panels[0].axes.y.label == want
    assert period_boxes(store, key).panels[0].axes.y.label == want
    tic = species_series(store, key, normalise="tic")
    assert tic.panels[0].axes.y.label == "signal / TIC"
    assert "normalised to TIC" in tic.subtitle
    assert species_series(store, key).panels[0].axes.x.label == f"time ({ZONE})"


class _Samples:
    def __init__(self, types: list):
        self.frame = pd.DataFrame(
            {
                "t_mid_utc": pd.date_range(
                    "2026-06-01", periods=len(types), freq="h", tz="UTC"
                ),
                "instrument_type": types,
            }
        )

    def table(self, name: str, **_) -> pd.DataFrame:
        assert name == "samples"
        return self.frame


@pytest.mark.parametrize(
    "types, label",
    [
        (["tof", "tof"], "peak area"),
        (["orbi"], "peak height"),
        (["TOF", "Orbitrap"], "peak area or peak height"),
        (["tof", "quadrupole"], "signal"),
        ([None], "signal"),
    ],
)
def test_the_y_label_follows_the_instrument_types_in_the_window(types, label):
    assert common.y_label(_Samples(types), {"normalise": "none"}) == label


def test_the_window_keeps_its_instrument_types():
    store = _Samples(["orbi", "tof", "tof"])

    window = {"normalise": "none", "start": "2026-06-01T01:00:00+00:00"}
    assert common.y_label(store, window) == "peak area"
    assert common.y_label(store, {**window, "end": None}) == "peak area"
    assert (
        common.y_label(store, {"normalise": "none", "end": "2026-06-01T01:00Z"})
        == "peak height"
    )


def test_the_summary_is_plain_json_in_the_display_zone(month):
    campaign, store = month
    keys = [_formula_only(campaign), _cycles(campaign, "flat")[0]]

    fig = species_series(store, keys, cadence="1h", start="2026-06-03")

    summary = json.loads(json.dumps(fig.summary, allow_nan=False))
    assert summary == fig.summary
    assert [e["key"] for e in summary["species"]] == keys
    species = _species(store).set_index("series_key")
    for entry in summary["species"]:
        assert entry["formula"] == species.loc[entry["key"], "formula"]
    assert summary["requested"] == keys
    assert summary["tz"] == ZONE
    assert summary["start"] == "2026-06-03T00:00:00+03:00"
    assert pd.Timestamp(summary["end"]) == campaign.tables["samples"]["t_mid_utc"].max()
    assert summary["end"].endswith("+03:00")
    assert summary["normalise"] == "none" and summary["normalise_members"] == []
    assert summary["cadence"] == "1h"
    assert summary["source"] in ("rollups", "rows")
    assert pd.Timestamp(fig.context.start) == pd.Timestamp("2026-06-03", tz=ZONE)
    assert "from 2026-06-03 00:00" in fig.subtitle


def test_species_series_under_a_reagent_sum(clusters):
    """The joined name on the face, and the stretch the cluster is below
    detection noted as a partial sum."""
    campaign, store = clusters
    shares = campaign.truth.shares
    key = _cycles(campaign, "flat")[0]

    fig = species_series(store, key, cadence="1h", normalise=list(shares.series_keys))

    name = "reagent ion + reagent water cluster"
    assert f"normalised to {name}" in fig.subtitle
    assert fig.panels[0].axes.y.label == f"signal / {name}"
    assert fig.summary["normalise"] == list(shares.series_keys)
    assert fig.summary["normalise_members"] == list(shares.series_keys)
    complete = fig.summary["normalise_complete"]
    n = len(campaign.tables["samples"])
    assert complete == pytest.approx(1 - shares.lost_samples / n)
    (note,) = [note for note in fig.notes if "partial sum" in note]
    assert f"{100 * shares.lost_samples / n:.1f} %" in note
    # One ion alone is no sum, and has no partial sums to note
    one = species_series(store, key, cadence="1h", normalise=shares.series_keys[0])
    assert not any("partial sum" in note for note in one.notes)


@pytest.fixture(scope="module")
def year_figure(year):
    campaign, store = year
    keys = sorted(_species(store)["series_key"])
    return keys, species_series(store, keys[:5], cadence="1h")


def test_a_year_of_hourly_rollups_renders_within_the_point_budget(year_figure, year):
    """Five species of a year at 1 h are 43,800 bins, from the rollups:
    plotly draws each bin's median and its band's two edges, nothing more."""
    keys, fig = year_figure

    assert fig.summary["source"] == "rollups"
    assert fig.context.cadence == "1h"
    assert fig.summary["marks"] == len(fig.data) == 5 * 365 * 24
    assert fig.summary["marks"] <= series_chart.BUDGET
    plotted = render.plotly(fig)
    points = sum(len(trace.x) for trace in plotted.data)
    # A line and a band: three points a bin, a break between pieces
    assert points <= 3 * series_chart.BUDGET + 1_000
    # A sixth species would pass the budget: the year is drawn by day
    six = species_series(year[1], keys[:6], cadence="1h")
    assert six.context.cadence == "1D"
    assert six.summary["coarsened_from"] == "1h"
    # The local days from 1 June to the three hours of the next 1 June
    assert six.summary["marks"] == len(six.data) == 6 * 366
    assert "coarsened from 1h to 1D" in six.subtitle


# --- diurnal ----------------------------------------------------------------------


def test_diurnal_recovers_the_known_phases(month):
    campaign, store = month
    keys = [*_cycles(campaign, "day"), *_cycles(campaign, "night")]
    keys += _cycles(campaign, "flat")

    fig = diurnal(store, keys)

    # Every hour of the month is sampled on some thirty days
    assert all(e["sparse_hours"] == [] for e in fig.summary["species"])
    cycles = campaign.truth.cycles
    for key in keys:
        entry = _entry(fig, key)
        cycle = cycles[key]
        if cycle.peak_hour is None:
            assert 0.9 <= entry["day_night_ratio"] <= 1.1, key
            assert entry["amplitude"] < 1.1, key
            continue
        # The peak is on the hour, so the hours either side of it tie
        assert entry["hour_max"] in (cycle.peak_hour, (cycle.peak_hour - 1) % 24)
        assert (entry["hour_min"] - cycle.peak_hour) % 24 in range(10, 15), key
        if cycle.kind == "day":
            assert entry["day_night_ratio"] > 1.5, key
        else:
            assert entry["day_night_ratio"] < 1 / 1.5, key


def test_diurnal_summary_is_of_the_hourly_medians(month):
    campaign, store = month
    key = _cycles(campaign, "day")[0]

    fig = diurnal(store, key, day_hours=(9, 17), night_hours=(21, 5))

    median = _rows(fig, key).set_index("hour")["value"]
    entry = _entry(fig, key)
    assert entry["hour_max"] == int(median.idxmax())
    assert entry["hour_min"] == int(median.idxmin())
    assert entry["amplitude"] == pytest.approx(median.max() / median.min())
    day = list(range(9, 17))
    night = [21, 22, 23, 0, 1, 2, 3, 4]
    assert entry["day_night_ratio"] == pytest.approx(
        median[day].median() / median[night].median()
    )
    assert entry["min_n"] == int(_rows(fig, key)["n"].min())
    assert fig.summary["day_hours"] == [9, 17]
    assert fig.summary["night_hours"] == [21, 5]
    assert fig.summary["day"] == day and fig.summary["night"] == night
    assert fig.summary["stat"] == "median" and fig.summary["band"] == [25, 75]
    defaults = diurnal(store, key).summary
    assert defaults["day"] == [10, 11, 12, 13, 14, 15]
    assert defaults["night"] == [22, 23, 0, 1, 2, 3]


def test_diurnal_draws_an_hour_at_its_middle_on_the_zones_clock(month):
    campaign, store = month
    key = _cycles(campaign, "night")[0]

    fig = diurnal(store, key)

    rows = _rows(fig, key)
    assert list(rows["x"]) == [h + 0.5 for h in range(24)]
    panel = fig.panels[0]
    # A line per species, its legend and its hover the species' name
    assert panel.encodings == {
        "x": "x",
        "y": "value",
        "group": "key",
        "name": "name",
        "lo": "lo",
        "hi": "hi",
    }
    assert panel.axes.x.label == f"hour of day ({ZONE})"
    assert panel.axes.x.range == (0.0, 24.0)
    assert panel.axes.y.scale == "linear"
    assert "hour of day medians, p25-p75 band" in fig.subtitle
    assert any("solar time" in note and ZONE in note for note in fig.notes)
    utc = diurnal(store, key, tz="UTC")
    assert utc.panels[0].axes.x.label == "hour of day (UTC)"
    # Helsinki is three hours ahead of UTC in June
    assert (_entry(utc, key)["hour_max"] - _entry(fig, key)["hour_max"]) % 24 == 21


def test_diurnal_by_a_split_draws_a_line_per_group_in_each_panel(month):
    campaign, store = month
    keys = _cycles(campaign, "day")[:2]

    fig = diurnal(store, keys, by="weekday")

    assert len(fig.panels) == 2
    assert fig.columns == 2 and fig.share_x
    assert diurnal(store, _cycles(campaign, "night", "formula:")[:4]).columns == 3
    for panel, key in zip(fig.panels, keys):
        assert panel.where == {"key": [key]}
        # A line per group, its legend the group's
        assert panel.encodings == {
            "x": "x",
            "y": "value",
            "group": "group",
            "lo": "lo",
            "hi": "hi",
        }
    assert set(fig.data["group"]) == {"weekday", "weekend"}
    assert "by weekday" in fig.subtitle
    assert fig.summary["by"] == "weekday"
    want = views.diurnal(store, keys, by="weekday")
    for key in keys:
        entry = _entry(fig, key)
        assert [g["group"] for g in entry["groups"]] == ["weekday", "weekend"]
        for group in entry["groups"]:
            rows = want[(want["key"] == key) & (want["group"] == group["group"])]
            median = rows.set_index("hour")["value"]
            assert group["hour_max"] == int(median.idxmax())
            assert group["min_n"] == int(rows["n"].min())
            # The weekend's two days a week, judged per day, are no fewer
            # than the week's five
            assert group["sparse_hours"] == [], group["group"]
        # The whole window's numbers stand beside the groups'
        whole = views.diurnal(store, key)
        assert entry["hour_max"] == int(whole.set_index("hour")["value"].idxmax())
        assert entry["min_n"] == int(whole["n"].min())
        assert entry["min_n"] > max(g["min_n"] for g in entry["groups"])
    assert diurnal(store, keys[0]).summary["species"][0].get("groups") is None


def test_diurnal_judges_an_hour_by_its_samples_not_its_values(month):
    """A night species below detection by day is sampled there: its few
    day values are its trough, read high, not an hour sampled on few days."""
    campaign, store = month
    seldom = _seldom(campaign)
    flat = _cycles(campaign, "flat")[0]

    fig = diurnal(store, [seldom, flat])

    rows = _rows(fig, seldom)
    assert (rows["n"] < rows["n"].median() / 2).any()
    entry = _entry(fig, seldom)
    assert entry["sparse_hours"] == []
    assert entry["min_n"] == int(rows["n"].min())
    assert not any("few days" in note for note in fig.notes)
    # Read high, if it is detected seldom enough
    named = any(entry["name"] in note and "read high" in note for note in fig.notes)
    assert named == (entry["presence"] < common.SELDOM)


def test_diurnal_notes_the_hours_a_window_samples_on_few_days(month):
    """Six hours of one day: the other eighteen hold no sample, beside
    hours that hold some."""
    campaign, store = month
    key = _cycles(campaign, "day")[0]

    fig = diurnal(
        store, key, start="2026-06-02T09:00+03:00", end="2026-06-02T15:00+03:00"
    )

    entry = _entry(fig, key)
    assert entry["sparse_hours"] == [*range(9), *range(15, 24)]
    (note,) = [note for note in fig.notes if "few days" in note]
    assert note == (
        "An hour sampled on few days is no cycle. Under half the samples of a "
        f"typical hour: {entry['name']} (hours 0-8, 15-23)."
    )


def test_diurnal_by_month_notes_a_month_the_window_holds_hours_of(month):
    """The window ends at 03:00 local on 1 July: July's three hours of one
    day are no cycle beside June's thirty days, in any hour."""
    campaign, store = month
    keys = _cycles(campaign, "day")[:2]

    fig = diurnal(store, keys, by="month")

    for key in keys:
        entry = _entry(fig, key)
        june, july = entry["groups"]
        assert (june["group"], july["group"]) == ("Jun", "Jul")
        assert june["sparse_hours"] == []
        assert july["sparse_hours"] == list(range(24))
        rows = _rows(fig, key)
        sampled = rows[(rows["group"] == "Jul") & (rows["n_samples"] > 0)]
        assert list(sampled["hour"]) == [0, 1, 2]
        # The window's own cycle is sampled on thirty days in every hour
        assert entry["sparse_hours"] == []
    (note,) = [note for note in fig.notes if "few days" in note]
    names = [_entry(fig, key)["name"] for key in keys]
    assert note == (
        "An hour sampled on few days is no cycle. Under half the samples of the "
        "fullest group's typical hour: "
        + "; ".join(f"{name}, Jul (every hour)" for name in names)
        + "."
    )


def test_diurnal_by_weekday_judges_a_group_per_day_of_the_week_it_holds(month):
    """A window from Saturday to 03:00 on Monday: the weekend's two whole
    days are its cycle; Monday's three hours, two fifths of a weekend day's
    samples per day of the week, are none."""
    campaign, store = month
    key = _cycles(campaign, "day")[0]

    fig = diurnal(
        store,
        key,
        by="weekday",
        start="2026-06-06T00:00+03:00",
        end="2026-06-08T03:00+03:00",
    )

    weekday, weekend = _entry(fig, key)["groups"]
    assert (weekday["group"], weekend["group"]) == ("weekday", "weekend")
    assert weekend["sparse_hours"] == []
    assert weekday["sparse_hours"] == list(range(24))
    rows = _rows(fig, key)
    monday = rows[(rows["group"] == "weekday") & (rows["n_samples"] > 0)]
    assert list(monday["hour"]) == [0, 1, 2]


def test_an_hour_under_half_the_typical_hours_samples_is_sparse():
    """The typical hour is the median over the hours sampled at all: an
    hour with none is sparse beside hours with some, and a cycle sampled in
    no hour has no sparse hour."""
    day, night = hours((10, 16), "day"), hours((22, 4), "night")
    n_samples = [0] * 14 + [100] * 6 + [50, 49, 100, 100]

    got = diurnal_module.cycle(_hours([1.0] * 24, n_samples=n_samples), day, night)

    assert got["sparse_hours"] == [*range(14), 21]
    unsampled = _hours([np.nan] * 24, n=[0] * 24, n_samples=[0] * 24)
    assert diurnal_module.cycle(unsampled, day, night)["sparse_hours"] == []
    # Against a fuller group's typical hour, every hour of this one
    full = _hours([1.0] * 24, n_samples=[100] * 24)
    assert diurnal_module.cycle(full, day, night, 201.0)["sparse_hours"] == list(
        range(24)
    )
    assert diurnal_module.cycle(full, day, night, 200.0)["sparse_hours"] == []


@pytest.mark.parametrize(
    "given, named",
    [
        ([5], "hour 5"),
        ([0, 1, 2, 5, 22, 23], "hours 0-2, 5, 22-23"),
        ([3, 7], "hours 3, 7"),
        (list(range(24)), "every hour"),
    ],
)
def test_a_note_names_hours_as_runs(given, named):
    assert diurnal_module._listed(given) == named


def test_diurnal_of_a_flat_species_is_flat_under_the_reagent_sum(clusters):
    """Divided by the bare ion alone, a flat species takes the inverse of
    the bare ion's share's cycle; divided by the sum, it stays flat."""
    campaign, store = clusters
    keys = _cycles(campaign, "flat")
    shares = list(campaign.truth.shares.series_keys)

    summed = diurnal(store, keys, normalise=shares)
    bare = diurnal(store, keys, normalise=shares[0])

    for key in keys:
        assert 0.9 <= _entry(summed, key)["day_night_ratio"] <= 1.1, key
        assert not 0.9 <= _entry(bare, key)["day_night_ratio"] <= 1.1, key
    assert "normalised to reagent ion + reagent water cluster" in summed.subtitle


@pytest.mark.parametrize(
    "span, want",
    [
        ((10, 16), list(range(10, 16))),
        ((22, 4), [22, 23, 0, 1, 2, 3]),
        ((20, 24), [20, 21, 22, 23]),
    ],
)
def test_day_and_night_hours_are_clock_spans(span, want):
    assert hours(span, "day_hours") == want


@pytest.mark.parametrize(
    "kwargs",
    [
        {"by": "year"},
        {"y": "symlog"},
        {"day_hours": (10, 10)},
        {"day_hours": (0, 24)},
        {"day_hours": (10,)},
        {"night_hours": (25, 3)},
        {"day_hours": (10, 16), "night_hours": (15, 20)},
    ],
)
def test_diurnal_refuses_what_it_cannot_draw(month, kwargs):
    campaign, store = month

    with pytest.raises(InvalidChart):
        diurnal(store, _cycles(campaign, "day")[0], **kwargs)


# --- period_boxes -----------------------------------------------------------------


def test_period_boxes_by_day_put_the_episode_day_highest(month):
    """The first episode runs from midday on 7 June to the early hours of
    the 8th, local time: by day, the 7th has the highest median."""
    campaign, store = month
    episode = campaign.truth.episodes[0]
    keys = list(episode.series_keys)

    fig = period_boxes(store, keys, period="day")

    day = episode.start.tz_convert(ZONE).normalize()
    for key in keys:
        entry = _entry(fig, key)
        assert entry["highest_median"] == day.isoformat(), key
        medians = [p["median"] for p in entry["periods"]]
        assert medians == list(_rows(fig, key)["median"])
        # Of the days that are not thin: in the positive month, the gap's
        # three night hours on the 21st read higher for a night species
        rows = _rows(fig, key)
        whole = rows[~rows["thin"]]
        assert entry["highest_median"] == common.at(
            whole.loc[whole["median"].idxmax(), "period"], ZONE
        )
    assert list(_rows(fig, keys[0])["label"][:2]) == ["2026-06-01", "2026-06-02"]
    assert fig.panels[0].axes.x.label == f"day ({ZONE})"


def test_period_boxes_by_week_show_the_episode_in_p95_not_the_median(month):
    """Six hours in a week move its top, not its middle."""
    campaign, store = month
    episode = campaign.truth.episodes[0]
    # The reagent's fall would move every week's median
    normalise = campaign.truth.drift.series_key if campaign.truth.drift else "none"
    keys = [k for k in episode.series_keys if k.startswith("ion:")]

    fig = period_boxes(store, keys, period="week", normalise=normalise)

    week = pd.Timestamp("2026-06-01", tz=ZONE)
    for key in keys:
        rows = _rows(fig, key).set_index("period")
        after = rows.drop(week).iloc[:3]
        assert rows.loc[week, "p95"] > 2.5 * after["p95"].max(), key
        assert rows.loc[week, "median"] < 1.15 * after["median"].max(), key
    assert list(_rows(fig, keys[0])["label"]) == [
        "2026-06-01",
        "2026-06-08",
        "2026-06-15",
        "2026-06-22",
        "2026-06-29",
    ]
    assert fig.panels[0].axes.x.label == f"week from Monday ({ZONE})"


def test_a_thin_period_is_hollow_and_flagged(month):
    """The last week holds two days; by day, the gap leaves the 21st three
    hours and 1 July has the three after midnight."""
    campaign, store = month
    key = _cycles(campaign, "flat")[0]

    weeks = period_boxes(store, key, period="week")
    days = period_boxes(store, key, period="day")

    assert _entry(weeks, key)["thin_periods"] == ["2026-06-29T00:00:00+03:00"]
    assert _entry(days, key)["thin_periods"] == [
        "2026-06-21T00:00:00+03:00",
        "2026-07-01T00:00:00+03:00",
    ]
    rows = _rows(weeks, key)
    fullest = weeks.data["n_samples"].max()
    np.testing.assert_array_equal(rows["thin"], rows["n_samples"] < fullest / 2)
    panel = weeks.panels[0]
    assert panel.encodings["hollow"] == "thin"
    assert panel.options["hollow_label"] == THIN
    periods = _entry(weeks, key)["periods"]
    assert [p["thin"] for p in periods] == list(rows["thin"])
    assert [p["n_samples"] for p in periods] == list(rows["n_samples"])
    assert [p["n"] for p in periods] == list(rows["n"])
    assert [p["label"] for p in periods] == list(rows["label"])
    assert periods[0]["start"] == "2026-06-01T00:00:00+03:00"
    # A species seldom detected: its values and its samples apart
    seldom = _seldom(campaign)
    sparse = period_boxes(store, seldom, period="week")
    rows = _rows(sparse, seldom)
    assert (rows["n"] < rows["n_samples"]).all()
    periods = _entry(sparse, seldom)["periods"]
    assert [p["n_samples"] for p in periods] == list(rows["n_samples"])
    assert [p["n"] for p in periods] == list(rows["n"])
    assert weeks.columns == 1 and days.columns == 1
    assert period_boxes(store, _cycles(campaign, "day")[:3]).columns == 2


def test_period_boxes_say_what_a_box_is(month):
    campaign, store = month
    key = _cycles(campaign, "day")[0]

    fig = period_boxes(store, key, period="month")

    assert "p25-p75 with the median, whiskers p5-p95, no outlier marks" in fig.subtitle
    panel = fig.panels[0]
    assert panel.primitive == "box"
    assert {k: panel.encodings[k] for k in ("q1", "median", "q3", "lo", "hi")} == {
        "q1": "p25",
        "median": "median",
        "q3": "p75",
        "lo": "p5",
        "hi": "p95",
    }
    # Each period's minimum and maximum stay in the frame
    assert {"min", "max"} <= set(fig.data.columns)
    assert list(_rows(fig, key)["label"]) == ["2026-06", "2026-07"]
    assert fig.summary["period"] == "month"
    assert panel.axes.x.label == f"month ({ZONE})"


@pytest.mark.parametrize("kwargs", [{"period": "year"}, {"y": "symlog"}])
def test_period_boxes_refuse_what_they_cannot_draw(month, kwargs):
    campaign, store = month

    with pytest.raises(InvalidChart):
        period_boxes(store, _cycles(campaign, "day")[0], **kwargs)


# --- every chart --------------------------------------------------------------------


def _build(kind: str, store: CampaignStore, campaign: Campaign) -> Figure:
    keys = [_formula_only(campaign), _cycles(campaign, "day")[0]]
    return registry.get(kind).build(store, species=keys)


@pytest.mark.parametrize("kind", KINDS)
def test_each_chart_renders_on_both_backends_and_round_trips(month, kind):
    campaign, store = month

    fig = _build(kind, store, campaign)

    drawn = render.matplotlib(fig)
    assert len(drawn.axes) >= len(fig.panels)
    plotted = render.plotly(fig)
    json.loads(plotted.to_json())
    again = Figure.from_json(fig.to_json())
    assert again == fig
    assert fig.id == kind


@pytest.mark.parametrize("kind", KINDS)
def test_each_chart_carries_the_view_context_and_presence(month, kind):
    campaign, store = month

    fig = _build(kind, store, campaign)

    assert fig.context.tz == ZONE
    assert fig.context.normalise == "none"
    assert fig.context.mixed_runs is True
    assert fig.summary["mixed_runs"] is True
    assert {"n", "n_samples"} <= set(fig.data.columns)
    for entry in fig.summary["species"]:
        rows = _rows(fig, entry["key"])
        assert set(entry) >= {"key", "name", "formula", "presence"}
        assert entry["presence"] == pytest.approx(
            rows["n"].sum() / rows["n_samples"].sum()
        )
    # Every field the registry promises
    fields = registry.get(kind).summary
    assert {f for f in fields if "[]" not in f} <= set(fig.summary)
    per_species = {f.split("[].")[1] for f in fields if "[]." in f}
    present = set().union(*(set(e) for e in fig.summary["species"]))
    assert per_species - {"groups"} <= present


@pytest.mark.parametrize("kind", KINDS)
def test_each_chart_refuses_more_panels_than_it_holds(month, kind):
    campaign, store = month
    keys = sorted(campaign.truth.cycles)[:25]

    with pytest.raises(InvalidChart, match="24"):
        registry.get(kind).build(store, species=keys)


@pytest.mark.parametrize("kind", KINDS)
def test_each_chart_refuses_a_window_without_values(month, kind):
    campaign, store = month

    with pytest.raises(InvalidChart, match="No values"):
        registry.get(kind).build(
            store,
            species=_cycles(campaign, "day")[0],
            start="2026-06-21 06:00",
            end="2026-06-21 12:00",
        )


def test_the_charts_are_registered_with_their_parameters():
    found = {kind.id: kind for kind in registry.all()}

    assert set(KINDS) <= set(found)
    for kind in KINDS:
        chart = found[kind]
        assert chart.tier == 1
        params = {p.name: p for p in chart.params}
        assert params["species"].type == "species"
        assert params["species"].default == "top:5"
        assert params["normalise"].type == "normaliser"
        assert params["normalise"].default == "none"
        json.dumps(chart.to_dict())
    assert found["species_series"].build is species_series
    assert found["diurnal"].build is diurnal
    assert found["period_boxes"].build is period_boxes
    series_params = {p.name: p for p in found["species_series"].params}
    assert series_params["cadence"].choices == ("auto", "10min", "1h", "1d", "raw")
    assert series_params["budget"].default == 50_000
    assert {p.name: p for p in found["diurnal"].params}["by"].choices == (
        "none",
        "month",
        "season",
        "weekday",
    )
    assert {p.name: p for p in found["period_boxes"].params}["period"].choices == (
        "day",
        "week",
        "month",
    )


@pytest.mark.parametrize("kind", KINDS)
def test_each_chart_takes_its_species_by_top_n(month, kind):
    campaign, store = month

    fig = registry.get(kind).build(store, species="top:3")

    assert fig.summary["requested"] == "top:3"
    assert [e["key"] for e in fig.summary["species"]] == resolve(store, "top:3").keys


# --- the shared helpers -------------------------------------------------------------


def test_a_time_is_given_in_the_display_zone():
    assert common.at(None, ZONE) is None
    assert common.at("2026-06-01T00:00:00Z", ZONE) == "2026-06-01T03:00:00+03:00"
    assert common.number(float("nan")) is None
    assert common.number(None) is None
    assert common.number(np.float64(2.5)) == 2.5
    assert common.presence([3, 4], [5, 5]) == 0.7
    assert common.presence([0], [0]) is None


@pytest.mark.parametrize(
    "top, bottom, want",
    [(6, 3, 2.0), (1, 0, None), (1, -1, None), (np.nan, 1, None), (1, None, None)],
)
def test_a_ratio_is_of_finite_values_over_a_positive_one(top, bottom, want):
    assert common.ratio(top, bottom) == want
    assert common.ratio(None, 1) is None


def test_a_normalised_axis_names_its_normaliser_or_its_key():
    attrs = {"normalise": "ion:x", "normalise_name": "reagent"}

    assert common.y_label(_Samples(["tof"]), attrs) == "signal / reagent"
    assert (
        common.y_label(_Samples(["tof"]), {**attrs, "normalise_name": None})
        == "signal / ion:x"
    )


def test_a_change_of_both_polarities_at_once_is_one_rule():
    at = "2026-06-16T00:00:00+00:00"
    changes = [
        {"at": at, "sample_batch_id": "b", "polarity": p, "before": "1", "after": "2"}
        for p in "+-"
    ]
    attrs = {"mixed_runs": True, "run_changes": changes, "tz": ZONE}

    assert common.run_rules(attrs) == [{"at": at, "label": "engine run: 2"}]
    (note,) = common.notes(attrs, [])
    assert note.count("2026-06-16 03:00") == 1
    assert common.run_rules({"run_changes": []}) == []


@pytest.mark.parametrize(
    "members, complete, noted",
    [
        (["ion:a", "ion:b"], 0.75, True),
        (["ion:a", "ion:b"], 1.0, False),
        (["ion:a", "ion:b"], None, False),
        (["ion:a"], 0.75, False),
    ],
)
def test_a_partial_sum_is_noted_only_for_a_sum(members, complete, noted):
    attrs = {
        "normalise_members": members,
        "normalise_complete": complete,
        "normalise_name": "a + b",
        "tz": ZONE,
    }

    found = [note for note in common.notes(attrs, []) if "partial sum" in note]
    assert bool(found) == noted
    if noted:
        assert "In 25.0 % of the samples" in found[0]


def _hours(values, n=None, n_samples=None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "hour": range(24),
            "value": values,
            "n": [10] * 24 if n is None else n,
            "n_samples": [10] * 24 if n_samples is None else n_samples,
        }
    )


def test_a_cycle_without_a_value_or_a_positive_trough_has_no_ratio():
    day, night = hours((10, 16), "day"), hours((22, 4), "night")

    empty = diurnal_module.cycle(_hours([np.nan] * 24, n=[0] * 24), day, night)
    assert empty["hour_max"] is None and empty["day_night_ratio"] is None
    assert empty["min_n"] == 0 and empty["presence"] == 0.0

    zeros = [0.0] * 24
    zeros[12] = 5.0
    flat_night = diurnal_module.cycle(_hours(zeros), day, night)
    assert flat_night["hour_max"] == 12 and flat_night["hour_min"] == 0
    assert flat_night["amplitude"] is None
    assert flat_night["day_night_ratio"] is None

    no_night = [1.0] * 24
    for h in night:
        no_night[h] = np.nan
    assert diurnal_module.cycle(_hours(no_night), day, night)["day_night_ratio"] is None
    assert (
        diurnal_module.cycle(_hours([2.0] * 24), day, night)["day_night_ratio"] == 1.0
    )
