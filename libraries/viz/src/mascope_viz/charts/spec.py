"""The Figure spec: one document, eight primitives, any number of renderers.

A chart kind returns a :class:`Figure`: a title, a subtitle, an ordered list
of :class:`Panel`, a machine-readable summary, the frame behind it and its
notes. A panel names one of the eight primitives (:data:`PRIMITIVES`), the
columns of the figure's frame it draws (its encodings), its axes and its
options. The renderers (:mod:`mascope_viz.render`) draw the spec, never the
chart kind's code, so the same spec reads the same in a PDF, in an HTML
dashboard and, later, in the web app (``docs/dev/visual_digest.md``,
sections 2.4 and 2.8).

The spec is a document, not only a Python object: :meth:`Figure.to_json`
writes it as a versioned JSON document (:data:`FIGURE_SPEC_VERSION`) and
:meth:`Figure.from_json` reads it back to an equal figure, which renders
the same. What the document says, and nothing else, is what a renderer
draws:

- **The primitives and their encodings.** Each primitive has encodings it
  needs and encodings it may take, each naming a column of the figure's
  frame of a kind it can draw (:data:`PRIMITIVES`). There is no escape
  hatch to a renderer's own attributes: when a chart needs what the eight
  cannot say, the vocabulary grows here and every renderer draws the
  addition.
- **The frame**, column by column with each column's type, so a time
  column comes back with its zone and a categorical with its categories in
  their order. A value that is not finite is a gap, and the document
  writes it as ``null``.
- **Theme tokens by name.** A panel names colours (``series.1``,
  ``accent``, ``muted``, the ``success``, ``warning``, ``error`` and
  ``info`` states), the palettes (``categorical``, ``sequential``,
  ``diverging``) and the span kinds (``event``, ``gap``) by name; the
  renderer's theme gives them values (:mod:`mascope_viz.render.theme`), so
  one spec draws in the light and the dark theme alike.

This module imports pandas and numpy only, so a server can build and send
specs without the renderers.
"""

import datetime
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from pandas.api import types as ptypes


#: The version of the document :meth:`Figure.to_json` writes. A reader
#: refuses a document of a later version, whose vocabulary it may not know.
FIGURE_SPEC_VERSION = 1

#: What a column of the frame is, as the encodings take it: ``time``
#: (datetimes), ``number`` (integers and floats), ``boolean`` and ``text``
#: (strings and categoricals).
KINDS = ("time", "number", "boolean", "text")

#: The scales an axis takes.
SCALES = ("linear", "log")

#: How a ``series`` or ``heatmap`` panel is drawn: one mark per value, or
#: rasterised onto a pixel grid (the ``[big]`` extra's datashader path,
#: reserved: until it exists, a renderer draws marks and says so).
MODES = ("marks", "raster")

#: The palettes a panel names: ``categorical`` colours groups,
#: ``sequential`` and ``diverging`` map a number to a colour.
PALETTES = ("categorical", "sequential", "diverging")

#: The colours a panel may name for a group (option ``colours``).
COLOUR_TOKENS = (
    *(f"series.{i}" for i in range(1, 11)),
    "accent",
    "text",
    "muted",
    "success",
    "warning",
    "error",
    "info",
)

#: The kinds of span a time panel shades: an ``event`` (an episode, a period
#: compared) or a ``gap`` (no data).
SPAN_KINDS = ("event", "gap")

#: A number format a ``table`` column takes: a small subset of Python's
#: format specification, ``[,][.N](e|f|g|%)`` or ``[,]d``. A renderer outside
#: Python maps it to its own (d3-format writes Python's ``g``, which drops
#: trailing zeros, as ``~g``).
_FORMAT = re.compile(r"^,?(?:\.\d{1,2}[efg%]?|[efg%d])$")

#: A figure's id, as a file name takes it.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


# --- the vocabulary ----------------------------------------------------------


@dataclass(frozen=True)
class Encoding:
    """An encoding a primitive takes: the column kinds it draws.

    :ivar kinds: The :data:`KINDS` the column may be.
    :ivar many: Whether it takes a list of columns as well as one.
    """

    kinds: tuple[str, ...]
    many: bool = False


@dataclass(frozen=True)
class Option:
    """An option a primitive takes.

    :ivar kind: What its value is: ``bool``, ``int`` (non-negative),
        ``number``, ``duration`` (a pandas offset such as ``1h``),
        ``text``, ``mode`` (:data:`MODES`), ``palette``
        (``sequential`` or ``diverging``), ``spans``, ``rules``,
        ``colours`` (group to :data:`COLOUR_TOKENS`), ``headers`` (column
        to text) or ``formats`` (column to a number format).
    :ivar default: Its value when the panel does not set it.
    """

    kind: str
    default: Any = None


@dataclass(frozen=True)
class Primitive:
    """One of the eight things a panel draws.

    :ivar required: The encodings it needs.
    :ivar optional: The encodings it may take.
    :ivar options: The options it takes, by name.
    :ivar summary: What it draws, in a line.
    """

    required: Mapping[str, Encoding]
    optional: Mapping[str, Encoding]
    options: Mapping[str, Option]
    summary: str

    @property
    def encodings(self) -> dict[str, Encoding]:
        return {**self.required, **self.optional}


_NUMBER = Encoding(("number",))
_TIME_OR_NUMBER = Encoding(("time", "number"))
_GROUP = Encoding(("text", "boolean", "number"))
_LABEL = Encoding(("text", "number"))
_ANY = ("time", "number", "boolean", "text")
_TIME_MARKS = {"spans": Option("spans", ()), "rules": Option("rules", ())}

#: The eight primitives, by name: what each draws and from which columns.
PRIMITIVES: dict[str, Primitive] = {
    "series": Primitive(
        required={"x": _TIME_OR_NUMBER, "y": Encoding(("number",), many=True)},
        optional={"group": _GROUP, "lo": _NUMBER, "hi": _NUMBER},
        options={
            "mode": Option("mode", "marks"),
            "markers": Option("bool", False),
            "colours": Option("colours", {}),
            **_TIME_MARKS,
        },
        summary=(
            "lines over time or a number: one per group, or one per column "
            "of a list of y; lo and hi a band; a gap (NaN) breaks the line"
        ),
    ),
    "strip": Primitive(
        required={"x": Encoding(("time",)), "y": _GROUP},
        optional={"value": _NUMBER, "mark": Encoding(("boolean",))},
        options={
            "width": Option("duration", None),
            "mark_label": Option("text", "marked"),
            **_TIME_MARKS,
        },
        summary=(
            "categorical rows by time: a cell where a row has a value, shaded "
            "by value; mark flags cells"
        ),
    ),
    "heatmap": Primitive(
        required={"x": Encoding(_ANY), "y": Encoding(_ANY), "value": _NUMBER},
        optional={},
        options={
            "mode": Option("mode", "marks"),
            "palette": Option("palette", "sequential"),
            "centre": Option("number", None),
        },
        summary="an x by y grid of cells coloured by value",
    ),
    "scatter": Primitive(
        required={"x": _NUMBER, "y": _NUMBER},
        optional={
            "size": _NUMBER,
            "colour": Encoding(("number", "text", "boolean")),
            "label": _LABEL,
        },
        options={
            "top_n": Option("int", 0),
            "palette": Option("palette", "sequential"),
            "colours": Option("colours", {}),
        },
        summary=(
            "points: size and colour by column, the top_n largest (by size, "
            "else y) labelled"
        ),
    ),
    "sticks": Primitive(
        required={"x": _NUMBER, "y": _NUMBER},
        optional={"group": _GROUP, "label": _LABEL},
        options={"top_n": Option("int", 0), "colours": Option("colours", {})},
        summary="vertical lines from zero, a spectrum; the top_n tallest labelled",
    ),
    "bars": Primitive(
        required={"x": _GROUP, "y": _NUMBER},
        optional={"group": _GROUP, "lo": _NUMBER, "hi": _NUMBER},
        options={"stack": Option("bool", False), "colours": Option("colours", {})},
        summary="bars per category, groups side by side or stacked, lo-hi error bars",
    ),
    "box": Primitive(
        required={
            "x": _GROUP,
            "q1": _NUMBER,
            "median": _NUMBER,
            "q3": _NUMBER,
            "lo": _NUMBER,
            "hi": _NUMBER,
        },
        optional={"group": _GROUP, "mean": _NUMBER},
        options={"colours": Option("colours", {})},
        summary=(
            "a box per category from its quartiles, whiskers from lo to hi, "
            "groups side by side"
        ),
    ),
    "table": Primitive(
        required={"columns": Encoding(_ANY, many=True)},
        optional={},
        options={
            "headers": Option("headers", {}),
            "formats": Option("formats", {}),
            "max_rows": Option("int", 0),
        },
        summary="rows of the frame, its columns formatted; max_rows caps them",
    ),
}

#: The primitives that draw an x and a y axis.
PLOTTED = tuple(name for name in PRIMITIVES if name != "table")


# --- the spec ----------------------------------------------------------------


@dataclass
class Axis:
    """One axis of a panel.

    :ivar label: What it shows, with its unit and, for a time axis, its zone.
    :ivar scale: ``linear`` or ``log``. A value that is not positive is a gap
        on a log axis.
    :ivar range: Its ends in data units, or None to fit the data.
    """

    label: str = ""
    scale: str = "linear"
    range: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        self.label = _text(self.label, "an axis label")
        if self.scale not in SCALES:
            raise ValueError(f"scale={self.scale!r} is none of {', '.join(SCALES)}")
        if self.range is not None:
            ends = tuple(self.range)
            if len(ends) != 2 or not all(_finite(end) for end in ends):
                raise ValueError(f"range={self.range!r} is not two finite numbers")
            low, high = (float(end) for end in ends)
            if low >= high:
                raise ValueError(f"range={self.range!r} does not increase")
            if self.scale == "log" and low <= 0:
                raise ValueError(f"range={self.range!r} reaches 0 on a log axis")
            self.range = (low, high)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "scale": self.scale,
            "range": list(self.range) if self.range else None,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "Axis":
        _keys(document, {"label", "scale", "range"}, "an axis")
        return cls(**document)


@dataclass
class Axes:
    """A panel's axes: ``x``, ``y`` and ``colour``, the scale of a colour
    by number (a heatmap's value, a scatter's or a strip's colour)."""

    x: Axis = field(default_factory=Axis)
    y: Axis = field(default_factory=Axis)
    colour: Axis = field(default_factory=Axis)

    def __post_init__(self) -> None:
        for name in ("x", "y", "colour"):
            value = getattr(self, name)
            if isinstance(value, Mapping):
                setattr(self, name, Axis.from_dict(value))
            elif not isinstance(value, Axis):
                raise TypeError(f"axes.{name} is not an Axis: {value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name).to_dict() for name in ("x", "y", "colour")}

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "Axes":
        _keys(document, {"x", "y", "colour"}, "the axes")
        return cls(**{name: Axis.from_dict(axis) for name, axis in document.items()})


@dataclass
class Panel:
    """One plot of a figure.

    :ivar primitive: One of :data:`PRIMITIVES`.
    :ivar axes: Its :class:`Axes`, or a dict of them (``{"y": {"scale":
        "log"}}``).
    :ivar encodings: The figure's columns it draws, by encoding
        (``{"x": "t", "y": "value", "group": "name"}``).
    :ivar options: The primitive's options; those not given take their
        defaults.
    :ivar title: A title over the panel, for small multiples.
    :ivar where: The rows of the figure's frame it draws: ``{column:
        value}`` keeps the rows whose column equals the value, or is one of
        a list of them. A text, number or boolean column only.
    """

    primitive: str
    axes: Axes = field(default_factory=Axes)
    encodings: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    where: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.primitive not in PRIMITIVES:
            raise ValueError(
                f"primitive={self.primitive!r} is none of {', '.join(PRIMITIVES)}"
            )
        if isinstance(self.axes, Mapping):
            self.axes = Axes(**self.axes)
        elif not isinstance(self.axes, Axes):
            raise TypeError(f"axes is not Axes: {self.axes!r}")
        self.title = _text(self.title, "a panel title")
        self.encodings = self._encodings(self.encodings)
        unknown = set(self.options) - set(self.spec.options)
        if unknown:
            raise ValueError(
                f"{self.primitive} takes no option {', '.join(sorted(unknown))}; "
                f"it takes {', '.join(self.spec.options) or 'none'}"
            )
        self.where = {
            _text(column, "a where column"): _where_values(column, values)
            for column, values in dict(self.where).items()
        }

    @property
    def spec(self) -> Primitive:
        """The primitive's vocabulary."""
        return PRIMITIVES[self.primitive]

    def _encodings(self, encodings: Mapping[str, Any]) -> dict[str, Any]:
        spec = self.spec
        unknown = set(encodings) - set(spec.encodings)
        if unknown:
            raise ValueError(
                f"{self.primitive} takes no encoding {', '.join(sorted(unknown))}; "
                f"it takes {', '.join(spec.encodings)}"
            )
        missing = [name for name in spec.required if encodings.get(name) is None]
        if missing:
            raise ValueError(
                f"{self.primitive} needs the encoding {', '.join(missing)}"
            )
        checked: dict[str, Any] = {}
        for name, columns in encodings.items():
            if columns is None:
                continue
            if isinstance(columns, str):
                checked[name] = columns
            elif spec.encodings[name].many and isinstance(columns, Sequence):
                if not columns or not all(isinstance(c, str) for c in columns):
                    raise ValueError(
                        f"encoding {name}={columns!r} is not a list of column names"
                    )
                checked[name] = list(columns)
            else:
                raise ValueError(f"encoding {name}={columns!r} is not a column name")
        return checked

    def columns(self) -> list[str]:
        """The columns it draws, each once, in encoding order."""
        seen: dict[str, None] = {}
        for columns in self.encodings.values():
            for column in [columns] if isinstance(columns, str) else columns:
                seen[column] = None
        return list(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primitive": self.primitive,
            "title": self.title,
            "axes": self.axes.to_dict(),
            "encodings": dict(self.encodings),
            "options": _options_json(self),
            "where": {column: list(values) for column, values in self.where.items()},
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "Panel":
        _keys(
            document,
            {"primitive", "title", "axes", "encodings", "options", "where"},
            "a panel",
        )
        fields = dict(document)
        if "axes" in fields:
            fields["axes"] = Axes.from_dict(fields["axes"])
        return cls(**fields)


@dataclass
class Figure:
    """A chart: what every renderer draws, and the numbers behind it.

    :ivar id: The chart's id, as a file name takes it (``species_series``).
    :ivar title: What it shows.
    :ivar subtitle: How: the cadence, the normalisation and the time zone
        of a chart over time (:func:`subtitle`).
    :ivar panels: Its plots, in reading order.
    :ivar summary: The numbers a caption may cite, as plain JSON values.
    :ivar data: The frame behind it; every panel draws columns of it. Kept
        with a fresh index and no ``attrs``: what the figure holds is what
        its document says.
    :ivar notes: Lines printed under it.
    :ivar columns: How many panels a row of it holds.
    :ivar share_x: Whether its panels share the x axis, zoom included.
    """

    id: str
    title: str
    subtitle: str = ""
    panels: list[Panel] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: list[str] = field(default_factory=list)
    columns: int = 1
    share_x: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID.match(self.id):
            raise ValueError(
                f"id={self.id!r} is not a name of letters, digits, '_', '.' and '-'"
            )
        self.title = _text(self.title, "the title")
        self.subtitle = _text(self.subtitle, "the subtitle")
        self.notes = [_text(note, "a note") for note in self.notes]
        if isinstance(self.columns, bool) or not isinstance(self.columns, int):
            raise ValueError(f"columns={self.columns!r} is not a whole number")
        if self.columns < 1:
            raise ValueError(f"columns={self.columns!r} is not at least 1")
        if not isinstance(self.share_x, (bool, np.bool_)):
            raise ValueError(f"share_x={self.share_x!r} is not true or false")
        self.share_x = bool(self.share_x)
        self.panels = [
            Panel.from_dict(panel) if isinstance(panel, Mapping) else panel
            for panel in self.panels
        ]
        if not self.panels:
            raise ValueError("a figure needs at least one panel")
        if not all(isinstance(panel, Panel) for panel in self.panels):
            raise TypeError("panels holds something that is not a Panel")
        if not isinstance(self.data, pd.DataFrame):
            raise TypeError(f"data is not a DataFrame: {type(self.data).__name__}")
        self.data = _canonical_frame(self.data)
        self.summary = _plain(dict(self.summary), "summary")
        kinds = self.kinds()
        zones = {
            name: self.data[name].dt.tz
            for name, kind in kinds.items()
            if kind == "time"
        }
        for index, panel in enumerate(self.panels):
            _check_panel(panel, kinds, zones, f"panel {index + 1} ({panel.primitive})")

    def kinds(self) -> dict[str, str]:
        """Each column's :data:`KINDS`."""
        return {name: _kind(self.data[name]) for name in self.data.columns}

    def frame(self, panel: Panel) -> pd.DataFrame:
        """The rows of :attr:`data` the panel draws: those its ``where``
        keeps."""
        rows = np.ones(len(self.data), dtype=bool)
        for column, values in panel.where.items():
            rows &= self.data[column].isin(values).to_numpy(dtype=bool)
        return self.data[rows].reset_index(drop=True)

    # --- the document ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The figure as its JSON document's values."""
        return {
            "figure_spec_version": FIGURE_SPEC_VERSION,
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "columns": self.columns,
            "share_x": self.share_x,
            "panels": [panel.to_dict() for panel in self.panels],
            "summary": self.summary,
            "notes": list(self.notes),
            "data": _frame_json(self.data),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """The figure as a JSON document, strict JSON (no NaN) in ASCII."""
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False)

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "Figure":
        """A figure from the values of its JSON document.

        :raises ValueError: The document is of a later version than this
            package reads, or is not a figure.
        """
        if not isinstance(document, Mapping):
            raise ValueError("a figure document is a JSON object")
        version = document.get("figure_spec_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError(
                "the document has no figure_spec_version: it is not a figure spec"
            )
        if version > FIGURE_SPEC_VERSION:
            raise ValueError(
                f"the document is figure spec version {version}; this mascope_viz "
                f"reads version {FIGURE_SPEC_VERSION} and earlier. Upgrade "
                "mascope_viz to read it."
            )
        if version < 1:
            raise ValueError(f"figure_spec_version={version} is not a version")
        _keys(
            document,
            {
                "figure_spec_version",
                "id",
                "title",
                "subtitle",
                "columns",
                "share_x",
                "panels",
                "summary",
                "notes",
                "data",
            },
            "a figure",
        )
        data = _frame_from_json(document.get("data", {"columns": []}))
        panels = document.get("panels", [])
        if not isinstance(panels, list) or not all(
            isinstance(panel, Mapping) for panel in panels
        ):
            raise ValueError("the document's panels is not a list of objects")
        panels = [Panel.from_dict(panel) for panel in panels]
        fields = {
            key: document[key]
            for key in ("id", "title", "subtitle", "summary", "notes", "columns")
            if key in document
        }
        if "share_x" in document:
            fields["share_x"] = document["share_x"]
        return cls(panels=panels, data=data, **fields)

    @classmethod
    def from_json(cls, text: str | bytes) -> "Figure":
        """A figure from its JSON document (:meth:`to_json`)."""
        return cls.from_dict(json.loads(text))


def subtitle(
    *,
    cadence: str | None = None,
    stat: str | None = None,
    normalise: str | None = None,
    tz: str | None = None,
    extra: Sequence[str] = (),
) -> str:
    """The subtitle a chart over time carries, from what its view says.

    :param cadence: What one point stands for (``1h``, ``raw``, ``hour of
        day``), as a view's ``attrs["cadence"]`` gives it.
    :param stat: The statistic of a bin (``median``).
    :param normalise: What the values are divided by, by name (a view's
        ``attrs["normalise_name"]``); None or ``none`` for raw values.
    :param tz: The zone the times are in.
    :param extra: More parts, in order.
    :return: ``"1h medians; raw values; times in Europe/Helsinki"``.
    """
    parts = []
    if cadence:
        if cadence == "raw":
            parts.append("every sample")
        elif stat:
            parts.append(f"{cadence} {stat}s")
        else:
            parts.append(f"cadence {cadence}")
    if normalise is not None:
        parts.append(
            "raw values" if normalise in ("", "none") else f"normalised to {normalise}"
        )
    if tz:
        parts.append(f"times in {tz}")
    parts.extend(extra)
    return "; ".join(parts)


# --- checking a panel against the frame -----------------------------------------


def _check_panel(
    panel: Panel, kinds: Mapping[str, str], zones: Mapping[str, Any], where: str
) -> None:
    spec = panel.spec
    for name, columns in panel.encodings.items():
        encoding = spec.encodings[name]
        for column in [columns] if isinstance(columns, str) else columns:
            if column not in kinds:
                raise ValueError(
                    f"{where}: encoding {name} names the column {column!r}, "
                    "which the figure's data does not have"
                )
            if kinds[column] not in encoding.kinds:
                raise ValueError(
                    f"{where}: encoding {name} draws a "
                    f"{' or '.join(encoding.kinds)} column; {column!r} is "
                    f"{kinds[column]}"
                )
    for column in panel.where:
        if column not in kinds:
            raise ValueError(
                f"{where}: where names the column {column!r}, which the "
                "figure's data does not have"
            )
        if kinds[column] == "time":
            raise ValueError(
                f"{where}: where keeps rows by a text, number or boolean column; "
                f"{column!r} is time"
            )
    if panel.primitive in PLOTTED:
        for axis in ("x", "y"):
            column = panel.encodings.get(axis)
            kind = kinds[column] if isinstance(column, str) else "number"
            spec = getattr(panel.axes, axis)
            if spec.scale == "log" and kind != "number":
                raise ValueError(
                    f"{where}: a log {axis} axis draws numbers; {column!r} is {kind}"
                )
            if spec.range is not None and kind != "number":
                raise ValueError(
                    f"{where}: a range sets the ends of a number axis; {axis} draws "
                    f"{column!r}, which is {kind}"
                )
        if panel.primitive in ("sticks", "bars") and panel.axes.y.scale == "log":
            raise ValueError(
                f"{where}: {panel.primitive} rise from zero, which a log axis does "
                "not have"
            )
    if panel.primitive == "series":
        if not isinstance(panel.encodings["y"], str) and (
            {"group", "lo", "hi"} & set(panel.encodings)
        ):
            raise ValueError(
                f"{where}: a list of y draws one line per column, with no group "
                "and no band"
            )
    if panel.primitive == "bars" and panel.options.get("stack"):
        if {"lo", "hi"} & set(panel.encodings):
            raise ValueError(f"{where}: stacked bars draw no error bars")
    if ("lo" in panel.encodings) != ("hi" in panel.encodings) and panel.primitive in (
        "series",
        "bars",
    ):
        raise ValueError(f"{where}: lo and hi come together")
    panel.options = _options(panel, _x_axis(panel, kinds, zones), where)


def _x_axis(
    panel: Panel, kinds: Mapping[str, str], zones: Mapping[str, Any]
) -> tuple[str | None, Any]:
    """The kind of *panel*'s x column, and its zone when it is time."""
    x = panel.encodings.get("x")
    if not isinstance(x, str) or x not in kinds:
        return None, None
    return kinds[x], zones.get(x)


def _options(
    panel: Panel, x_axis: tuple[str | None, Any], where: str
) -> dict[str, Any]:
    """*panel*'s options checked, each one the primitive takes, those not
    given at their defaults."""
    checked: dict[str, Any] = {}
    for name, option in panel.spec.options.items():
        value = panel.options.get(name, option.default)
        label = f"{where}: option {name}"
        checked[name] = _option(option.kind, value, x_axis, label)
    return checked


def _option(kind: str, value: Any, x_axis: tuple[str | None, Any], label: str) -> Any:
    if kind == "bool":
        if not isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{label}={value!r} is not true or false")
        return bool(value)
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError(f"{label}={value!r} is not a whole number")
        if value < 0:
            raise ValueError(f"{label}={value!r} is negative")
        return int(value)
    if kind == "number":
        if value is None:
            return None
        if not _finite(value):
            raise ValueError(f"{label}={value!r} is not a finite number")
        return float(value)
    if kind == "text":
        return _text(value, label)
    if kind == "mode":
        if value not in MODES:
            raise ValueError(f"{label}={value!r} is none of {', '.join(MODES)}")
        return value
    if kind == "palette":
        if value not in ("sequential", "diverging"):
            raise ValueError(f"{label}={value!r} is neither sequential nor diverging")
        return value
    if kind == "duration":
        if value is None:
            return None
        try:
            step = pd.Timedelta(pd.tseries.frequencies.to_offset(value))
        except (TypeError, ValueError) as e:
            raise ValueError(f"{label}={value!r} is not a fixed duration") from e
        if step <= pd.Timedelta(0):
            raise ValueError(f"{label}={value!r} is not a positive duration")
        return str(value)
    if kind == "colours":
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} is not a mapping of group to colour")
        colours = {}
        for group, token in value.items():
            if token not in COLOUR_TOKENS:
                raise ValueError(
                    f"{label}: {token!r} is no colour token; they are "
                    f"{', '.join(COLOUR_TOKENS)}"
                )
            colours[_text(group, f"{label} group")] = token
        return colours
    if kind == "headers":
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} is not a mapping of column to header")
        return {
            _text(column, f"{label} column"): _text(header, label)
            for column, header in value.items()
        }
    if kind == "formats":
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} is not a mapping of column to format")
        formats = {}
        for column, spec in value.items():
            if not isinstance(spec, str) or not _FORMAT.match(spec):
                raise ValueError(
                    f"{label}: {spec!r} is not a number format such as '.3g', "
                    "',.0f', '.1%', '.2e' or 'd'"
                )
            formats[_text(column, f"{label} column")] = spec
        return formats
    if kind == "spans":
        return [_span(span, x_axis, label) for span in _list(value, label)]
    if kind == "rules":
        return [_rule(rule, x_axis, label) for rule in _list(value, label)]
    raise AssertionError(kind)  # pragma: no cover


def _list(value: Any, label: str) -> list[Any]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} is not a list")
    return list(value)


def _span(span: Any, x_axis: tuple[str | None, Any], label: str) -> dict[str, Any]:
    if not isinstance(span, Mapping):
        raise ValueError(f"{label}: a span is a mapping of start, end, label, kind")
    _keys(span, {"start", "end", "label", "kind"}, f"{label}: a span")
    kind = span.get("kind", "event")
    if kind not in SPAN_KINDS:
        raise ValueError(f"{label}: kind={kind!r} is none of {', '.join(SPAN_KINDS)}")
    start = _at(span.get("start"), x_axis, f"{label}: start")
    end = _at(span.get("end"), x_axis, f"{label}: end")
    if not start < end:
        raise ValueError(f"{label}: a span's start is not before its end")
    return {
        "start": start,
        "end": end,
        "label": _text(span.get("label", ""), f"{label}: label"),
        "kind": kind,
    }


def _rule(rule: Any, x_axis: tuple[str | None, Any], label: str) -> dict[str, Any]:
    if not isinstance(rule, Mapping):
        raise ValueError(f"{label}: a rule is a mapping of at and label")
    _keys(rule, {"at", "label"}, f"{label}: a rule")
    return {
        "at": _at(rule.get("at"), x_axis, f"{label}: at"),
        "label": _text(rule.get("label", ""), f"{label}: label"),
    }


def _at(value: Any, x_axis: tuple[str | None, Any], label: str) -> Any:
    """A place on the x axis: on a time axis a timestamp (an ISO 8601 text
    too) in the axis' zone, a time without a zone read in it; else a
    number."""
    kind, zone = x_axis
    if kind == "time":
        try:
            moment = pd.Timestamp(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{label}={value!r} is not a time") from e
        if moment is pd.NaT or pd.isna(moment):
            raise ValueError(f"{label} is missing")
        if zone is not None:
            moment = (
                moment.tz_convert(zone)
                if moment.tz is not None
                else moment.tz_localize(zone)
            )
        elif moment.tz is not None:
            raise ValueError(
                f"{label}={value!r} has a zone; the panel's time axis has none"
            )
        return moment.as_unit("ns")
    if not _finite(value):
        raise ValueError(f"{label}={value!r} is not a finite number")
    return float(value)


def _options_json(panel: Panel) -> dict[str, Any]:
    """*panel*'s options as JSON values: a time as ISO 8601."""
    options = {}
    for name, value in panel.options.items():
        kind = panel.spec.options[name].kind
        if kind == "spans":
            value = [
                {**span, "start": _place(span["start"]), "end": _place(span["end"])}
                for span in value
            ]
        elif kind == "rules":
            value = [{**rule, "at": _place(rule["at"])} for rule in value]
        options[name] = value
    return options


def _place(value: Any) -> Any:
    return value.isoformat() if isinstance(value, pd.Timestamp) else value


# --- the frame as a document ---------------------------------------------------


def _kind(column: pd.Series) -> str:
    dtype = column.dtype
    if ptypes.is_bool_dtype(dtype):
        return "boolean"
    if ptypes.is_datetime64_any_dtype(dtype):
        return "time"
    if ptypes.is_numeric_dtype(dtype):
        return "number"
    return "text"


#: The dtype a text column of this pandas takes: ``str`` from pandas 3,
#: ``object`` before.
_STRING = pd.Series(["a"]).dtype


def _canonical_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """*frame* as :func:`_frame_from_json` would read it back: a fresh
    index, no attrs, each column in the dtype its type reads to."""
    names = list(frame.columns)
    for name in names:
        if not isinstance(name, str):
            raise ValueError(f"the data's column {name!r} is not named by a string")
    if len(set(names)) != len(names):
        raise ValueError("the data names a column twice")
    columns = {name: _canonical(name, frame[name]) for name in names}
    canonical = pd.DataFrame(columns, index=pd.RangeIndex(len(frame)))
    canonical.attrs = {}
    return canonical


def _canonical(name: str, column: pd.Series) -> pd.Series:
    column = column.reset_index(drop=True)
    if column.dtype == object:
        column = _inferred(name, column)
    dtype = column.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        categories = list(dtype.categories)
        if not all(isinstance(c, str) for c in categories):
            raise ValueError(f"column {name!r}: a categorical's categories are text")
        values = column.astype(object).where(column.notna(), None)
        return pd.Series(
            pd.Categorical(
                values.tolist(),
                categories=pd.Index(categories, dtype=_STRING),
                ordered=bool(dtype.ordered),
            )
        )
    kind = _kind(column)
    if kind == "boolean":
        if column.isna().any() or isinstance(dtype, pd.BooleanDtype):
            return column.astype("boolean")
        return column.astype(bool)
    if kind == "time":
        zone = getattr(dtype, "tz", None)
        return column.astype(f"datetime64[ns, {zone}]" if zone else "datetime64[ns]")
    if kind == "number":
        if ptypes.is_integer_dtype(dtype):
            if isinstance(dtype, pd.api.extensions.ExtensionDtype):
                return column.astype("Int64")
            return column.astype("int64")
        values = column.astype("float64")
        return values.where(np.isfinite(values.to_numpy()), np.nan)
    values = column.astype(object)
    bad = [v for v in values if not (isinstance(v, str) or _missing(v))]
    if bad:
        raise ValueError(
            f"column {name!r} holds {type(bad[0]).__name__} values; a column is "
            "times, numbers, booleans or text"
        )
    return pd.Series([v if isinstance(v, str) else None for v in values], dtype=_STRING)


def _inferred(name: str, column: pd.Series) -> pd.Series:
    """An object column as the type its values are: booleans, integers or
    floats (a missing value among them NA), times, or text."""
    inferred = ptypes.infer_dtype(column, skipna=True)
    if inferred == "boolean":
        return column.astype("boolean")
    if inferred == "integer":
        return column.astype("Int64")
    if inferred in ("floating", "mixed-integer-float", "decimal"):
        return column.astype("float64")
    if inferred in ("datetime", "datetime64"):
        return pd.to_datetime(column)
    if inferred in ("string", "empty"):
        return column
    raise ValueError(
        f"column {name!r} holds {inferred} values; a column is times, numbers, "
        "booleans or text"
    )


def _frame_json(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "length": len(frame),
        "columns": [_column_json(name, frame[name]) for name in frame.columns],
    }


def _column_json(name: str, column: pd.Series) -> dict[str, Any]:
    dtype = column.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        return {
            "name": name,
            "type": "category",
            "categories": [str(c) for c in dtype.categories],
            "ordered": bool(dtype.ordered),
            "values": [None if _missing(v) else str(v) for v in column.astype(object)],
        }
    kind = _kind(column)
    if kind == "time":
        zone = getattr(dtype, "tz", None)
        return {
            "name": name,
            "type": "time",
            "tz": str(zone) if zone is not None else None,
            "values": [None if v is pd.NaT else v.isoformat() for v in column],
        }
    if kind == "boolean":
        nullable = isinstance(dtype, pd.BooleanDtype)
        return {
            "name": name,
            "type": "boolean",
            "nullable": nullable,
            "values": [None if _missing(v) else bool(v) for v in column.astype(object)],
        }
    if kind == "number":
        if ptypes.is_integer_dtype(dtype):
            nullable = isinstance(dtype, pd.api.extensions.ExtensionDtype)
            return {
                "name": name,
                "type": "integer",
                "nullable": nullable,
                "values": [
                    None if _missing(v) else int(v) for v in column.astype(object)
                ],
            }
        values = column.to_numpy(dtype="float64")
        return {
            "name": name,
            "type": "number",
            "values": [float(v) if math.isfinite(v) else None for v in values.tolist()],
        }
    return {
        "name": name,
        "type": "text",
        "values": [v if isinstance(v, str) else None for v in column.astype(object)],
    }


def _frame_from_json(document: Any) -> pd.DataFrame:
    if not isinstance(document, Mapping) or not isinstance(
        document.get("columns"), list
    ):
        raise ValueError("the document's data is not an object with a columns list")
    _keys(document, {"length", "columns"}, "the data")
    columns: dict[str, pd.Series] = {}
    length = document.get("length")
    for column in document["columns"]:
        if not isinstance(column, Mapping):
            raise ValueError("a data column is a JSON object")
        name = column.get("name")
        if not isinstance(name, str):
            raise ValueError("a data column has no name")
        if name in columns:
            raise ValueError(f"the data names the column {name!r} twice")
        values = column.get("values")
        if not isinstance(values, list):
            raise ValueError(f"column {name!r} has no values list")
        if length is None:
            length = len(values)
        if len(values) != length:
            raise ValueError(
                f"column {name!r} has {len(values)} values; the data has {length} rows"
            )
        columns[name] = _column_from_json(name, column, values)
    if length is None:
        length = 0
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        raise ValueError(f"the data's length {length!r} is not a row count")
    return pd.DataFrame(columns, index=pd.RangeIndex(length))


def _column_from_json(
    name: str, column: Mapping[str, Any], values: list[Any]
) -> pd.Series:
    kind = column.get("type")
    allowed = {"name", "type", "values"}
    if kind == "category":
        _keys(column, allowed | {"categories", "ordered"}, f"column {name!r}")
        categories = column.get("categories")
        if not isinstance(categories, list) or not all(
            isinstance(c, str) for c in categories
        ):
            raise ValueError(f"column {name!r}: categories is not a list of text")
        unknown = {v for v in values if v is not None} - set(categories)
        if unknown:
            raise ValueError(
                f"column {name!r} holds values its categories do not: "
                f"{', '.join(sorted(map(repr, unknown))[:5])}"
            )
        return pd.Series(
            pd.Categorical(
                values,
                categories=pd.Index(categories, dtype=_STRING),
                ordered=bool(column.get("ordered", False)),
            )
        )
    if kind == "time":
        _keys(column, allowed | {"tz"}, f"column {name!r}")
        zone = column.get("tz")
        try:
            if zone:
                parsed = pd.to_datetime(
                    pd.Series(values, dtype=object), format="ISO8601", utc=True
                ).dt.tz_convert(zone)
                return parsed.astype(f"datetime64[ns, {zone}]")
            parsed = pd.to_datetime(pd.Series(values, dtype=object), format="ISO8601")
            return parsed.astype("datetime64[ns]")
        except (TypeError, ValueError) as e:
            raise ValueError(f"column {name!r} holds a value that is not a time") from e
    if kind == "boolean":
        _keys(column, allowed | {"nullable"}, f"column {name!r}")
        if not all(v is None or isinstance(v, bool) for v in values):
            raise ValueError(f"column {name!r} holds a value that is not a boolean")
        if column.get("nullable") or any(v is None for v in values):
            return pd.Series(values, dtype="boolean")
        return pd.Series(values, dtype=bool)
    if kind == "integer":
        _keys(column, allowed | {"nullable"}, f"column {name!r}")
        if not all(
            v is None or (isinstance(v, int) and not isinstance(v, bool))
            for v in values
        ):
            raise ValueError(f"column {name!r} holds a value that is not an integer")
        if column.get("nullable") or any(v is None for v in values):
            return pd.Series(values, dtype="Int64")
        return pd.Series(values, dtype="int64")
    if kind == "number":
        _keys(column, allowed, f"column {name!r}")
        if not all(
            v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
            for v in values
        ):
            raise ValueError(f"column {name!r} holds a value that is not a number")
        return pd.Series(
            [np.nan if v is None else float(v) for v in values], dtype="float64"
        )
    if kind == "text":
        _keys(column, allowed, f"column {name!r}")
        if not all(v is None or isinstance(v, str) for v in values):
            raise ValueError(f"column {name!r} holds a value that is not text")
        return pd.Series(values, dtype=_STRING)
    raise ValueError(
        f"column {name!r} is of type {kind!r}; the types are category, time, "
        "boolean, integer, number and text"
    )


# --- plain values ----------------------------------------------------------------


def _missing(value: Any) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float, np.integer, np.floating))
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(float(value))
    )


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is not text: {value!r}")
    return value


def _where_values(column: str, values: Any) -> list[Any]:
    if isinstance(values, (str, bool, int, float, np.generic)):
        values = [values]
    if not isinstance(values, Sequence) or not values:
        raise ValueError(f"where {column!r}: not a value or a list of them")
    plain = []
    for value in values:
        if isinstance(value, np.generic):
            value = value.item()
        if not isinstance(value, (str, bool, int, float)) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError(
                f"where {column!r}: {value!r} is not text, a number or a boolean"
            )
        plain.append(value)
    return plain


def _keys(document: Mapping[str, Any], allowed: set[str], what: str) -> None:
    unknown = set(document) - allowed
    if unknown:
        raise ValueError(
            f"{what} has no field {', '.join(sorted(map(str, unknown)))}; "
            f"it has {', '.join(sorted(allowed))}"
        )


def _plain(value: Any, where: str) -> Any:
    """*value* as plain JSON values: a NumPy scalar as a Python one, a
    number that is not finite as None, a time as ISO 8601, a tuple as a
    list."""
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{where}: the key {key!r} is not text")
            out[key] = _plain(item, f"{where}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(item, f"{where}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return None if value is pd.NaT else value.isoformat()
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, pd.Timedelta):
        return value.isoformat()
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    raise ValueError(f"{where}: {type(value).__name__} is not a JSON value")
