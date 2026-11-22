"""The Figure spec on its own: the context, the subtitle, what a figure
refuses, equality, and the registry of chart kinds (docs/dev/visual_digest.md,
2.4). What the renderers draw of it is test_render.py's."""

import json

import numpy as np
import pandas as pd
import pytest
from fixtures import specs

from mascope_viz.charts import (
    CONTEXT_FIELDS,
    ChartKind,
    Context,
    Figure,
    Panel,
    Param,
    registry,
    subtitle,
)


ZONE = specs.ZONE


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": pd.date_range("2026-07-01", periods=3, freq="1h", tz="UTC"),
            "x": [1.0, 2.0, 3.0],
            "label": ["a", "b", "c"],
            "name": ["A", "B", "C"],
        }
    )


def _figure(*panels: Panel, **fields) -> Figure:
    return Figure("f", "F", panels=list(panels), data=_frame(), **fields)


#: A box panel's statistics.
BOX = ("q1", "median", "q3", "lo", "hi")


#: A view frame's attrs, as views.series gives them after MAS-1094: a sum
#: of three reagent ions as the normaliser.
ATTRS = {
    "view": "series",
    "tz": ZONE,
    "cadence": "1h",
    "stat": "median",
    "band": (10, 90),
    "start": "2026-06-30T21:00:00+00:00",
    "end": "2026-07-02T21:00:00+00:00",
    "normalise": list(specs.REAGENTS),
    "normalise_name": specs.SUM_NAME,
    "normalise_members": list(specs.REAGENTS),
    "normalise_complete": 0.92,
    "keys": ["ion:1"],
    "mixed_runs": True,
    "run_changes": [
        {
            "at": "2026-07-02T09:00:00+00:00",
            "sample_batch_id": "batch-2",
            "polarity": "-",
            "before": "run 1",
            "after": "run 2",
        }
    ],
    "provenance": {"server": "x"},
    "source": "rows",
}


# --- the context ---------------------------------------------------------------


def test_the_context_is_the_views_attrs_as_data():
    context = Context.from_attrs(ATTRS)
    assert context.to_dict() == {
        name: (list(ATTRS[name]) if name == "band" else ATTRS[name])
        for name in CONTEXT_FIELDS
    }
    assert context.band == [10.0, 90.0]
    assert context.normalise == specs.REAGENTS
    # What is not the figure's to carry stays out.
    assert set(context.to_dict()) == set(CONTEXT_FIELDS)
    fig = _figure(Panel("series", encodings={"x": "t", "y": "x"}), context=context)
    document = json.loads(fig.to_json())
    assert document["context"]["normalise"] == specs.REAGENTS
    assert document["context"]["run_changes"][0]["at"] == "2026-07-02T09:00:00+00:00"
    parsed = Figure.from_json(fig.to_json())
    assert parsed.context == context
    assert parsed.subtitle == fig.subtitle


def test_normalise_is_a_text_or_a_list_of_keys():
    assert Context(normalise="tic").normalise == "tic"
    assert Context(normalise=("ion:1", "ion:2")).normalise == ["ion:1", "ion:2"]
    with pytest.raises(ValueError, match="a sum needs a key"):
        Context(normalise=[])
    with pytest.raises(ValueError, match="is not text"):
        Context(normalise=["ion:1", 2])


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"band": [90, 10]}, "low first"),
        ({"band": [50, 50]}, "low first"),
        ({"band": [10]}, "not two percentiles"),
        ({"start": "2026-07-01T00:00"}, "has no offset"),
        ({"start": "soon"}, "is not a time"),
        ({"start": 5}, "is not a time"),
        (
            {"start": "2026-07-02T00:00Z", "end": "2026-07-01T00:00Z"},
            "start is not before its end",
        ),
        (
            {"start": "2026-07-01T03:00+03:00", "end": "2026-07-01T00:00Z"},
            "start is not before its end",
        ),
        ({"normalise_complete": 1.5}, "no share"),
        ({"mixed_runs": "yes"}, "not true or false"),
        ({"cadence": 1}, "is not text"),
        ({"normalise_members": "ion:1"}, "is not a list"),
        ({"run_changes": [{"before": "a"}]}, "has no time"),
        ({"run_changes": [{"at": "2026-07-01T00:00Z", "who": "x"}]}, "no field who"),
    ],
)
def test_the_context_refuses_what_is_not_its_type(fields, message):
    with pytest.raises(ValueError, match=message):
        Context(**fields)


def test_a_document_context_names_only_its_fields():
    document = json.loads(specs.series().to_json())
    document["context"]["provenance"] = {"server": "x"}
    with pytest.raises(ValueError, match="the context has no field provenance"):
        Figure.from_dict(document)


def test_a_nan_share_is_unknown():
    assert Context(normalise_complete=float("nan")).normalise_complete is None
    assert Context(normalise_complete=np.float64(0.5)).normalise_complete == 0.5


# --- the subtitle --------------------------------------------------------------------


def test_the_subtitle_is_written_from_the_context():
    expected = (
        f"1h medians, p10-p90 band; normalised to {specs.SUM_NAME}; "
        f"from 2026-07-01 00:00 to 2026-07-03 00:00; times in {ZONE}"
    )
    assert subtitle(ATTRS) == expected
    assert subtitle(Context.from_attrs(ATTRS)) == expected
    fig = _figure(
        Panel("series", encodings={"x": "t", "y": "x"}),
        context=Context.from_attrs(ATTRS),
    )
    assert fig.subtitle == expected
    # A subtitle given is drawn as given, an empty one included.
    assert (
        _figure(Panel("table", encodings={"columns": ["x"]}), subtitle="").subtitle
        == ""
    )


@pytest.mark.parametrize(
    "attrs, expected",
    [
        (
            {"cadence": "raw", "stat": "median", "tz": "UTC"},
            "every sample; times in UTC",
        ),
        ({"cadence": "1h", "stat": "max"}, "1h maxima"),
        ({"cadence": "1D", "stat": "min"}, "1D minima"),
        ({"cadence": "week"}, "cadence week"),
        ({"stat": "mean", "band": (25, 75)}, "means, p25-p75 band"),
        ({"normalise": "none"}, "raw values"),
        ({"normalise": "tic", "normalise_name": "TIC"}, "normalised to TIC"),
        ({"normalise": ["ion:1", "ion:2"]}, "normalised to ion:1 + ion:2"),
        (
            {"normalise": ["ion:1", "ion:2"], "normalise_members": ["I-", "IH2O-"]},
            "normalised to I- + IH2O-",
        ),
        ({"start": "2026-07-01T00:00:00+00:00"}, "from 2026-07-01 00:00+0000"),
        (
            {"end": "2026-07-01T00:00:00+00:00", "tz": ZONE},
            f"until 2026-07-01 03:00; times in {ZONE}",
        ),
        ({}, ""),
    ],
)
def test_the_subtitle_names_what_the_view_says(attrs, expected):
    assert subtitle(attrs) == expected


def test_the_subtitle_takes_more_parts():
    assert subtitle({"tz": "UTC"}, extra=["night against day"]) == (
        "times in UTC; night against day"
    )


# --- equality, durations, refusals -------------------------------------------------------


@pytest.mark.parametrize("name", [*specs.FIGURES, *specs.EXTRA])
def test_a_figure_equals_its_parsed_document(name):
    fig = {**specs.FIGURES, **specs.EXTRA}[name]()
    parsed = Figure.from_json(fig.to_json())
    assert parsed == fig
    assert fig in [parsed]
    changed = Figure.from_json(fig.to_json())
    changed.notes.append("one more")
    assert changed != fig
    assert fig != fig.to_dict()


@pytest.mark.parametrize(
    "width, written",
    [
        ("1h", "1h"),
        (pd.Timedelta("1h"), "1h"),
        (pd.Timedelta("90min"), "90min"),
        (pd.offsets.Hour(2), "2h"),
        ("1D", "1D"),
    ],
)
def test_a_duration_is_written_as_the_document_reads_it(width, written):
    fig = Figure(
        "s",
        "S",
        panels=[
            Panel("strip", encodings={"x": "t", "y": "label"}, options={"width": width})
        ],
        data=_frame(),
    )
    assert fig.panels[0].options["width"] == written
    parsed = Figure.from_json(fig.to_json())
    assert parsed == fig


@pytest.mark.parametrize(
    "panels, message",
    [
        (
            [
                Panel("series", encodings={"x": "t", "y": "x"}),
                Panel("series", encodings={"x": "x", "y": "x"}),
            ],
            "panel 1 \\(series\\) draws time and panel 2 \\(series\\) draws number",
        ),
        (
            [
                Panel("series", encodings={"x": "x", "y": "x"}),
                Panel("bars", encodings={"x": "label", "y": "x"}),
            ],
            "panel 2 \\(bars\\) draws categories",
        ),
        (
            [Panel("heatmap", encodings={"x": "label", "y": "x", "value": "x"})],
            "panel 1 \\(heatmap\\) draws categories",
        ),
        # Bars and boxes stand at 0, 1, 2 whatever their x column: a number
        # there (an hour, a week) is a category too.
        (
            [
                Panel("series", encodings={"x": "x", "y": "x"}),
                Panel("bars", encodings={"x": "x", "y": "x"}),
            ],
            "panel 2 \\(bars\\) draws categories",
        ),
        (
            [
                Panel("series", encodings={"x": "x", "y": "x"}),
                Panel("box", encodings={"x": "x", **{s: "x" for s in BOX}}),
            ],
            "panel 2 \\(box\\) draws categories",
        ),
    ],
)
def test_share_x_joins_axes_of_one_kind(panels, message):
    with pytest.raises(ValueError, match=message):
        _figure(*panels, share_x=True)


def test_share_x_passes_over_tables():
    fig = _figure(
        Panel("series", encodings={"x": "t", "y": "x"}),
        Panel("table", encodings={"columns": ["x"]}),
        Panel("strip", encodings={"x": "t", "y": "label"}),
        share_x=True,
    )
    assert fig.share_x


@pytest.mark.parametrize(
    "panel, message",
    [
        (
            dict(primitive="series", encodings={"x": "t", "y": "x", "name": "name"}),
            "name names the groups of its group column",
        ),
        (
            dict(primitive="scatter", encodings={"x": "x", "y": "x", "name": "name"}),
            "the panel has no categorical colour",
        ),
        (
            dict(
                primitive="scatter",
                encodings={"x": "x", "y": "x", "colour": "x", "name": "name"},
            ),
            "the panel has no categorical colour",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": "x"},
                options={"names": {"x": "X"}},
            ),
            "names the lines of a list of y",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": ["x"]},
                options={"names": {"label": "X"}},
            ),
            "'label' is none of them",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": ["x"], "name": "name"},
            ),
            "a list of y",
        ),
        (
            dict(
                primitive="sticks",
                encodings={"x": "x", "y": "x", "group": "label"},
                options={"by_sign": True},
            ),
            "one or the other",
        ),
        (
            dict(
                primitive="scatter",
                encodings={"x": "x", "y": "x", "size": "x"},
                axes={"size": {"scale": "log"}},
            ),
            "the size axis takes a label only",
        ),
        (
            dict(
                primitive="scatter",
                encodings={"x": "x", "y": "x", "size": "x"},
                axes={"size": {"range": (0, 10)}},
            ),
            "the size axis takes a label only",
        ),
        (
            dict(
                primitive="scatter",
                encodings={"x": "x", "y": "x"},
                axes={"x": {"scale": "log", "range": (0, 10)}},
            ),
            "reaches 0 on a log axis",
        ),
        # An axis that draws categories takes neither a range nor a log
        # scale, whatever its column's kind.
        (
            dict(
                primitive="bars",
                encodings={"x": "x", "y": "x"},
                axes={"x": {"range": (0, 10)}},
            ),
            "a range sets the ends of a number axis; x draws 'x' as categories",
        ),
        (
            dict(
                primitive="box",
                encodings={"x": "x", **{s: "x" for s in BOX}},
                axes={"x": {"scale": "log"}},
            ),
            "a log x axis draws numbers; x draws 'x' as categories",
        ),
        (
            dict(
                primitive="strip",
                encodings={"x": "t", "y": "x"},
                axes={"y": {"range": (0, 10)}},
            ),
            "y draws 'x' as categories",
        ),
        (
            dict(
                primitive="heatmap",
                encodings={"x": "label", "y": "x", "value": "x"},
                axes={"x": {"range": (0, 10)}},
            ),
            "x draws 'label' as categories",
        ),
        (
            dict(
                primitive="series",
                encodings={"x": "t", "y": "x"},
                axes={"x": {"scale": "log"}},
            ),
            "a log x axis draws numbers; x draws 't', which is time",
        ),
        (
            dict(
                primitive="sticks",
                encodings={"x": "x", "y": "x", "mark": "label"},
            ),
            "draws a boolean column; 'label' is text",
        ),
    ],
)
def test_the_spec_refuses_what_it_cannot_draw(panel, message):
    with pytest.raises((ValueError, TypeError), match=message):
        _figure(Panel(**panel))


def test_a_document_of_version_0_is_refused():
    document = json.loads(specs.box().to_json())
    document["figure_spec_version"] = 0
    with pytest.raises(ValueError, match="figure_spec_version=0 is not a version"):
        Figure.from_dict(document)


def test_the_diverging_ends_are_colour_tokens():
    from mascope_viz.render import theme

    for name in theme.THEMES:
        palette = theme.get(name)
        assert palette.token("diverging.low") == palette.diverging[0]
        assert palette.token("diverging.high") == palette.diverging[-1]
        assert palette.tokens()["diverging.high"] == palette.diverging[-1]
    fig = _figure(
        Panel(
            "bars",
            encodings={"x": "label", "y": "x", "group": "label"},
            options={"colours": {"a": "diverging.low"}},
        )
    )
    assert fig.panels[0].options["colours"] == {"a": "diverging.low"}


# --- the registry ----------------------------------------------------------------------------


@pytest.fixture
def kinds(monkeypatch):
    """An empty registry for the test, the package's own left as it was."""
    monkeypatch.setattr(registry, "_KINDS", {})
    return registry


def _kind(id: str, tier: int = 1, **fields) -> ChartKind:
    return ChartKind(
        id=id,
        question=f"what {id} answers",
        tier=tier,
        series="each key of keys",
        params=(
            Param("keys", "keys", help="the series"),
            Param("cadence", "duration", default="1h", choices=("10min", "1h", "1D")),
        ),
        summary=("median", "coverage_fraction"),
        build=lambda store, **params: specs.series(),
        **fields,
    )


def test_the_package_registers_its_chart_kinds_on_import():
    assert {
        "coverage",
        "instrument_signal",
        "scope_summary",
        "top_species",
        "species_series",
        "diurnal",
        "period_boxes",
    } <= set(registry.ids())
    assert [kind.id for kind in registry.all()] == list(registry.ids())
    assert all(kind.build is not None for kind in registry.all())


def test_a_kind_is_registered_once_and_found_by_id(kinds):
    first = kinds.register(_kind("species_series"))
    kinds.register(_kind("coverage"))
    kinds.register(_kind("an_untargeted_view", tier=2))
    assert kinds.get("species_series") is first
    # By tier, then by id.
    assert list(kinds.ids()) == ["coverage", "species_series", "an_untargeted_view"]
    assert [kind.id for kind in kinds.all()] == list(kinds.ids())
    with pytest.raises(ValueError, match="registered already"):
        kinds.register(_kind("coverage"))
    with pytest.raises(
        KeyError, match="the kinds are an_untargeted_view, coverage, species_series"
    ):
        kinds.get("nope")
    assert first.build(None).id == "species_series"


def test_a_kind_describes_itself_as_plain_data(kinds):
    kind = kinds.register(_kind("species_series"))
    described = json.loads(json.dumps(kind.to_dict()))
    assert described == {
        "id": "species_series",
        "question": "what species_series answers",
        "tier": 1,
        "series": "each key of keys",
        "params": [
            {
                "name": "keys",
                "type": "keys",
                "default": None,
                "choices": [],
                "help": "the series",
            },
            {
                "name": "cadence",
                "type": "duration",
                "default": "1h",
                "choices": ["10min", "1h", "1D"],
                "help": "",
            },
        ],
        "summary": ["median", "coverage_fraction"],
    }


@pytest.mark.parametrize(
    "make, message",
    [
        (lambda: Param("a b", "text"), "is not a Python name"),
        (lambda: Param("a", ""), "is not text"),
        (
            lambda: Param("a", "text", default="x", choices=("y",)),
            "none of its choices",
        ),
        (lambda: Param("a", "text", default=object()), "is not a JSON value"),
        (lambda: _kind("bad id"), "is not a name"),
        (lambda: _kind("k", tier=0), "is not at least 1"),
        (lambda: _kind("k", tier=True), "is not a number"),
        (
            lambda: ChartKind("k", "q", 1, "s", params=(Param("a", "text"),) * 2),
            "named twice",
        ),
        (lambda: ChartKind("k", "q", 1, "s", summary=(1,)), "field names"),
        (lambda: ChartKind("k", "q", 1, "s", build="nope"), "not callable"),
    ],
)
def test_the_registry_refuses_a_malformed_kind(make, message):
    with pytest.raises(ValueError, match=message):
        make()


def test_a_document_without_a_subtitle_draws_none():
    document = json.loads(specs.series().to_json())
    del document["subtitle"]
    assert Figure.from_dict(document).subtitle == ""


def test_a_zone_the_subtitle_cannot_read_keeps_the_offset():
    attrs = {"start": "2026-07-01T00:00:00+00:00", "tz": "Mars/Base"}
    assert subtitle(attrs) == "from 2026-07-01 00:00+0000; times in Mars/Base"


# --- what an axis draws ----------------------------------------------------------


@pytest.mark.parametrize(
    "panel, x, y",
    [
        (Panel("series", encodings={"x": "t", "y": "x"}), "time", "number"),
        (Panel("series", encodings={"x": "x", "y": ["x", "x"]}), "number", "number"),
        (Panel("bars", encodings={"x": "x", "y": "x"}), "category", "number"),
        (
            Panel("box", encodings={"x": "label", **{s: "x" for s in BOX}}),
            "category",
            "number",
        ),
        (Panel("strip", encodings={"x": "t", "y": "x"}), "time", "category"),
        (
            Panel("heatmap", encodings={"x": "label", "y": "x", "value": "x"}),
            "category",
            "number",
        ),
        (Panel("sticks", encodings={"x": "x", "y": "x"}), "number", "number"),
    ],
)
def test_an_axis_draws_time_numbers_or_categories(panel, x, y):
    from mascope_viz.charts.spec import axis_kind

    fig = _figure(panel)
    kinds = fig.kinds()
    assert (axis_kind(panel, kinds, "x"), axis_kind(panel, kinds, "y")) == (x, y)
