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
from mascope_viz.charts import FIGURE_SPEC_VERSION, PRIMITIVES, Figure, Panel, subtitle
from mascope_viz.render import common, theme


TESTS = Path(__file__).parent

ALL = [*specs.FIGURES, "everything"]


def _figure(name: str) -> Figure:
    return specs.everything() if name == "everything" else specs.FIGURES[name]()


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


#: The trace types each primitive draws with plotly.
TRACES = {
    "series": {"scatter"},
    "strip": {"bar", "scatter"},
    "heatmap": {"heatmap"},
    "scatter": {"scatter"},
    "sticks": {"scatter"},
    "bars": {"bar"},
    "box": {"box"},
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
    plotly_boxes = sum(len(t.x) for t in render.plotly(box).data)
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


# --- gaps, log axes, bands and spans -------------------------------------------------


def test_a_nan_bin_breaks_the_line_on_both_backends():
    fig = specs.series()
    rows = fig.data[fig.data["name"] == "Malonic acid"]
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
    assert colours.groups["Malonic acid"] == theme.LIGHT.categorical[0]
    assert colours.groups["C3H4O4 $I$ <b>"] == theme.LIGHT.categorical[1]
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
    fig = specs.everything()
    plotted = render.plotly(fig)
    shown = [t.name for t in plotted.data if getattr(t, "showlegend", None)]
    assert shown == [
        "Malonic acid",
        "C3H4O4 $I$ &lt;b&gt;",
        "acquired, in no batch",
        "assigned",
        "candidate",
    ]
    malonic = [
        t for t in plotted.data if getattr(t, "legendgroup", None) == "Malonic acid"
    ]
    assert {t.type for t in malonic} >= {"scatter", "box"}
    legend = render.matplotlib(fig).legends[0]
    assert [t.get_text() for t in legend.get_texts()] == [
        "Malonic acid",
        "C3H4O4 \\$I\\$ <b>",
        "acquired, in no batch",
        "assigned",
        "candidate",
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
    assert fig.panels[1].columns() == ["mz", "intensity", "direction", "formula"]


def test_a_colours_option_names_a_groups_colour_by_token():
    fig = specs.sticks()
    for name in theme.THEMES:
        palette = theme.get(name)
        colours = common.colours(fig, palette)
        assert colours.groups["up"] == palette.success
        assert colours.groups["down"] == palette.error
        lines = {t.name: t for t in render.plotly(fig, theme=name).data if t.name}
        assert lines["up"].line.color == palette.success


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
        "group": "name",
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
    colours = json.loads(specs.sticks().to_json())["panels"][1]["options"]["colours"]
    assert colours == {"up": "success", "down": "error"}


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
    spectrogram, weekly = (t for t in plotted.data if t.type == "heatmap")
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
        if isinstance(c, QuadMesh)
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


def test_subtitle_names_the_cadence_the_normalisation_and_the_zone():
    assert subtitle(cadence="1h", stat="median", normalise="none", tz="UTC") == (
        "1h medians; raw values; times in UTC"
    )
    assert subtitle(cadence="raw", normalise="the TIC", tz="Europe/Helsinki") == (
        "every sample; normalised to the TIC; times in Europe/Helsinki"
    )
