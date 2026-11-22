"""The Figure spec and its two renderers (docs/dev/visual_digest.md, 2.4).

Every primitive renders on both backends from the same spec; a NaN bin
breaks a line on both; log axes, bands and spans appear on both; the
palettes pass the contrast check in both themes; a PNG is the same bytes
on every run; a spec round-trips through JSON and renders the same from
the parsed document; and the data layer and the charts import without
the renderers.
"""

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fixtures import specs
from matplotlib.collections import PolyCollection, QuadMesh
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path as MplPath
from matplotlib.table import Table

from mascope_viz import render
from mascope_viz.charts import FIGURE_SPEC_VERSION, PRIMITIVES, Figure, Panel
from mascope_viz.render import common, theme


TESTS = Path(__file__).parent

ALL = [*specs.FIGURES, *specs.EXTRA, "everything"]


def _figure(name: str) -> Figure:
    if name == "everything":
        return specs.everything()
    return {**specs.FIGURES, **specs.EXTRA}[name]()


def _png(fig: Figure, theme_name: str = "light") -> bytes:
    drawn = render.matplotlib(fig, theme=theme_name)
    buffer = io.BytesIO()
    drawn.savefig(buffer, format="png")
    return buffer.getvalue()


def _texts(drawn) -> list[str]:
    """Every text a matplotlib figure draws."""
    return [t.get_text() for t in drawn.findobj(lambda a: hasattr(a, "get_text"))]


def _runs(line) -> int:
    """How many pieces a matplotlib line is drawn in: a NaN starts a new
    one."""
    codes = [code for _, code in line.get_path().iter_segments(remove_nans=True)]
    return codes.count(MplPath.MOVETO)


# --- every primitive, both backends ---------------------------------------------


def test_the_fixtures_cover_every_primitive():
    assert set(specs.FIGURES) == set(PRIMITIVES)
    assert {p.primitive for p in specs.everything().panels} == set(PRIMITIVES)


@pytest.mark.parametrize("theme_name", theme.THEMES)
@pytest.mark.parametrize("name", ALL)
def test_every_primitive_renders_on_both_backends(name, theme_name):
    fig = _figure(name)
    drawn = render.matplotlib(fig, theme=theme_name)
    assert _png(fig, theme_name).startswith(b"\x89PNG")
    assert len(drawn.axes) >= len(fig.panels)
    plotted = render.plotly(fig, theme=theme_name)
    json.loads(plotted.to_json())
    assert plotted.layout.paper_bgcolor == theme.get(theme_name).paper
    assert len(plotted.data) >= len(fig.panels)


#: The trace types each primitive's fixture draws with plotly: a legend
#: entry with no data of its own (a gap, a hollow box) is a scatter, and so
#: are the bars' intervals.
TRACES = {
    "series": {"scatter"},
    "strip": {"bar", "scatter"},
    "heatmap": {"heatmap", "scatter"},
    "scatter": {"scatter"},
    "sticks": {"scatter"},
    "bars": {"bar", "scatter"},
    "box": {"box", "scatter"},
    "table": {"table"},
}


@pytest.mark.parametrize("name", list(specs.FIGURES))
def test_each_primitive_draws_its_marks_on_both_backends(name):
    fig = specs.FIGURES[name]()
    plotted = render.plotly(fig)
    assert {trace.type for trace in plotted.data} == TRACES[name]
    ax = render.matplotlib(fig).axes[0]
    children = ax.get_children()
    if name == "series":
        assert len(ax.get_lines()) >= 2
    elif name == "strip":
        assert any(isinstance(c, PolyCollection) for c in children)
    elif name == "heatmap":
        assert any(isinstance(c, QuadMesh) for c in children)
    elif name == "scatter":
        assert len(ax.collections) >= 1
    elif name == "sticks":
        assert ax.collections and ax.collections[0].get_segments()
    elif name == "bars":
        bars = [c for c in children if isinstance(c, Rectangle) and c.get_width() < 1]
        assert len(bars) == 6
    elif name == "box":
        assert len([c for c in children if isinstance(c, PathPatch)]) == 7
    else:
        assert any(isinstance(c, Table) for c in children)


def test_the_eight_primitives_draw_the_same_counts_on_both_backends():
    """What a renderer draws of a spec is the spec's: the same bars, boxes,
    sticks and cells on both."""
    bars = specs.bars()
    plotted = render.plotly(bars)
    drawn = render.matplotlib(bars)
    plotly_bars = sum(len(t.x) for t in plotted.data if t.type == "bar")
    mpl_bars = sum(
        1
        for ax in drawn.axes
        for c in ax.get_children()
        if isinstance(c, Rectangle) and 0 < c.get_width() < 1
    )
    assert plotly_bars == mpl_bars == 12
    box = specs.box()
    plotly_boxes = sum(len(t.x) for t in render.plotly(box).data if t.type == "box")
    mpl_boxes = sum(
        isinstance(c, PathPatch) for c in render.matplotlib(box).axes[0].get_children()
    )
    # One box has no first quartile: neither draws it.
    assert plotly_boxes == mpl_boxes == 7
    sticks = specs.sticks()
    plotly_sticks = sum(
        sum(1 for v in t.y if v is not None) // 2
        for t in render.plotly(sticks).data
        if t.mode == "lines"
    )
    mpl_sticks = sum(
        len(c.get_segments())
        for ax in render.matplotlib(sticks).axes
        for c in ax.collections
    )
    assert plotly_sticks == mpl_sticks == 16


#: The axes whose grid a primitive hides: a heatmap's cells, a strip's rows
#: and a category x. Every other axis draws its grid.
HIDDEN_GRIDS = {"strip": {"y"}, "heatmap": {"x", "y"}, "bars": {"x"}, "box": {"x"}}


def _mpl_grid(ax, axis: str) -> bool:
    """Whether a matplotlib axis draws its grid: every gridline, or none."""
    shown = {line.get_visible() for line in getattr(ax, f"{axis}axis").get_gridlines()}
    assert len(shown) == 1
    return shown.pop()


@pytest.mark.parametrize("theme_name", theme.THEMES)
@pytest.mark.parametrize("name", ALL)
def test_both_backends_hide_the_same_grids(name, theme_name):
    """The theme's axis style does not turn a grid a panel hides back on:
    plotly styles the axes before the panels draw."""
    fig = _figure(name)
    drawn = render.matplotlib(fig, theme=theme_name)
    drawn.draw_without_rendering()
    axes = [ax for ax in drawn.axes if ax.get_subplotspec() is not None]
    plotted = render.plotly(fig, theme=theme_name)
    style = theme.get(theme_name)
    cells = common.arrange(fig, style).cells
    for panel, ax, (row, column) in zip(fig.panels, axes, cells, strict=True):
        if panel.primitive == "table":
            continue
        subplot = plotted.get_subplot(row + 1, column + 1)
        for axis in ("x", "y"):
            shown = axis not in HIDDEN_GRIDS.get(panel.primitive, set())
            where = (panel.primitive, axis)
            assert _mpl_grid(ax, axis) is shown, where
            styled = getattr(subplot, f"{axis}axis")
            assert styled.showgrid == shown, where
            # The rest of the theme's style still reaches every axis.
            assert styled.gridcolor == style.grid, where
            assert styled.showline is True and styled.zeroline is False, where


# --- gaps, log axes, bands and spans -------------------------------------------------


def test_a_nan_bin_breaks_the_line_on_both_backends():
    fig = specs.series()
    rows = fig.data[fig.data["key"] == "ion:1"]
    gaps = np.flatnonzero(rows["value"].isna().to_numpy())
    assert list(gaps) == [20, 21, 30, 32]
    ax = render.matplotlib(fig).axes[0]
    line = next(line for line in ax.get_lines() if len(line.get_ydata()) == 48)
    assert np.isnan(np.asarray(line.get_ydata(), dtype=float)[gaps]).all()
    # 0-19, 22-29, 31 alone, 33-47
    assert _runs(line) == 4
    plotted = render.plotly(fig)
    lines = [t for t in plotted.data if t.mode == "lines" and t.fill is None]
    assert len(lines) == 2
    for trace in lines:
        assert trace.connectgaps is False
        assert [i for i, v in enumerate(trace.y) if v is None] == [20, 21, 30, 32]


def test_a_value_alone_between_gaps_is_a_point_on_both_backends():
    """A line through one value draws nothing: both draw it as a point."""
    fig = specs.series()
    ax = render.matplotlib(fig).axes[0]
    dots = [
        line
        for line in ax.get_lines()
        if line.get_linestyle() == "None" and line.get_marker() == "o"
    ]
    assert len(dots) == 2 and all(len(d.get_xdata()) == 1 for d in dots)
    plotted = render.plotly(fig)
    points = [t for t in plotted.data if t.mode == "markers"]
    assert len(points) == 2 and all(len(t.y) == 1 for t in points)


def test_log_axes_bands_and_spans_appear_on_both_backends():
    fig = specs.series()
    ax = render.matplotlib(fig).axes[0]
    assert ax.get_yscale() == "log"
    bands = [c for c in ax.collections if isinstance(c, PolyCollection)]
    # Four runs of band per species: the line's runs.
    assert len(bands) == 8
    spans = [
        c
        for c in ax.get_children()
        if isinstance(c, Rectangle) and c.get_zorder() == 0.5
    ]
    assert len(spans) == 2
    rules = [line for line in ax.get_lines() if line.get_zorder() == 1.5]
    assert len(rules) == 1
    texts = [t.get_text().strip() for t in ax.texts]
    assert {"Episode 1", "no samples", "run changes"} <= set(texts)
    plotted = render.plotly(fig)
    assert plotted.layout.yaxis.type == "log"
    filled = [t for t in plotted.data if t.fill == "toself"]
    assert len(filled) == 2
    assert sum(v is None for v in filled[0].x) == 3
    shapes = plotted.layout.shapes
    assert [s.type for s in shapes] == ["rect", "rect", "line"]
    gap, event = theme.LIGHT.token("gap"), theme.LIGHT.token("event")
    assert shapes[0].fillcolor.startswith(
        "rgba(" + ",".join(str(round(c * 255)) for c in theme.rgb(event))
    )
    assert shapes[1].fillcolor.startswith(
        "rgba(" + ",".join(str(round(c * 255)) for c in theme.rgb(gap))
    )
    assert {"Episode 1", "no samples", "run changes"} <= {
        a.text for a in plotted.layout.annotations
    }


def test_a_log_axis_draws_a_value_that_is_not_positive_as_a_gap():
    fig = specs.series()
    fig.data.loc[5, "value"] = 0.0
    fig.data.loc[6, "value"] = -1.0
    fig = Figure.from_json(fig.to_json())
    line = next(
        line
        for line in render.matplotlib(fig).axes[0].get_lines()
        if len(line.get_ydata()) == 48
    )
    assert np.isnan(np.asarray(line.get_ydata(), dtype=float)[[5, 6]]).all()
    trace = render.plotly(fig).data[1]
    assert trace.y[5] is None and trace.y[6] is None


def test_time_axes_show_the_columns_zone_on_both_backends():
    """matplotlib labels the instants in the column's zone; plotly, which
    has no zones, draws the wall-clock times in it."""
    fig = specs.series()
    plotted = render.plotly(fig)
    assert plotted.data[1].x[0] == "2026-07-01T00:00:00"
    ax = render.matplotlib(fig).axes[0]
    formatter = ax.xaxis.get_major_formatter()
    first = pd.Timestamp("2026-07-01T00:00", tz=specs.ZONE)
    assert str(formatter._tz) == specs.ZONE
    # matplotlib's date number of the first bin's instant: days since 1970 UTC
    assert ax.get_lines()[0].get_xdata()[0] == pytest.approx(
        first.as_unit("ns").value / 86400e9, abs=1e-9
    )


# --- groups, colours, labels ------------------------------------------------------------


def test_a_group_keeps_its_colour_in_every_panel_and_on_both_backends():
    fig = specs.everything()
    colours = common.colours(fig, theme.LIGHT)
    assert colours.groups["ion:1"] == theme.LIGHT.categorical[0]
    assert colours.groups["formula:C3H4O4|[M+I]-"] == theme.LIGHT.categorical[1]
    assert colours.name("ion:1") == "Malonic acid"
    drawn = render.matplotlib(fig)
    legend = drawn.legends[0]
    handles = dict(
        zip([t.get_text() for t in legend.get_texts()], legend.legend_handles)
    )
    from matplotlib.colors import to_hex

    assert to_hex(handles["Malonic acid"].get_color()) == theme.LIGHT.categorical[0]
    plotted = render.plotly(fig)
    named = {t.name: t for t in plotted.data if getattr(t, "showlegend", None)}
    assert named["Malonic acid"].line.color == theme.LIGHT.categorical[0]
    box = next(t for t in plotted.data if t.type == "box" and t.name == "Malonic acid")
    assert box.line.color == theme.LIGHT.categorical[0]


def test_a_group_is_one_legend_entry_on_both_backends():
    """One entry per group across the panels, under the group's display
    name, then one per mark, gap style, size and hollow style."""
    fig = specs.everything()
    plotted = render.plotly(fig)
    shown = [t.name for t in plotted.data if getattr(t, "showlegend", None)]
    assert shown == [
        "Malonic acid",
        "C3H4O4 $I$ &lt;b&gt;",
        "acquired, in no batch",
        "no samples",
        "50000",
        "10000",
        "assigned",
        "candidate",
        "fewer than 10 samples",
    ]
    malonic = [t for t in plotted.data if getattr(t, "legendgroup", None) == "ion:1"]
    assert {t.type for t in malonic} >= {"scatter", "box"}
    legend = render.matplotlib(fig).legends[0]
    assert [t.get_text() for t in legend.get_texts()] == [
        "Malonic acid",
        "C3H4O4 \\$I\\$ <b>",
        "acquired, in no batch",
        "no samples",
        "Median intensity",
        "50000",
        "10000",
        "assigned",
        "candidate",
        "fewer than 10 samples",
    ]


def test_stacked_bars_sit_on_the_group_before_on_both_backends():
    fig = specs.bars()
    plotted = render.plotly(fig)
    stacked = [t for t in plotted.data if t.type == "bar" and t.xaxis == "x2"]
    assert list(stacked[0].base) == [0.0, 0.0, 0.0]
    assert list(stacked[1].base) == list(stacked[0].y)
    drawn = render.matplotlib(fig)
    ax = [a for a in drawn.axes if a.get_label() != "<colorbar>"][1]
    bottoms = sorted(
        (round(c.get_x(), 6), c.get_y(), c.get_height())
        for c in ax.patches
        if isinstance(c, Rectangle)
    )
    # Each day's candidate bar starts where its assigned bar ends.
    for day in range(3):
        assigned, candidate = (b for b in bottoms if abs(b[0] + 0.4 - day) < 1e-6)
        first, second = sorted([assigned, candidate], key=lambda b: b[1])
        assert second[1] == pytest.approx(first[2])


def test_a_panel_draws_only_the_rows_where_keeps():
    fig = specs.sticks()
    frame = fig.frame(fig.panels[1])
    assert set(frame["part"]) == {"difference"} and len(frame) == 8
    assert list(frame.index) == list(range(8))
    assert fig.panels[1].columns() == ["mz", "intensity", "formula", "only"]


def test_a_colours_option_names_a_groups_colour_by_token():
    fig = specs.scatter()
    for name in theme.THEMES:
        palette = theme.get(name)
        colours = common.colours(fig, palette)
        assert colours.groups["CHOS"] == palette.warning
        entries = {t.name: t for t in render.plotly(fig, theme=name).data if t.name}
        assert entries["CHOS"].marker.color == palette.warning


def test_top_n_labels_the_largest_on_both_backends():
    fig = specs.scatter()
    labelled = common.labelled(fig, fig.panels[0])
    assert list(labelled["text"]) == ["C6HxOy", "C10HxOy", "C4HxOy"]
    ax = render.matplotlib(fig).axes[0]
    assert {t.get_text() for t in ax.texts} >= {"C6HxOy", "C10HxOy", "C4HxOy"}
    plotted = render.plotly(fig)
    texts = next(t for t in plotted.data if t.mode == "text")
    assert list(texts.text) == ["C6HxOy", "C10HxOy", "C4HxOy"]
    sticks = specs.sticks()
    assert list(common.labelled(sticks, sticks.panels[0])["text"]) == [
        "m126",
        "m62",
        "m190",
        "m97",
    ]
    # A difference spectrum's tallest are the largest either way from zero.
    assert list(common.labelled(sticks, sticks.panels[1])["text"]) == [
        "m126",
        "m190",
        "m62",
    ]


def test_names_are_drawn_as_written_on_both_backends():
    """A name is data: plotly gets it HTML-escaped, matplotlib with its
    dollar signs escaped, so neither reads it as markup or mathtext."""
    fig = Figure(
        "names",
        "a $\\notacommand$ title <i>",
        "<a href='x'>link</a> & more",
        panels=[
            Panel(
                "series",
                encodings={"x": "x", "y": "y", "group": "who"},
                options={"rules": [{"at": 1.0, "label": "$\\bad$"}]},
            )
        ],
        data=pd.DataFrame(
            {"x": [0.0, 1.0, 2.0], "y": [1.0, 2.0, 3.0], "who": "<b>$x$"}
        ),
    )
    texts = _texts(render.matplotlib(fig))
    assert "a \\$\\notacommand\\$ title <i>" in texts
    assert _png(fig).startswith(b"\x89PNG")
    plotted = render.plotly(fig)
    assert "a $\\notacommand$ title &lt;i&gt;" in plotted.layout.title.text
    assert "&lt;a href='x'&gt;link&lt;/a&gt; &amp; more" in plotted.layout.title.text
    assert plotted.data[0].name == "&lt;b&gt;$x$"


# --- the theme -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", theme.THEMES)
def test_the_palettes_pass_the_contrast_check(name):
    palette = theme.get(name)
    assert theme.check(palette) == []
    for colour in palette.categorical:
        for ground in palette.grounds:
            assert theme.contrast(colour, ground) >= theme.GRAPHIC_CONTRAST


def test_the_contrast_check_finds_a_colour_that_misses():
    """The app's own Series 4 green reads 1.8:1 on the light ground: the
    check says so, which is why light darkens it."""
    from dataclasses import replace

    green = "#a6c33c"
    failing = replace(
        theme.LIGHT,
        categorical=(*theme.LIGHT.categorical[:3], green, *theme.LIGHT.categorical[4:]),
    )
    problems = theme.check(failing)
    assert problems and all("categorical 4" in p for p in problems)
    twin = replace(
        theme.DARK, categorical=(*theme.DARK.categorical, theme.DARK.categorical[0])
    )
    assert any("are 0.0 apart" in p for p in theme.check(twin))
    flat = replace(
        theme.LIGHT,
        sequential=theme.LIGHT.sequential[::-1][:3] + theme.LIGHT.sequential[3:],
    )
    assert any("monotonic" in p for p in theme.check(flat))


def test_the_first_five_are_the_apps_series_palette():
    """docs/dev/frontend_design_system.md, section 3: the accent, the info
    teal, then the guide's Series 3 to 5; light darkens Series 4."""
    assert theme.LIGHT.categorical[:3] == ("#a23f00", "#0b7e8e", "#8266ea")
    assert theme.DARK.categorical[:5] == (
        "#dd743b",
        "#16b0c4",
        "#8266ea",
        "#a6c33c",
        "#c93a82",
    )
    assert theme.LIGHT.categorical[4] == "#c93a82"


def test_the_renderers_draw_in_the_themes_values():
    for name in theme.THEMES:
        palette = theme.get(name)
        plotted = render.plotly(specs.series(), theme=name)
        assert plotted.layout.plot_bgcolor == palette.plot
        assert plotted.layout.font.color == palette.text
        assert list(plotted.layout.colorway) == list(palette.categorical)
        drawn = render.matplotlib(specs.series(), theme=name)
        from matplotlib.colors import to_hex

        assert to_hex(drawn.get_facecolor()) == palette.paper
        assert to_hex(drawn.axes[0].get_facecolor()) == palette.plot


# --- determinism and the document ---------------------------------------------------------------

_SAVE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {tests!r})
    from fixtures import specs
    from mascope_viz import render
    for name in ("png", "svg", "pdf", "html"):
        render.save(specs.everything(), {out!r} + "/everything." + name, theme="dark")
    """
)


def test_a_saved_figure_is_the_same_bytes_on_every_run(tmp_path):
    env = dict(os.environ, SOURCE_DATE_EPOCH="1767225600", PYTHONHASHSEED="random")
    outputs = []
    for run in ("one", "two"):
        out = tmp_path / run
        out.mkdir()
        script = _SAVE.format(tests=str(TESTS), out=str(out))
        subprocess.run([sys.executable, "-c", script], check=True, env=env)
        outputs.append(out)
    for name in ("png", "svg", "pdf", "html"):
        first = (outputs[0] / f"everything.{name}").read_bytes()
        assert first == (outputs[1] / f"everything.{name}").read_bytes(), name


@pytest.mark.parametrize("name", ALL)
def test_a_spec_round_trips_through_json_and_renders_the_same(name):
    fig = _figure(name)
    document = fig.to_json()
    parsed = Figure.from_json(document)
    assert parsed.to_json() == document
    pd.testing.assert_frame_equal(parsed.data, fig.data, check_exact=True)
    assert parsed.data.dtypes.to_dict() == fig.data.dtypes.to_dict()
    assert _png(parsed) == _png(fig)
    assert render.plotly(parsed).to_json() == render.plotly(fig).to_json()


def test_the_document_says_what_each_panel_draws():
    document = json.loads(specs.series().to_json())
    assert document["figure_spec_version"] == FIGURE_SPEC_VERSION == 1
    panel = document["panels"][0]
    assert panel["primitive"] == "series"
    assert panel["encodings"] == {
        "x": "t",
        "y": "value",
        "group": "key",
        "name": "name",
        "lo": "lo",
        "hi": "hi",
    }
    assert panel["axes"]["y"] == {
        "label": "Intensity (counts)",
        "scale": "log",
        "range": None,
    }
    assert panel["options"]["mode"] == "marks"
    assert panel["options"]["spans"][0] == {
        "start": "2026-07-01T06:00:00+03:00",
        "end": "2026-07-01T10:00:00+03:00",
        "label": "Episode 1",
        "kind": "event",
    }
    columns = {c["name"]: c for c in document["data"]["columns"]}
    assert columns["t"]["type"] == "time" and columns["t"]["tz"] == specs.ZONE
    assert columns["t"]["values"][0] == "2026-07-01T00:00:00+03:00"
    assert columns["key"]["type"] == "category"
    assert columns["key"]["categories"] == specs.KEYS
    assert columns["value"]["values"][20] is None
    assert columns["n"]["type"] == "integer"
    colours = json.loads(specs.scatter().to_json())["panels"][1]["options"]["colours"]
    assert colours == {"CHOS": "warning"}


def test_every_column_type_round_trips():
    data = pd.DataFrame(
        {
            "utc": pd.to_datetime(["2026-07-01T00:00:00.000000001Z", None], utc=True),
            "naive": pd.to_datetime(["2026-07-01T12:00", "2026-07-02T12:00"]),
            "float": [1.5, np.nan],
            "inf": [np.inf, -1.0],
            "int": np.array([1, 2], dtype="int64"),
            "nullable": pd.array([1, None], dtype="Int64"),
            "flag": [True, False],
            "maybe": pd.array([True, None], dtype="boolean"),
            "objects": pd.Series([True, None], dtype=object),
            "text": ["a", None],
            "cat": pd.Categorical(["b", None], categories=["b", "a"], ordered=True),
        }
    )
    fig = Figure(
        "types",
        "Types",
        panels=[Panel("table", encodings={"columns": list(data.columns)})],
        data=data,
    )
    assert np.isnan(fig.data.loc[0, "inf"])
    assert str(fig.data["objects"].dtype) == "boolean"
    parsed = Figure.from_json(fig.to_json())
    pd.testing.assert_frame_equal(parsed.data, fig.data, check_exact=True)
    assert parsed.data["utc"].iloc[0].nanosecond == 1
    assert list(parsed.data["cat"].cat.categories) == ["b", "a"]
    assert parsed.data["cat"].cat.ordered


def test_the_summary_is_plain_json():
    fig = Figure(
        "s",
        "S",
        panels=[Panel("table", encodings={"columns": ["a"]})],
        summary={
            "n": np.int64(3),
            "median": np.float64(1.5),
            "missing": np.nan,
            "at": pd.Timestamp("2026-07-01", tz="UTC"),
            "keys": ("a", "b"),
        },
        data=pd.DataFrame({"a": [1]}),
    )
    assert fig.summary == {
        "n": 3,
        "median": 1.5,
        "missing": None,
        "at": "2026-07-01T00:00:00+00:00",
        "keys": ["a", "b"],
    }
    assert json.loads(fig.to_json())["summary"] == fig.summary


# --- the import boundary --------------------------------------------------------------------------

_WITHOUT_RENDERERS = textwrap.dedent(
    """
    import sys

    class Absent:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ("matplotlib", "plotly"):
                raise ModuleNotFoundError(f"No module named {{name!r}}")
            return None

    sys.meta_path.insert(0, Absent())
    sys.path.insert(0, {tests!r})
    import mascope_viz.data, mascope_viz.charts
    from fixtures import specs
    document = specs.everything().to_json()
    assert mascope_viz.charts.Figure.from_json(document).to_json() == document
    import mascope_viz.render
    assert mascope_viz.render.theme.check("dark") == []
    try:
        mascope_viz.render.plotly(specs.series())
    except ModuleNotFoundError as e:
        assert "plotly" in str(e)
    else:
        raise AssertionError("drew without plotly")
    loaded = sorted(n for n in sys.modules if n.split(".")[0] in ("matplotlib", "plotly"))
    assert not loaded, loaded
    print("ok")
    """
)


def test_the_data_layer_and_the_charts_import_without_the_renderers():
    """The server imports mascope_viz.data and mascope_viz.charts, never the
    renderers (docs/dev/visual_digest.md, section 2.8): both import, and
    build and read a spec, with matplotlib and plotly absent."""
    result = subprocess.run(
        [sys.executable, "-c", _WITHOUT_RENDERERS.format(tests=str(TESTS))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_the_render_functions_keep_their_names_after_the_modules_import():
    import mascope_viz.render.mpl  # noqa: F401
    import mascope_viz.render.plotly  # noqa: F401

    assert callable(render.plotly) and callable(render.matplotlib)
    assert render.plotly(specs.box()).layout.boxmode == "overlay"


# --- save -----------------------------------------------------------------------------------------


def test_save_writes_by_extension(tmp_path):
    fig = specs.series()
    assert render.save(fig, tmp_path / "a.png").read_bytes().startswith(b"\x89PNG")
    assert b"<svg" in render.save(fig, tmp_path / "a.svg").read_bytes()[:400]
    assert render.save(fig, tmp_path / "a.pdf").read_bytes().startswith(b"%PDF")
    page = render.save(fig, tmp_path / "a.html").read_text()
    assert 'id="figure-species_series"' in page and "Plotly.newPlot" in page
    assert 'src="https://cdn.plot.ly/' not in page
    small = render.save(fig, tmp_path / "b.html", plotlyjs="cdn").read_text()
    assert 'src="https://cdn.plot.ly/' in small and len(small) < len(page) / 10
    document = render.save(fig, tmp_path / "a.json").read_text()
    assert Figure.from_json(document).to_json() == fig.to_json()
    with pytest.raises(ValueError, match="the formats are"):
        render.save(fig, tmp_path / "a.jpg")


# --- the raster mode, notes, tables ------------------------------------------------------------------


def test_the_raster_mode_falls_back_to_marks_and_says_so():
    fig = specs.heatmap()
    assert fig.panels[0].options["mode"] == "raster"
    assert common.drawn_mode(fig.panels[0]) == "marks"
    note = common.RASTER_NOTE.format(where="Panel 1 (Time by m/z)")
    assert common.notes(fig) == [note]
    drawn = render.matplotlib(fig)
    assert any(isinstance(c, QuadMesh) for c in drawn.axes[0].get_children())
    assert any("asks to be rasterised" in t for t in _texts(drawn))
    plotted = render.plotly(fig)
    assert plotted.data[0].type == "heatmap"
    assert any("asks to be rasterised" in a.text for a in plotted.layout.annotations)


def test_a_table_formats_its_columns_and_shows_max_rows():
    fig = specs.table()
    table = common.table(fig, fig.panels[0])
    assert table.headers == ["name", "Median", "Last / first", "n", "First seen", "new"]
    assert table.rows[0] == [
        "Malonic acid",
        "1.23e+04",
        "150%",
        "120",
        "2026-07-01 08:00",
        "no",
    ]
    assert table.rows[3][1] == ""
    assert len(table.rows) == 4 and table.total == 6
    assert "Panel 1 shows the first 4 of 6 rows." in common.notes(fig)
    cells = render.plotly(fig).data[0].cells.values
    assert list(cells[0]) == [
        "Malonic acid",
        "C3H4O4 $I$ &lt;b&gt;",
        "Nitrate",
        "Iodide",
    ]


def test_the_colour_scales_match_on_both_backends():
    fig = specs.heatmap()
    plotted = render.plotly(fig)
    spectrogram, weekly = (
        t for t in plotted.data if t.type == "heatmap" and t.showscale
    )
    scale = common.colour_scale(
        common.grid(fig, fig.panels[0]).values.ravel(), fig.panels[0], theme.LIGHT
    )
    assert spectrogram.zmin == pytest.approx(scale.low)
    assert list(spectrogram.colorbar.ticktext) == [text for _, text in scale.ticks]
    # The diverging scale stands evenly around its centre.
    assert weekly.zmin == pytest.approx(-weekly.zmax)
    meshes = [
        c
        for ax in render.matplotlib(fig).axes
        if ax.get_label() != "<colorbar>"
        for c in ax.get_children()
        if isinstance(c, QuadMesh) and c.get_cmap().name == "mascope_viz"
    ]
    assert len(meshes) == 2
    assert meshes[0].norm.vmin == pytest.approx(scale.low)
    assert meshes[1].norm.vmin == pytest.approx(-meshes[1].norm.vmax)


# --- the spec refuses what it cannot draw --------------------------------------------------------------


def _table_data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": pd.date_range("2026-07-01", periods=3, freq="1h", tz="UTC"),
            "x": [1.0, 2.0, 3.0],
            "label": ["a", "b", "c"],
        }
    )


@pytest.mark.parametrize(
    "panel, message",
    [
        (dict(primitive="pie"), "is none of series"),
        (dict(primitive="series", encodings={"x": "t"}), "needs the encoding y"),
        (dict(primitive="series", encodings={"x": "t", "y": "nope"}), "does not have"),
        (
            dict(primitive="scatter", encodings={"x": "t", "y": "x"}),
            "draws a number column; 't' is time",
        ),
        (
            dict(primitive="series", encodings={"x": "t", "y": "x", "colour": "x"}),
            "takes no encoding colour",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": "x"},
                options={"wobble": 1},
            ),
            "takes no option wobble",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": "x"},
                options={"colours": {"a": "#ff0000"}},
            ),
            "is no colour token",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": "x"},
                options={"mode": "pixels"},
            ),
            "is none of marks, raster",
        ),
        (
            dict(
                primitive="sticks",
                encodings={"x": "x", "y": "x"},
                axes={"y": {"scale": "log"}},
            ),
            "rise from zero",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": "x"},
                axes={"x": {"range": (0, 1)}},
            ),
            "is time",
        ),
        (
            dict(
                primitive="series", encodings={"x": "t", "y": "x"}, where={"t": "2026"}
            ),
            "'t' is time",
        ),
        (
            dict(primitive="series", encodings={"x": "t", "y": "x", "lo": "x"}),
            "lo and hi come together",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": ["x", "x"], "group": "label"},
            ),
            "a list of y",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": "x"},
                options={"spans": [{"start": "2026-07-02", "end": "2026-07-01"}]},
            ),
            "not before its end",
        ),
        (
            dict(
                primitive="table",
                encodings={"columns": ["x"]},
                options={"formats": {"x": "%Y"}},
            ),
            "not a number format",
        ),
        (
            dict(
                primitive="bars",
                encodings={"x": "label", "y": "x", "lo": "x", "hi": "x"},
                options={"stack": True},
            ),
            "stacked bars",
        ),
    ],
)
def test_the_spec_refuses_what_it_cannot_draw(panel, message):
    with pytest.raises((ValueError, TypeError), match=message):
        Figure("bad", "Bad", panels=[Panel(**panel)], data=_table_data())


def test_panels_share_their_x_axis_on_both_backends():
    fig = specs.sticks()
    assert fig.share_x is True
    first, second = (
        ax for ax in render.matplotlib(fig).axes if ax.get_label() != "<colorbar>"
    )
    assert first.get_shared_x_axes().joined(first, second)
    plotted = render.plotly(fig)
    assert plotted.layout.xaxis.matches == "x2" or plotted.layout.xaxis2.matches == "x"
    with pytest.raises(ValueError, match="share_x='yes' is not true or false"):
        Figure("s", "S", panels=fig.panels, data=fig.data, share_x="yes")


def test_a_document_of_a_later_version_is_refused():
    document = json.loads(specs.box().to_json())
    document["figure_spec_version"] = FIGURE_SPEC_VERSION + 1
    with pytest.raises(ValueError, match="Upgrade mascope_viz"):
        Figure.from_dict(document)
    del document["figure_spec_version"]
    with pytest.raises(ValueError, match="not a figure spec"):
        Figure.from_dict(document)
    document = json.loads(specs.box().to_json())
    document["panels"][0]["escape_hatch"] = {"plotly": {"marker": "x"}}
    with pytest.raises(ValueError, match="has no field escape_hatch"):
        Figure.from_dict(document)
    document = json.loads(specs.box().to_json())
    document["data"]["columns"][0]["values"].append("W31")
    with pytest.raises(ValueError, match="the data has 8 rows"):
        Figure.from_dict(document)


def test_span_times_are_read_in_the_axis_zone():
    fig = Figure(
        "zone",
        "Zone",
        panels=[
            Panel(
                "series",
                encodings={"x": "t", "y": "x"},
                options={
                    "spans": [
                        {"start": "2026-07-01T00:30:00Z", "end": "2026-07-01 05:00"}
                    ]
                },
            )
        ],
        data=_table_data().assign(t=lambda f: f["t"].dt.tz_convert(specs.ZONE)),
    )
    span = fig.panels[0].options["spans"][0]
    assert span["start"].isoformat() == "2026-07-01T03:30:00+03:00"
    assert span["end"].isoformat() == "2026-07-01T05:00:00+03:00"


# --- the owner's notes of 2026-11-21: the reagent sum and the tier-1 charts -----------


def _subtitle_text(drawn):
    """The matplotlib figure's subtitle: the header text drawn in the
    muted colour."""
    from matplotlib.colors import to_hex

    muted = {theme.LIGHT.muted, theme.DARK.muted}
    return next(
        t for t in drawn.texts if t.get_text() and to_hex(t.get_color()) in muted
    )


def test_a_reagent_sum_round_trips_and_draws_on_both_backends():
    """The note's suggested check: a list for normalise, a three-member
    name, a '+'-joined key and two panels of different y scale round-trip
    through JSON and draw on both backends, the subtitle whole in each."""
    fig = specs.reagent_sum()
    document = json.loads(fig.to_json())
    assert document["context"]["normalise"] == specs.REAGENTS
    assert document["context"]["normalise_name"] == specs.SUM_NAME
    assert specs.SUM_KEY in document["data"]["columns"][1]["categories"]
    assert [p["axes"]["y"]["scale"] for p in document["panels"]] == ["linear", "log"]
    parsed = Figure.from_json(fig.to_json())
    assert parsed == fig
    assert _png(parsed) == _png(fig)
    assert render.plotly(parsed).to_json() == render.plotly(fig).to_json()
    assert specs.SUM_NAME in fig.subtitle

    drawn = render.matplotlib(fig)
    text = _subtitle_text(drawn)
    lines = text.get_text().split("\n")
    assert len(lines) > 1
    assert " ".join(lines) == fig.subtitle
    # Measured where the layout puts things, as a save would.
    drawn.draw_without_rendering()
    renderer = drawn._get_renderer()
    box = text.get_window_extent(renderer)
    legend = drawn.legends[0].get_window_extent(renderer)
    assert box.x1 < legend.x0, "the subtitle runs under the legend"
    assert box.x1 <= drawn.bbox.x1
    # The layout leaves the subtitle its room: it ends above every panel,
    # in a figure as tall as its header of that many lines makes it.
    tops = [ax.get_tightbbox(renderer).y1 for ax in drawn.axes]
    assert box.y0 > max(tops)
    total = common.arrange(fig, theme.LIGHT, subtitle=len(lines)).total
    assert drawn.get_size_inches()[1] * 96 == pytest.approx(total)
    assert len(lines) > len(common.subtitle_lines(fig, theme.LIGHT))
    axes = [ax for ax in drawn.axes if ax.get_label() != "<colorbar>"]
    assert ax_scales(axes) == ["linear", "log"]

    plotted = render.plotly(fig)
    title = plotted.layout.title.text
    parts = [
        part.split(">", 1)[1].rsplit("</span>", 1)[0]
        for part in title.split("<br>")[1:]
    ]
    assert len(parts) > 1
    assert " ".join(parts) == fig.subtitle.replace("<", "&lt;").replace(">", "&gt;")
    chars = int(
        (theme.LIGHT.layout.width - common.TEXT_MARGINS)
        / (theme.LIGHT.type.subtitle * common.CHAR_WIDTH)
    )
    assert all(len(part) <= chars for part in parts)
    assert plotted.layout.yaxis.type is None and plotted.layout.yaxis2.type == "log"
    assert plotted.layout.xaxis.matches == "x2" or plotted.layout.xaxis2.matches == "x"


def ax_scales(axes) -> list[str]:
    return [ax.get_yscale() for ax in axes]


def test_a_long_subtitle_grows_the_header_on_both_backends():
    short = specs.reagent_sum()
    short.subtitle = "one line"
    fig = specs.reagent_sum()
    lines = len(common.subtitle_lines(fig, theme.LIGHT))
    assert lines >= 2
    grown = (lines - 1) * theme.LIGHT.type.subtitle * common.LINE_HEIGHT
    assert common.arrange(fig, theme.LIGHT).header == pytest.approx(
        common.arrange(short, theme.LIGHT).header + grown
    )
    assert render.plotly(fig).layout.margin.t > render.plotly(short).layout.margin.t
    taller = render.matplotlib(fig).get_size_inches()[1]
    assert taller > render.matplotlib(short).get_size_inches()[1]
    # A word longer than a line is broken inside, never cut off.
    long = "x" * 300
    assert "".join(common.wrap(long, 13, theme.LIGHT)) == long


def test_a_legend_names_a_series_by_its_display_name_never_its_key():
    fig = specs.reagent_sum()
    legend = render.matplotlib(fig).legends[0]
    texts = [t.get_text() for t in legend.get_texts()]
    assert specs.SUM_NAME in texts
    assert not any(key in text for text in texts for key in specs.REAGENTS)
    plotted = render.plotly(fig)
    shown = {t.name: t for t in plotted.data if t.showlegend}
    assert shown[specs.SUM_NAME].legendgroup == specs.SUM_KEY
    assert not any(
        t.name and t.name.startswith(("ion:", "formula:")) for t in plotted.data
    )


def test_a_list_of_y_takes_its_names_from_the_option():
    data = pd.DataFrame({"t": [0.0, 1.0], "a": [1.0, 2.0], "b": [2.0, 1.0]})
    fig = Figure(
        "names",
        "Names",
        panels=[
            Panel(
                "series",
                encodings={"x": "t", "y": ["a", "b"]},
                options={"names": {"a": "First"}},
            )
        ],
        data=data,
    )
    legend = render.matplotlib(fig).legends[0]
    assert [t.get_text() for t in legend.get_texts()] == ["First", "b"]
    assert [t.name for t in render.plotly(fig).data if t.showlegend] == ["First", "b"]


def _categorical_figure() -> Figure:
    order = ["Mon", "Tue", "Wed"]
    days = pd.Categorical(["Wed", "Mon", "Tue"], categories=order, ordered=True)
    data = pd.DataFrame(
        {
            "day": days,
            "count": [3.0, 1.0, 2.0],
            "t": pd.date_range("2026-07-01", periods=3, freq="1h", tz="UTC"),
            "hour": [0, 1, 2],
        }
    )
    return Figure(
        "order",
        "Order",
        panels=[
            Panel("bars", encodings={"x": "day", "y": "count"}),
            Panel("strip", encodings={"x": "t", "y": "day"}),
            Panel("heatmap", encodings={"x": "hour", "y": "day", "value": "count"}),
        ],
        data=data,
    )


def test_the_renderers_draw_a_categoricals_values_in_their_order():
    fig = _categorical_figure()
    order = ["Mon", "Tue", "Wed"]
    assert common.places(fig, fig.panels[0], theme.LIGHT).categories == order
    assert common.strip(fig, fig.panels[1], theme.LIGHT).rows == order
    assert common.grid(fig, fig.panels[2]).ys == order
    bars, strip, heatmap = (
        ax for ax in render.matplotlib(fig).axes if ax.get_label() != "<colorbar>"
    )
    assert [t.get_text() for t in bars.get_xticklabels()] == order
    assert [t.get_text() for t in strip.get_yticklabels()] == order
    assert [t.get_text() for t in heatmap.get_yticklabels()] == order
    plotted = render.plotly(fig)
    assert list(plotted.layout.xaxis.ticktext) == order
    assert list(plotted.layout.yaxis2.categoryarray) == order
    assert list(plotted.layout.yaxis3.categoryarray) == order
    bar = next(t for t in plotted.data if t.type == "bar")
    # Wed, the frame's first row, stands third.
    assert list(bar.x) == [0.0, 1.0, 2.0] and list(bar.y) == [1.0, 2.0, 3.0]


def test_share_x_joins_every_plotted_panel_on_both_backends():
    fig = specs.reagent_sum()
    fig = Figure.from_dict({**fig.to_dict(), "columns": 2})
    first, second = (
        ax for ax in render.matplotlib(fig).axes if ax.get_label() != "<colorbar>"
    )
    assert first.get_shared_x_axes().joined(first, second)
    layout = render.plotly(fig).layout
    assert {layout.xaxis.matches, layout.xaxis2.matches} - {None} in ({"x"}, {"x2"})


# --- bugs of round 1 -----------------------------------------------------------------------


def test_a_float_count_formatted_d_draws_rounded():
    data = pd.DataFrame({"v": [1.0, 2.5, np.nan, 1234.0]})
    fig = Figure(
        "d",
        "D",
        panels=[
            Panel(
                "table",
                encodings={"columns": ["v", "v"]},
                options={"formats": {"v": ",d"}},
            )
        ],
        data=data,
    )
    table = common.table(fig, fig.panels[0])
    assert [row[0] for row in table.rows] == ["1", "2", "", "1,234"]
    assert list(render.plotly(fig).data[0].cells.values[0]) == ["1", "2", "", "1,234"]
    assert _png(fig).startswith(b"\x89PNG")


def test_an_interval_that_misses_the_bar_draws_the_same_on_both_backends():
    data = pd.DataFrame(
        {
            "x": ["a", "b", "c", "d"],
            "y": [1.0, 2.0, 1.0, 1.0],
            "lo": [1.5, 1.0, np.nan, 3.0],
            "hi": [2.0, 3.0, 2.0, 2.5],
        }
    )
    fig = Figure(
        "b",
        "B",
        panels=[Panel("bars", encodings={"x": "x", "y": "y", "lo": "lo", "hi": "hi"})],
        data=data,
    )
    spans = common.intervals(common.bars(fig, fig.panels[0], theme.LIGHT)[1])
    # c has no lo, so no interval; d's lo is above its hi.
    assert list(spans["middle"] - spans["half"]) == [1.5, 1.0, 2.5]
    assert list(spans["middle"] + spans["half"]) == [2.0, 3.0, 3.0]
    drawn = render.matplotlib(fig)
    segments = [
        sorted(y for _, y in segment)
        for c in drawn.axes[0].collections
        for segment in c.get_segments()
    ]
    assert segments == [[1.5, 2.0], [1.0, 3.0], [2.5, 3.0]]
    assert _png(fig).startswith(b"\x89PNG")
    error = next(t for t in render.plotly(fig).data if t.type == "scatter")
    ends = [(m - h, m + h) for m, h in zip(error.y, error.error_y.array, strict=True)]
    assert ends == [(1.5, 2.0), (1.0, 3.0), (2.5, 3.0)]
    assert error.error_y.symmetric is True


def test_a_bar_without_a_height_is_not_drawn():
    data = pd.DataFrame({"x": ["a", "b", "c"], "y": [1.0, np.nan, 3.0]})
    fig = Figure(
        "b", "B", panels=[Panel("bars", encodings={"x": "x", "y": "y"})], data=data
    )
    assert list(common.bars(fig, fig.panels[0], theme.LIGHT)[1]["category"]) == [
        "a",
        "c",
    ]
    assert list(render.plotly(fig).data[0].x) == [0.0, 2.0]
    assert len(render.matplotlib(fig).axes[0].patches) == 2


def test_whiskers_end_at_lo_and_hi_on_both_backends():
    fig = specs.box()
    place, boxes = common.boxes(fig, fig.panels[0], theme.LIGHT)
    ends = sorted(boxes["lo"]) + sorted(boxes["hi"])
    ax = render.matplotlib(fig).axes[0]
    whiskers = [
        line.get_ydata()
        for line in ax.get_lines()
        if len(line.get_ydata()) == 2 and line.get_ydata()[0] != line.get_ydata()[1]
    ]
    drawn = sorted(y[1] for y in whiskers if y[1] < y[0]) + sorted(
        y[1] for y in whiskers if y[1] > y[0]
    )
    assert drawn == pytest.approx(ends)
    traces = [t for t in render.plotly(fig).data if t.type == "box"]
    assert sorted(v for t in traces for v in t.lowerfence) == pytest.approx(
        sorted(boxes["lo"])
    )
    assert sorted(v for t in traces for v in t.upperfence) == pytest.approx(
        sorted(boxes["hi"])
    )


def test_a_box_with_few_samples_is_hollow_on_both_backends():
    from matplotlib.colors import to_rgba

    fig = specs.box()
    place, boxes = common.boxes(fig, fig.panels[0], theme.LIGHT)
    assert boxes["hollow"].tolist().count(True) == 1
    ax = render.matplotlib(fig).axes[0]
    fills = [
        to_rgba(c.get_facecolor())[3] for c in ax.patches if isinstance(c, PathPatch)
    ]
    assert fills.count(0.0) == 1 and len(fills) == 7
    plotted = render.plotly(fig)
    hollow = [
        t for t in plotted.data if t.type == "box" and t.fillcolor == "rgba(0,0,0,0)"
    ]
    assert len(hollow) == 1 and len(hollow[0].x) == 1
    assert hollow[0].showlegend is False
    shown = [t.name for t in plotted.data if t.showlegend]
    assert shown[-1] == "fewer than 10 samples"
    legend = render.matplotlib(fig).legends[0]
    assert legend.get_texts()[-1].get_text() == "fewer than 10 samples"


def test_a_heatmap_draws_a_gap_and_a_blank_cell_apart_on_both_backends():
    fig = specs.heatmap()
    # A gap cell is a gap whatever its value.
    fig.data.loc[fig.data["empty"], "intensity"] = 5.0
    fig = Figure.from_json(fig.to_json())
    cells = common.grid(fig, fig.panels[0])
    assert np.isnan(cells.values[cells.gaps]).all()
    gap_column = cells.gaps.any(axis=0)
    assert gap_column.sum() == 1 and cells.gaps[:, gap_column].all()
    blank = np.isnan(cells.values) & ~cells.gaps
    assert blank.sum() == 1
    ax = render.matplotlib(fig).axes[0]
    meshes = [c for c in ax.collections if isinstance(c, QuadMesh)]
    gap_mesh = next(m for m in meshes if m.get_cmap().name != "mascope_viz")
    drawn_gaps = ~np.ma.getmaskarray(gap_mesh.get_array()).reshape(cells.gaps.shape)
    assert (drawn_gaps == cells.gaps).all()
    plotted = render.plotly(fig)
    gap_trace = next(t for t in plotted.data if t.type == "heatmap" and not t.showscale)
    assert (np.array(gap_trace.z, dtype=float) == 1).sum() == cells.gaps.sum()
    assert "no samples" in [t.name for t in plotted.data if t.showlegend]


def test_sticks_by_sign_take_the_diverging_ends_on_both_backends():
    fig = specs.sticks()
    for name in theme.THEMES:
        palette = theme.get(name)
        sticks = common.sticks(
            fig, fig.panels[1], palette, common.colours(fig, palette)
        )
        up = sticks["y"] >= 0
        assert set(sticks.loc[up, "colour"]) == {palette.diverging[-1]}
        assert set(sticks.loc[~up, "colour"]) == {palette.diverging[0]}
        plotted = render.plotly(fig, theme=name)
        lines = [t for t in plotted.data if t.mode == "lines" and t.xaxis == "x2"]
        colours = {t.line.color: sum(v is not None for v in t.y) // 2 for t in lines}
        assert colours == {
            palette.diverging[-1]: int(up.sum()),
            palette.diverging[0]: int((~up).sum()),
        }
    from matplotlib.colors import to_hex

    light = common.sticks(
        fig, fig.panels[1], theme.LIGHT, common.colours(fig, theme.LIGHT)
    )
    ax = [a for a in render.matplotlib(fig).axes if a.get_label() != "<colorbar>"][1]
    drawn = [to_hex(c) for c in ax.collections[0].get_colors()]
    assert drawn == list(light["colour"])


def test_a_marked_stick_is_marked_at_its_tip_on_both_backends():
    fig = specs.sticks()
    tips = fig.frame(fig.panels[1]).query("only")[["mz", "intensity"]].to_numpy()
    ax = [a for a in render.matplotlib(fig).axes if a.get_label() != "<colorbar>"][1]
    marks = next(line for line in ax.get_lines() if line.get_marker() == "D")
    assert np.column_stack([marks.get_xdata(), marks.get_ydata()]) == pytest.approx(
        tips
    )
    plotted = render.plotly(fig)
    trace = next(t for t in plotted.data if t.mode == "markers" and t.name)
    assert trace.name == "only in one period" and trace.marker.symbol == "diamond"
    assert np.column_stack([trace.x, trace.y]) == pytest.approx(tips)


def test_a_markers_area_is_in_proportion_to_its_value():
    marks = theme.LIGHT.marks
    values = np.array([100.0, 25.0, 1.0, 0.0, np.nan, -3.0])
    sizes = common.sizes(values, theme.LIGHT)
    assert sizes[0] == marks.size_max
    assert sizes[1] == pytest.approx(marks.size_max / 2)
    # Too small to see, missing, zero or negative: the smallest size.
    assert list(sizes[2:]) == [marks.size_min] * 4
    legend = common.size_legend(np.array([80000.0, 3.0]), theme.LIGHT)
    assert [value for value, _ in legend] == [50000.0, 10000.0]
    assert legend[0][1] == pytest.approx(marks.size_max * np.sqrt(50000 / 80000))
    assert common.size_legend(np.array([np.nan, -1.0]), theme.LIGHT) == []


def test_scatter_draws_the_larger_behind_on_both_backends():
    fig = specs.scatter()
    points = common.points(
        fig, fig.panels[0], theme.LIGHT, common.colours(fig, theme.LIGHT)
    )
    diameters = points.points["diameter"].to_numpy()
    assert (np.diff(diameters) <= 0).all() and diameters[0] > diameters[-1]
    ax = render.matplotlib(fig).axes[0]
    drawn = ax.collections[0].get_sizes()
    assert (np.diff(drawn) <= 0).all()
    alphas = ax.collections[0].get_facecolors()[:, 3]
    assert alphas == pytest.approx([theme.LIGHT.marks.point_opacity] * len(alphas))
    trace = render.plotly(fig).data[0]
    assert list(trace.marker.size) == pytest.approx(list(diameters))
    assert all(
        c.endswith(f",{theme.LIGHT.marks.point_opacity})") for c in trace.marker.color
    )


def test_hollow_points_are_drawn_as_rings_on_both_backends():
    from matplotlib.colors import to_hex

    fig = specs.scatter()
    panel = fig.panels[1]
    points = common.points(fig, panel, theme.LIGHT, common.colours(fig, theme.LIGHT))
    hollow = points.points["hollow"].to_numpy()
    assert hollow.sum() == 3
    ax = [a for a in render.matplotlib(fig).axes if a.get_label() != "<colorbar>"][1]
    scatter = ax.collections[0]
    faces = scatter.get_facecolors()
    assert (faces[hollow][:, 3] == 0).all() and (faces[~hollow][:, 3] > 0).all()
    edges = [to_hex(e) for e in scatter.get_edgecolors()[hollow]]
    assert edges == list(points.points.loc[hollow, "colour"])
    rings = np.asarray(scatter.get_linewidths())
    assert (rings[hollow] > 0).all() and (rings[~hollow] == 0).all()
    trace = next(t for t in render.plotly(fig).data if t.xaxis == "x2")
    widths = np.array(trace.marker.line.width)
    assert (widths[hollow] > 0).all() and (widths[~hollow] == 0).all()
    assert [trace.marker.color[i] for i in np.flatnonzero(hollow)] == [
        "rgba(0,0,0,0)"
    ] * 3
    shown = [t.name for t in render.plotly(fig).data if t.showlegend]
    assert "candidate" in shown


def test_scatter_hover_names_the_point_and_its_group():
    fig = specs.scatter()
    trace = render.plotly(fig).data[0]
    labelled = common.points(
        fig, fig.panels[0], theme.LIGHT, common.colours(fig, theme.LIGHT)
    )
    assert list(trace.text) == list(labelled.points["text"])
    assert "C6HxOy" in trace.text and "x+y+text" == trace.hoverinfo
    second = next(
        t
        for t in render.plotly(fig).data
        if t.xaxis == "x2" and t.mode == "markers" and len(t.x) > 1
    )
    assert set(second.text) == {"CHO", "CHON", "CHOS"}


def test_a_strip_draws_no_cell_for_an_hour_without_samples():
    fig = specs.strip()
    strip = common.strip(fig, fig.panels[0], theme.LIGHT)
    samples = fig.frame(fig.panels[0])["samples"].to_numpy()
    drawn = strip.cells["colour"].notna().to_numpy()
    # The fixture's hours 10 to 13 have no samples in either row.
    assert (drawn == (samples > 0)).all() and (~drawn).sum() >= 8
    ax = render.matplotlib(fig).axes[0]
    cells = sum(
        len(c.get_paths()) for c in ax.collections if isinstance(c, PolyCollection)
    )
    assert cells == drawn.sum()
    bar = next(t for t in render.plotly(fig).data if t.type == "bar")
    assert len(bar.x) == drawn.sum()


def test_a_strips_rows_run_top_to_bottom_on_both_backends():
    fig = specs.strip()
    ax = render.matplotlib(fig).axes[0]
    bottom, top = ax.get_ylim()
    assert top < bottom
    assert ax.get_yticklabels()[0].get_text() == "negative / mode A"
    assert render.plotly(fig).layout.yaxis.autorange == "reversed"


def test_a_strip_cell_is_as_wide_as_the_smallest_step():
    times = pd.to_datetime(
        ["2026-07-01T00:00", "2026-07-01T03:00", "2026-07-01T03:30"], utc=True
    )
    fig = Figure(
        "w",
        "W",
        panels=[Panel("strip", encodings={"x": "t", "y": "row"})],
        data=pd.DataFrame({"t": times, "row": ["a", "a", "a"]}),
    )
    assert common.strip(fig, fig.panels[0], theme.LIGHT).width == pd.Timedelta("30min")


def test_a_colour_range_sets_the_scale_on_both_backends():
    fig = specs.heatmap()
    document = fig.to_dict()
    document["panels"][0]["axes"]["colour"]["range"] = [10.0, 1e5]
    document["panels"][1]["axes"]["colour"]["range"] = [-2.0, 2.0]
    fig = Figure.from_dict(document)
    plotted = render.plotly(fig)
    spectrogram, weekly = (
        t for t in plotted.data if t.type == "heatmap" and t.showscale
    )
    assert (spectrogram.zmin, spectrogram.zmax) == (1.0, 5.0)
    assert (weekly.zmin, weekly.zmax) == (-2.0, 2.0)
    meshes = [
        c
        for ax in render.matplotlib(fig).axes
        if ax.get_label() != "<colorbar>"
        for c in ax.get_children()
        if isinstance(c, QuadMesh) and c.get_cmap().name == "mascope_viz"
    ]
    assert [(m.norm.vmin, m.norm.vmax) for m in meshes] == [(1.0, 5.0), (-2.0, 2.0)]


def test_a_log_colour_scale_ticks_twice_and_five_times_its_powers_when_few():
    """Two powers of ten or more inside: the powers alone; fewer: 2 and 5
    times them too."""
    assert [text for _, text in common._log_ticks(0.0, 3.0)] == [
        "1",
        "10",
        "100",
        "1000",
    ]
    assert [text for _, text in common._log_ticks(1.0, 2.0)] == ["10", "100"]
    assert [text for _, text in common._log_ticks(1.0, 1.9)] == ["10", "20", "50"]
    assert [text for _, text in common._log_ticks(1.1, 1.9)] == ["20", "50"]


def test_a_top_point_without_a_label_is_passed_over():
    data = pd.DataFrame(
        {"x": [1.0, 2.0, 3.0], "y": [3.0, 2.0, 1.0], "who": [None, "b", "c"]}
    )
    fig = Figure(
        "l",
        "L",
        panels=[
            Panel(
                "scatter",
                encodings={"x": "x", "y": "y", "label": "who"},
                options={"top_n": 2},
            )
        ],
        data=data,
    )
    assert list(common.labelled(fig, fig.panels[0])["text"]) == ["b"]


def test_the_first_panel_to_name_a_groups_colour_wins():
    data = pd.DataFrame({"x": ["a", "b"], "y": [1.0, 2.0]})
    panels = [
        Panel(
            "bars",
            encodings={"x": "x", "y": "y", "group": "x"},
            options={"colours": {"a": "error"}},
        ),
        Panel(
            "bars",
            encodings={"x": "x", "y": "y", "group": "x"},
            options={"colours": {"a": "success"}},
        ),
    ]
    fig = Figure("c", "C", panels=panels, data=data)
    assert common.colours(fig, theme.LIGHT).groups["a"] == theme.LIGHT.error


def test_a_table_shows_seconds_when_a_time_has_them():
    data = pd.DataFrame(
        {"t": pd.to_datetime(["2026-07-01T08:00:00", "2026-07-01T08:00:30"], utc=True)}
    )
    fig = Figure(
        "s", "S", panels=[Panel("table", encodings={"columns": ["t"]})], data=data
    )
    assert [row[0] for row in common.table(fig, fig.panels[0]).rows] == [
        "2026-07-01 08:00:00",
        "2026-07-01 08:00:30",
    ]


def test_a_table_is_as_tall_as_the_rows_it_shows():
    fig = specs.table()
    height = common.arrange(fig, theme.LIGHT).heights[0]
    layout = theme.LIGHT.layout
    assert height == layout.table_row * (4 + 1) + 12


def test_a_marked_row_is_shaded_on_both_backends():
    fig = specs.table()
    table = common.table(fig, fig.panels[0])
    assert table.marked == [False, True, False, False]
    assert (
        "Panel 1 shades the rows read differently after the run change."
        in common.notes(fig)
    )
    shade = theme.blend(
        theme.LIGHT.warning, theme.LIGHT.paper, theme.LIGHT.marks.span_opacity
    )
    fills = render.plotly(fig).data[0].cells.fill.color
    assert all(
        list(column) == [theme.LIGHT.paper, shade, theme.LIGHT.paper, theme.LIGHT.paper]
        for column in fills
    )
    from matplotlib.colors import to_hex

    drawn = next(
        c for c in render.matplotlib(fig).axes[0].get_children() if isinstance(c, Table)
    )
    assert to_hex(drawn[2, 0].get_facecolor()) == shade
    assert to_hex(drawn[1, 0].get_facecolor()) == theme.LIGHT.paper


@pytest.mark.parametrize(
    "change, problem",
    [
        (
            lambda t: {"sequential": (*t.sequential[:-1], t.plot)},
            "the sequential ramp's high end",
        ),
        (
            lambda t: {"sequential": (t.paper, *t.sequential[1:])},
            "the sequential ramp's low end",
        ),
        (
            lambda t: {"diverging": (t.plot, *t.diverging[1:])},
            "the diverging ramp's low end",
        ),
        (
            lambda t: {"diverging": (*t.diverging[:-1], t.plot)},
            "the diverging ramp's high end",
        ),
        (
            lambda t: {"diverging": (*t.diverging[:4], t.paper, *t.diverging[5:])},
            "the diverging ramp's centre",
        ),
        (
            lambda t: {"diverging": (t.diverging[3], *t.diverging[1:])},
            "does not move away from its centre",
        ),
    ],
)
def test_the_contrast_check_finds_each_kind_of_miss(change, problem):
    from dataclasses import replace

    for name in theme.THEMES:
        palette = theme.get(name)
        problems = theme.check(replace(palette, **change(palette)))
        assert any(problem in p for p in problems), problems


def test_sticks_by_group_are_coloured_and_named_on_both_backends():
    data = pd.DataFrame(
        {
            "mz": [62.0, 97.0, 126.9, 145.0],
            "y": [3.0, -1.0, 2.0, 1.0],
            "key": ["ion:1", "ion:2", "ion:1", None],
            "name": ["Nitrate", "Iodide", "Nitrate", None],
        }
    )
    fig = Figure(
        "s",
        "S",
        panels=[
            Panel(
                "sticks",
                encodings={"x": "mz", "y": "y", "group": "key", "name": "name"},
                options={"colours": {"ion:2": "error"}},
            )
        ],
        data=data,
    )
    colours = common.colours(fig, theme.LIGHT)
    sticks = common.sticks(fig, fig.panels[0], theme.LIGHT, colours)
    assert list(sticks["colour"]) == [
        theme.LIGHT.categorical[0],
        theme.LIGHT.error,
        theme.LIGHT.categorical[0],
        theme.LIGHT.muted,
    ]
    from matplotlib.colors import to_hex

    drawn = render.matplotlib(fig)
    assert [to_hex(c) for c in drawn.axes[0].collections[0].get_colors()] == list(
        sticks["colour"]
    )
    assert [t.get_text() for t in drawn.legends[0].get_texts()] == ["Nitrate", "Iodide"]
    plotted = render.plotly(fig)
    shown = {t.name: t.line.color for t in plotted.data if t.showlegend}
    assert shown == {"Nitrate": theme.LIGHT.categorical[0], "Iodide": theme.LIGHT.error}
    unnamed = [t for t in plotted.data if t.line.color == theme.LIGHT.muted]
    assert len(unnamed) == 1 and unnamed[0].showlegend is False


def test_a_point_without_a_colour_group_is_drawn_muted():
    data = pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0], "c": ["a", None]})
    fig = Figure(
        "p",
        "P",
        panels=[Panel("scatter", encodings={"x": "x", "y": "y", "colour": "c"})],
        data=data,
    )
    points = common.points(
        fig, fig.panels[0], theme.LIGHT, common.colours(fig, theme.LIGHT)
    )
    assert list(points.points["colour"]) == [
        theme.LIGHT.categorical[0],
        theme.LIGHT.muted,
    ]
    assert points.groups == ["a"]


def test_the_first_panel_to_name_a_group_names_it():
    data = pd.DataFrame(
        {"x": ["a", "a"], "y": [1.0, 2.0], "first": ["One", "Uno"], "part": [1, 2]}
    )
    fig = Figure(
        "n",
        "N",
        panels=[
            Panel(
                "bars",
                encodings={"x": "x", "y": "y", "group": "x", "name": "first"},
                where={"part": part},
            )
            for part in (1, 2)
        ],
        data=data,
    )
    assert common.colours(fig, theme.LIGHT).name("a") == "One"
    bar = render.plotly(fig).data[0]
    assert bar.name == "One" and bar.hovertemplate.endswith("<extra>One</extra>")


def test_the_size_legend_and_the_hollow_entry_on_both_backends():
    fig = specs.scatter()
    legend = render.matplotlib(fig).legends[0]
    texts = [t.get_text() for t in legend.get_texts()]
    assert texts == [
        "Median intensity",
        "50000",
        "10000",
        "CHO",
        "CHON",
        "CHOS",
        "candidate",
    ]
    plotted = render.plotly(fig)
    sizes = [t for t in plotted.data if t.legendgroup == "size:0"]
    assert [t.name for t in sizes] == ["50000", "10000"]
    assert sizes[0].legendgrouptitle.text == "Median intensity"
    assert [t.marker.size for t in sizes] == pytest.approx(
        [d for _, d in common.size_legend(fig.data["median"].to_numpy(), theme.LIGHT)]
    )


def test_blend_tints_a_colour_over_its_ground():
    assert theme.blend("#ffffff", "#000000", 0.5) == "#808080"
    assert theme.blend("#a23f00", "#ffffff", 0.0) == "#ffffff"
    assert theme.blend("#a23f00", "#ffffff", 1.0) == "#a23f00"


def test_a_flag_column_reads_a_missing_value_as_false():
    column = pd.Series([True, None, False], dtype="boolean")
    assert list(common.flags(column)) == [True, False, False]


def test_a_group_is_named_by_its_first_name_that_is_there():
    data = pd.DataFrame(
        {"x": ["a", "a", "b"], "y": [1.0, 2.0, 3.0], "name": [None, "Alpha", None]}
    )
    fig = Figure(
        "n",
        "N",
        panels=[
            Panel("bars", encodings={"x": "x", "y": "y", "group": "x", "name": "name"})
        ],
        data=data,
    )
    colours = common.colours(fig, theme.LIGHT)
    assert colours.name("a") == "Alpha"
    # A group without a name is called by its own value.
    assert colours.name("b") == "b"


def test_a_log_axis_range_is_drawn_in_its_powers_on_both_backends():
    data = pd.DataFrame({"x": [2.0, 20.0], "y": [1.0, 2.0]})
    fig = Figure(
        "r",
        "R",
        panels=[
            Panel(
                "scatter",
                axes={"x": {"scale": "log", "range": (1, 100)}},
                encodings={"x": "x", "y": "y"},
            )
        ],
        data=data,
    )
    assert list(render.plotly(fig).layout.xaxis.range) == [0.0, 2.0]
    assert render.matplotlib(fig).axes[0].get_xlim() == pytest.approx((1, 100))


# --- round 3: gap cells, y ranges, integer formats, NumPy times, the size legend, the header ----


def test_a_gap_cell_is_drawn_in_the_gap_cell_tint_on_both_backends():
    """A gap cell takes the gap token at the theme's gap cell opacity, which
    the contrast check holds apart from both ramps: on light grounds the
    span's light tint would read as the diverging ramp's "no change"."""
    fig = specs.heatmap()
    for name in theme.THEMES:
        palette = theme.get(name)
        ax = render.matplotlib(fig, theme=name).axes[0]
        meshes = [c for c in ax.collections if isinstance(c, QuadMesh)]
        gap_mesh = next(m for m in meshes if m.get_cmap().name != "mascope_viz")
        colour = gap_mesh.get_cmap().colors[0]
        assert theme.hex_colour(colour[:3]) == palette.token("gap")
        assert colour[3] == palette.marks.gap_cell_opacity
        plotted = render.plotly(fig, theme=name)
        gap_trace = next(
            t for t in plotted.data if t.type == "heatmap" and not t.showscale
        )
        r, g, b = (round(c * 255) for c in theme.rgb(palette.token("gap")))
        tint = f"rgba({r},{g},{b},{palette.marks.gap_cell_opacity})"
        assert [stop[1] for stop in gap_trace.colorscale] == [tint, tint]
    # The light theme's cell is not the span's tint, which would sit on the
    # diverging centre.
    assert theme.LIGHT.marks.gap_cell_opacity != theme.LIGHT.marks.gap_opacity
    centre = theme.LIGHT.diverging[len(theme.LIGHT.diverging) // 2]
    span_tint = theme.blend(
        theme.LIGHT.token("gap"), theme.LIGHT.plot, theme.LIGHT.marks.gap_opacity
    )
    assert theme.distance(span_tint, centre) < theme.APART
    assert theme.distance(theme.gap_cell(theme.LIGHT, theme.LIGHT.plot), centre) > 7


@pytest.mark.parametrize(
    "change, problem",
    [
        (
            lambda t: {
                "diverging": (
                    *t.diverging[:4],
                    theme.gap_cell(t, t.plot),
                    *t.diverging[5:],
                )
            },
            "from the diverging ramp's",
        ),
        (
            lambda t: {"sequential": (theme.gap_cell(t, t.plot), *t.sequential[1:])},
            "from the sequential ramp's",
        ),
        (
            lambda t: {"marks": theme.Marks(gap_cell_opacity=0.0)},
            "too close to tell from a blank cell",
        ),
    ],
)
def test_the_contrast_check_holds_a_gap_cell_apart(change, problem):
    from dataclasses import replace

    for name in theme.THEMES:
        palette = theme.get(name)
        problems = theme.check(replace(palette, **change(palette)))
        assert any(problem in p for p in problems), problems


def test_the_contrast_check_measures_a_gap_cell_along_a_ramp_not_at_its_stops():
    from dataclasses import replace

    cell = theme.gap_cell(theme.LIGHT, theme.LIGHT.plot)
    # Two stops each well apart from the cell, whose middle is the cell.
    low = theme.hex_colour(c - 24 / 255 for c in theme.rgb(cell))
    high = theme.hex_colour(c + 24 / 255 for c in theme.rgb(cell))
    assert min(theme.distance(cell, low), theme.distance(cell, high)) > theme.APART
    problems = theme.check(replace(theme.LIGHT, sequential=(low, high)))
    assert any(
        f"a gap cell {cell} on {theme.LIGHT.plot}" in p and "sequential" in p
        for p in problems
    ), problems
    assert len(theme.along(("#000000", "#ffffff"))) == theme.RAMP_SAMPLES
    assert theme.along(("#000000", "#ffffff"))[:: theme.RAMP_SAMPLES - 1] == [
        "#000000",
        "#ffffff",
    ]


def _y_range(fig: Figure) -> tuple[tuple[float, float], list[float] | None]:
    return (
        render.matplotlib(fig).axes[0].get_ylim(),
        render.plotly(fig).layout.yaxis.range,
    )


def test_a_y_range_holds_for_a_list_of_y_on_both_backends():
    data = pd.DataFrame(
        {"x": [1.0, 2.0, 3.0], "a": [1.0, 2.0, 4.0], "b": [2.0, 3.0, 1.0]}
    )
    for y in ("a", ["a", "b"]):
        fig = Figure(
            "r",
            "R",
            panels=[
                Panel(
                    "series",
                    axes={"y": {"range": (0, 100)}},
                    encodings={"x": "x", "y": y},
                )
            ],
            data=data,
        )
        ylim, yrange = _y_range(fig)
        assert ylim == pytest.approx((0, 100)), y
        assert list(yrange) == [0.0, 100.0], y


def test_a_y_range_holds_for_a_box_on_both_backends():
    fig = specs.box()
    panel = fig.panels[0]
    panel.axes.y.range = (0.0, 1e6)
    fig = Figure.from_json(fig.to_json())
    ylim, yrange = _y_range(fig)
    assert ylim == pytest.approx((0, 1e6))
    assert list(yrange) == [0.0, 1e6]


def test_a_precision_without_a_type_formats_an_integer_column():
    data = pd.DataFrame({"n": [1, 25, 1234], "m": [3, 4, 5]})
    fig = Figure(
        "p",
        "P",
        panels=[
            Panel(
                "table",
                encodings={"columns": ["n", "m"]},
                options={"formats": {"n": ".2", "m": ",.3"}},
            )
        ],
        data=data,
    )
    table = common.table(fig, fig.panels[0])
    assert [row[0] for row in table.rows] == ["1.0", "2.5e+01", "1.2e+03"]
    assert [row[1] for row in table.rows] == ["3.0", "4.0", "5.0"]
    cells = render.plotly(fig).data[0].cells.values
    assert list(cells[0]) == ["1.0", "2.5e+01", "1.2e+03"]
    table = next(
        c for c in render.matplotlib(fig).axes[0].get_children() if isinstance(c, Table)
    )
    drawn = {cell.get_text().get_text() for cell in table.get_celld().values()}
    assert {"1.0", "2.5e+01", "1.2e+03", "5.0"} <= drawn
    # A precision with a type formats the integer as Python does.
    typed = Figure(
        "q",
        "Q",
        panels=[
            Panel(
                "table", encodings={"columns": ["n"]}, options={"formats": {"n": ".1%"}}
            )
        ],
        data=data,
    )
    assert common.table(typed, typed.panels[0]).rows[1] == ["2500.0%"]


def test_numpy_times_in_the_summary_are_written_as_their_pandas_twins():
    zoned = pd.Series(pd.date_range("2026-07-01", periods=3, freq="1h", tz=specs.ZONE))
    fig = Figure(
        "s",
        "S",
        panels=[Panel("table", encodings={"columns": ["a"]})],
        summary={
            "first": zoned.to_numpy(dtype="datetime64[ns]").min(),
            "day": np.datetime64("2026-07-01", "D"),
            "never": np.datetime64("NaT", "ns"),
            "step": np.timedelta64(90, "m"),
            "no_step": np.timedelta64("NaT", "ns"),
        },
        data=pd.DataFrame({"a": [1]}),
    )
    first = zoned.min().tz_convert("UTC").isoformat()
    assert fig.summary == {
        "first": first,
        "day": "2026-07-01T00:00:00+00:00",
        "never": None,
        "step": pd.Timedelta("90min").isoformat(),
        "no_step": None,
    }
    assert pd.Timestamp(fig.summary["first"]) == zoned.min()
    assert Figure.from_json(fig.to_json()).summary == fig.summary


def test_the_size_legend_gives_three_rows_and_a_round_value_of_two():
    from dataclasses import replace

    fine = replace(theme.LIGHT, marks=replace(theme.LIGHT.marks, size_min=1.0))
    legend = common.size_legend(np.array([10.0, 1e5]), fine)
    assert [value for value, _ in legend] == [1e5, 2e4, 5e3]
    assert [diameter for _, diameter in legend] == pytest.approx(
        [18.0, 18.0 * np.sqrt(0.2), 18.0 * np.sqrt(0.05)]
    )
    # A round value too small to draw above the smallest size is left out.
    assert [value for value, _ in common.size_legend(np.array([9e4]), theme.LIGHT)] == [
        5e4,
        1e4,
    ]


def test_the_header_is_the_themes_for_one_line_of_subtitle():
    fig = specs.series()
    layout = theme.LIGHT.layout
    line = theme.LIGHT.type.subtitle * common.LINE_HEIGHT
    assert common.arrange(fig, theme.LIGHT, subtitle=1).header == layout.header
    assert common.arrange(fig, theme.LIGHT, subtitle=3).header == pytest.approx(
        layout.header + 2 * line
    )
    assert common.arrange(fig, theme.LIGHT, subtitle=0).header == (
        layout.header - theme.LIGHT.type.subtitle - 6
    )


def test_a_panel_draws_only_the_categories_its_rows_hold_on_both_backends():
    days = pd.CategoricalDtype(["Mon", "Tue", "Wed"], ordered=True)
    data = pd.DataFrame(
        {
            "day": pd.Series(["Wed", "Mon", "Tue"], dtype=days),
            "y": [3.0, 1.0, 2.0],
            "part": [1, 1, 2],
        }
    )
    fig = Figure(
        "c",
        "C",
        panels=[Panel("bars", encodings={"x": "day", "y": "y"}, where={"part": 1})],
        data=data,
    )
    assert common.ordered(fig.frame(fig.panels[0])["day"]) == ["Mon", "Wed"]
    ax = render.matplotlib(fig).axes[0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["Mon", "Wed"]
    assert list(render.plotly(fig).layout.xaxis.ticktext) == ["Mon", "Wed"]


def test_a_group_with_two_names_in_a_panel_takes_its_first():
    data = pd.DataFrame(
        {"x": ["a", "a", "b"], "y": [1.0, 2.0, 3.0], "name": ["One", "Uno", "Two"]}
    )
    fig = Figure(
        "n",
        "N",
        panels=[
            Panel("bars", encodings={"x": "x", "y": "y", "group": "x", "name": "name"})
        ],
        data=data,
    )
    assert common.colours(fig, theme.LIGHT).name("a") == "One"
    names = [t.name for t in render.plotly(fig).data if t.showlegend]
    assert names == ["One", "Two"]


# --- round 4: heatmap cells on a log axis, a lone cell ------------------------------


LOG = {"scale": "log"}


def _grid_figure(xs, ys, axes, gap_at=0) -> Figure:
    """A heatmap of one cell per x and y, numbered, the *gap_at*-th a gap."""
    cells = [(x, y) for y in ys for x in xs]
    data = pd.DataFrame(
        {
            "x": [x for x, _ in cells],
            "y": [y for _, y in cells],
            "v": np.arange(1.0, len(cells) + 1),
            "gap": [i == gap_at for i in range(len(cells))],
        }
    )
    panel = Panel(
        "heatmap",
        axes=axes,
        encodings={"x": "x", "y": "y", "value": "v", "gap": "gap"},
    )
    return Figure("h", "H", panels=[panel], data=data)


def _mesh_edges(fig: Figure) -> list[tuple[list[float], list[float]]]:
    """The x and y edges of each mesh matplotlib draws: the values', then
    the gap cells'."""
    ax = render.matplotlib(fig).axes[0]
    meshes = [c for c in ax.collections if isinstance(c, QuadMesh)]
    return [
        (
            np.asarray(m.get_coordinates()[0, :, 0]).tolist(),
            np.asarray(m.get_coordinates()[:, 0, 1]).tolist(),
        )
        for m in meshes
    ]


def _heatmaps(fig: Figure) -> list:
    return [t for t in render.plotly(fig).data if t.type == "heatmap"]


def test_a_heatmap_on_a_log_axis_meets_its_cells_halfway_in_log_space():
    """matplotlib draws the cells at the edges common.edges gives, which on
    a log axis are the geometric means of two centres; plotly is given the
    centres, which plotly.js places by the same rule (``makeBoundArray``).
    Read off a plotly.js page, its edges for these centres are the same."""
    fig = _grid_figure([1.0, 10.0, 100.0], [2.0, 200.0], {"x": LOG, "y": LOG})
    x_edges = [10**-0.5, 10**0.5, 10**1.5, 10**2.5]
    y_edges = [0.2, 20.0, 2000.0]
    assert common.edges(np.array([1.0, 10.0, 100.0]), "log") == pytest.approx(x_edges)
    meshes = _mesh_edges(fig)
    assert len(meshes) == 2
    for xs, ys in meshes:
        assert xs == pytest.approx(x_edges)
        assert ys == pytest.approx(y_edges)
    traces = _heatmaps(fig)
    assert len(traces) == 2
    for trace in traces:
        assert trace.x == (1.0, 10.0, 100.0) and trace.y == (2.0, 200.0)
    layout = render.plotly(fig).layout
    assert layout.xaxis.type == "log" and layout.yaxis.type == "log"
    # On a linear axis the edges stay halfway in linear space.
    linear = _grid_figure([1.0, 10.0, 100.0], [2.0, 200.0], {})
    assert _mesh_edges(linear)[0][0] == pytest.approx([-3.5, 5.5, 55.0, 145.0])


def test_a_heatmap_on_a_log_axis_has_no_cells_where_a_value_is_not_positive():
    fig = _grid_figure([-1.0, 0.0, 1.0, 10.0], [0.0, 5.0, 50.0], {"x": LOG, "y": LOG})
    cells = common.grid(fig, fig.panels[0])
    assert cells.xs == [1.0, 10.0] and cells.ys == [5.0, 50.0]
    ((xs, ys),) = _mesh_edges(fig)
    assert len(xs) == 3 and len(ys) == 3
    (trace,) = _heatmaps(fig)
    assert trace.x == (1.0, 10.0) and trace.y == (5.0, 50.0)
    # On a linear axis they are cells like any other.
    linear = _grid_figure([-1.0, 0.0, 1.0, 10.0], [0.0, 5.0, 50.0], {})
    cells = common.grid(linear, linear.panels[0])
    assert cells.xs == [-1.0, 0.0, 1.0, 10.0] and cells.ys == [0.0, 5.0, 50.0]


def test_a_lone_heatmap_cell_is_as_wide_on_both_backends():
    """A lone cell has no neighbour to meet: it is one unit wide on a linear
    axis, a day on a time axis and as wide as its value on a log axis,
    where plotly.js would make it one unit wide too and put a cell at 0.5 or
    under off the axis."""
    fig = _grid_figure([0.2], [3.0, 4.0], {"x": LOG})
    for xs, ys in _mesh_edges(fig):
        assert xs == pytest.approx([0.1, 0.3])
        assert ys == pytest.approx([2.5, 3.5, 4.5])
    for trace in _heatmaps(fig):
        assert trace.x is None and trace.x0 == 0.2 and trace.dx == pytest.approx(0.2)
        assert trace.y == (3.0, 4.0) and trace.y0 is None
    # Both axes lone: a log y and a linear x.
    fig = _grid_figure([7.0], [40.0], {"y": LOG}, gap_at=None)
    ((xs, ys),) = _mesh_edges(fig)
    assert xs == pytest.approx([6.5, 7.5]) and ys == pytest.approx([20.0, 60.0])
    (trace,) = _heatmaps(fig)
    assert (trace.x, trace.x0, trace.dx) == (None, 7.0, 1.0)
    assert (trace.y, trace.y0, trace.dy) == (None, 40.0, 40.0)
    # A lone category stays a category.
    fig = _grid_figure([1.0, 2.0], ["a"], {}, gap_at=None)
    (trace,) = _heatmaps(fig)
    assert trace.y == ("a",) and trace.y0 is None and trace.dy is None


def test_a_lone_heatmap_cell_on_a_time_axis_is_a_day_wide_on_both_backends():
    moment = pd.Timestamp("2026-07-01T00:00", tz="UTC")
    fig = _grid_figure([moment], [3.0, 4.0], {})
    day = pd.Timedelta(days=1)
    for xs, _ in _mesh_edges(fig):
        # matplotlib's date numbers: days since 1970.
        assert xs == pytest.approx(
            [(moment - day / 2).value / day.value, (moment + day / 2).value / day.value]
        )
    for trace in _heatmaps(fig):
        assert trace.x is None and trace.x0 == "2026-07-01T00:00:00"
        assert trace.dx == day / pd.Timedelta(milliseconds=1)


def test_a_heatmaps_hover_shows_a_cells_value_not_its_log():
    """On a log colour scale a cell's z is its value's log10, which the
    colour bar labels; the hover says the value itself."""
    fig = specs.heatmap()
    cells = common.grid(fig, fig.panels[0])
    trace = next(t for t in render.plotly(fig).data if t.type == "heatmap")
    assert "%{customdata}" in trace.hovertemplate
    assert "%{z}" not in trace.hovertemplate
    i, j = np.unravel_index(np.nanargmax(cells.values), cells.values.shape)
    peak = cells.values[i, j]
    assert peak > 1000
    assert trace.customdata[i][j] == f"{peak:.4g}"
    assert trace.z[i][j] == pytest.approx(np.log10(peak))
    blank = np.argwhere(np.isnan(cells.values))
    assert len(blank) and all(trace.customdata[a][b] is None for a, b in blank)
    # On a linear scale the value is z, and the hover says it alike.
    weekly = next(
        t for t in render.plotly(fig).data if t.type == "heatmap" and t.xaxis == "x2"
    )
    change = common.grid(fig, fig.panels[1]).values
    k, m = np.argwhere(np.isfinite(change))[0]
    assert weekly.customdata[k][m] == f"{change[k, m]:.4g}"
    # Four significant digits.
    data = pd.DataFrame(
        {"x": [1.0, 2.0], "y": ["a", "a"], "v": [1234.5678, 1.23456e-4]}
    )
    panel = Panel("heatmap", encodings={"x": "x", "y": "y", "value": "v"})
    (trace,) = _heatmaps(Figure("h", "H", panels=[panel], data=data))
    assert list(trace.customdata[0]) == ["1235", "0.0001235"]
