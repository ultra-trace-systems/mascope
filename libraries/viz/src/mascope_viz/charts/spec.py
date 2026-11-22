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
:meth:`Figure.from_json` reads it back to an equal figure (two figures are
equal when their documents are), which renders the same. What the document says, and nothing else, is what a renderer
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
  writes it as ``null``. A renderer draws a categorical's values in the
  order of its categories.
- **The context** (:class:`Context`): what the view behind the figure
  says of its values (the cadence, the statistic and band, the window and
  zone, the normalisation, its members and how complete it is, the run
  changes), as fields, so that nothing reading a figure parses its
  subtitle. The subtitle is written from it (:func:`subtitle`).
- **Display names.** A group column is a series' identity: its colour and
  its one legend entry. What a legend or a hover text shows is the
  panel's ``name`` column for that group (the option ``names`` for the
  lines of a list of y), so a key such as ``ion:12+ion:14`` is never shown
  for a name, and nothing reads meaning into a key.
- **Theme tokens by name.** A panel names colours (``series.1``,
  ``accent``, ``muted``, the ``success``, ``warning``, ``error`` and
  ``info`` states, the diverging ramp's ends ``diverging.low`` and
  ``diverging.high``), the palettes (``categorical``, ``sequential``,
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

#: The colours a panel may name for a group (option ``colours``): the
#: categorical palette's, the states', and the two ends of the diverging
#: ramp (below and above its centre).
COLOUR_TOKENS = (
    *(f"series.{i}" for i in range(1, 11)),
    "accent",
    "text",
    "muted",
    "success",
    "warning",
    "error",
    "info",
    "diverging.low",
    "diverging.high",
)

#: The kinds of span a time panel shades: an ``event`` (an episode, a period
#: compared) or a ``gap`` (no data).
SPAN_KINDS = ("event", "gap")

#: A number format a ``table`` column takes: a small subset of Python's
#: format specification, ``[,][.N](e|f|g|%)`` or ``[,]d``. A precision
#: without a type writes an integer as the float it equals, since Python
#: takes none on an integer. A renderer outside Python maps it to its own
#: (d3-format writes Python's ``g``, which drops trailing zeros, as ``~g``).
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
        to text: a table's headers, a series' display names) or
        ``formats`` (column to a number format).
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
_FLAG = Encoding(("boolean",))
#: A group's display name: what its legend entry and hover text say. The
#: group column is its identity (its colour, its one legend entry); the
#: name is only what a reader sees, so a key is never shown for a name.
_NAME = Encoding(("text",))
#: What an interactive renderer's hover text says of a mark, beside its
#: place; a static renderer has no hover and draws none.
_HOVER = Encoding(("text",))
_ANY = ("time", "number", "boolean", "text")
_TIME_MARKS = {"spans": Option("spans", ()), "rules": Option("rules", ())}

#: The eight primitives, by name: what each draws and from which columns.
PRIMITIVES: dict[str, Primitive] = {
    "series": Primitive(
        required={"x": _TIME_OR_NUMBER, "y": Encoding(("number",), many=True)},
        optional={"group": _GROUP, "name": _NAME, "lo": _NUMBER, "hi": _NUMBER},
        options={
            "mode": Option("mode", "marks"),
            "markers": Option("bool", False),
            "colours": Option("colours", {}),
            "names": Option("headers", {}),
            **_TIME_MARKS,
        },
        summary=(
            "lines over time or a number: one per group (named by name), or "
            "one per column of a list of y (named by the option names); lo "
            "and hi a band; a gap (NaN) breaks the line"
        ),
    ),
    "strip": Primitive(
        required={"x": Encoding(("time",)), "y": _GROUP},
        optional={"value": _NUMBER, "mark": _FLAG},
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
        optional={"gap": _FLAG},
        options={
            "mode": Option("mode", "marks"),
            "palette": Option("palette", "sequential"),
            "centre": Option("number", None),
            "gap_label": Option("text", "no samples"),
            "rules": Option("rules", ()),
        },
        summary=(
            "an x by y grid of cells coloured by value; a gap cell (no "
            "samples) in the gap style, a cell without a value blank; rules "
            "mark a moment of a time or number x over the cells"
        ),
    ),
    "scatter": Primitive(
        required={"x": _NUMBER, "y": _NUMBER},
        optional={
            "size": _NUMBER,
            "colour": Encoding(("number", "text", "boolean")),
            "name": _NAME,
            "label": _LABEL,
            "hollow": _FLAG,
        },
        options={
            "top_n": Option("int", 0),
            "palette": Option("palette", "sequential"),
            "colours": Option("colours", {}),
            "hollow_label": Option("text", ""),
        },
        summary=(
            "points: area in proportion to size, colour by column, hollow "
            "where hollow is true, larger behind; the top_n largest (by "
            "size, else y) labelled"
        ),
    ),
    "sticks": Primitive(
        required={"x": _NUMBER, "y": _NUMBER},
        optional={
            "group": _GROUP,
            "name": _NAME,
            "label": _LABEL,
            "mark": _FLAG,
            "hover": _HOVER,
        },
        options={
            "top_n": Option("int", 0),
            "colours": Option("colours", {}),
            "by_sign": Option("bool", False),
            "mark_label": Option("text", "marked"),
        },
        summary=(
            "vertical lines from zero, a spectrum, signed values below it; "
            "coloured by group or, by_sign, on the diverging ramp's ends; "
            "mark flags a stick at its tip; the top_n tallest labelled; "
            "hover what an interactive renderer says of a stick and its label"
        ),
    ),
    "bars": Primitive(
        required={"x": _GROUP, "y": _NUMBER},
        optional={"group": _GROUP, "name": _NAME, "lo": _NUMBER, "hi": _NUMBER},
        options={"stack": Option("bool", False), "colours": Option("colours", {})},
        summary=(
            "bars per category, groups side by side or stacked, an interval "
            "from lo to hi"
        ),
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
        optional={"group": _GROUP, "name": _NAME, "mean": _NUMBER, "hollow": _FLAG},
        options={"colours": Option("colours", {}), "hollow_label": Option("text", "")},
        summary=(
            "a box per category from its quartiles, whiskers from lo to hi, "
            "groups side by side; hollow where hollow is true"
        ),
    ),
    "table": Primitive(
        required={"columns": Encoding(_ANY, many=True)},
        optional={"mark": _FLAG},
        options={
            "headers": Option("headers", {}),
            "formats": Option("formats", {}),
            "max_rows": Option("int", 0),
            "mark_label": Option("text", "marked"),
        },
        summary=(
            "rows of the frame, its columns formatted; a row where mark is "
            "true shaded; max_rows caps them"
        ),
    ),
}

#: The primitives whose ``name`` encoding names the groups of a ``group``
#: column; a scatter's names those of its ``colour`` column.
_NAMED_BY_GROUP = ("series", "sticks", "bars", "box")

#: The primitives that draw an x and a y axis.
PLOTTED = tuple(name for name in PRIMITIVES if name != "table")

#: The axes that draw their column as categories, at 0, 1, 2 in order,
#: whatever its kind: a bars or a box panel's x, a strip's rows.
CATEGORY_AXES = frozenset({("bars", "x"), ("box", "x"), ("strip", "y")})


def axis_kind(panel: "Panel", kinds: Mapping[str, str], axis: str) -> str:
    """What a plotted *panel*'s *axis* draws: ``time``, ``number`` or
    ``category``. An axis of :data:`CATEGORY_AXES` draws categories; one
    with no column of its own (a list of y, a box's statistics) numbers;
    any other its column's kind, a text or a boolean as categories. The
    spec checks a scale and a range by it, and the renderers draw by it.

    :param kinds: The figure's column kinds (:meth:`Figure.kinds`).
    """
    if (panel.primitive, axis) in CATEGORY_AXES:
        return "category"
    column = panel.encodings.get(axis)
    if not isinstance(column, str):
        return "number"
    kind = kinds[column]
    return kind if kind in ("time", "number") else "category"


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


#: A panel's axes, in the document's order.
AXES = ("x", "y", "colour", "size")


@dataclass
class Axes:
    """A panel's axes: ``x``, ``y``, ``colour``, the scale of a colour by
    number (a heatmap's value, a scatter's or a strip's colour), and
    ``size``, whose label titles a scatter's size legend. A marker's area
    is in proportion to its size value, so the size axis takes a label and
    neither a log scale nor a range."""

    x: Axis = field(default_factory=Axis)
    y: Axis = field(default_factory=Axis)
    colour: Axis = field(default_factory=Axis)
    size: Axis = field(default_factory=Axis)

    def __post_init__(self) -> None:
        for name in AXES:
            value = getattr(self, name)
            if isinstance(value, Mapping):
                setattr(self, name, Axis.from_dict(value))
            elif not isinstance(value, Axis):
                raise TypeError(f"axes.{name} is not an Axis: {value!r}")
        if self.size.scale != "linear" or self.size.range is not None:
            raise ValueError(
                "the size axis takes a label only: a marker's area is in "
                "proportion to its value"
            )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name).to_dict() for name in AXES}

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "Axes":
        _keys(document, set(AXES), "the axes")
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


#: The fields of a figure's :class:`Context`, as the view frames' ``attrs``
#: name them.
CONTEXT_FIELDS = (
    "cadence",
    "stat",
    "band",
    "tz",
    "start",
    "end",
    "normalise",
    "normalise_name",
    "normalise_members",
    "normalise_complete",
    "mixed_runs",
    "run_changes",
)

#: The fields of a run change in :attr:`Context.run_changes`, as the store's
#: ``RunChange.to_dict`` writes them.
RUN_CHANGE_FIELDS = ("at", "sample_batch_id", "polarity", "before", "after")


@dataclass
class Context:
    """What the view behind a figure says of its values: its frame's
    ``attrs``, as data in the document. The subtitle is written from it
    (:func:`subtitle`), and whatever reads a figure later (the web app's
    renderer, a saved dashboard, the figure route's provenance) reads these
    fields, never the subtitle's text. Each field is None where the view
    does not say.

    :ivar cadence: What one point stands for: ``1h``, ``raw``, ``hour of
        day``, ``week``.
    :ivar stat: A bin's statistic: ``median``, ``mean``, ``min`` or ``max``.
    :ivar band: The percentiles of a bin's band, low then high (``[10,
        90]``).
    :ivar tz: The zone the times are shown in.
    :ivar start: The window's start, ISO 8601 with its offset.
    :ivar end: The window's end, the first moment after it.
    :ivar normalise: What the values are divided by: ``none``, ``tic``, a
        series key, or a list of series keys whose per-sample sum is the
        denominator.
    :ivar normalise_name: That, by name; a sum's members' names joined by
        ``" + "``.
    :ivar normalise_members: The keys the denominator sums, in order: one
        for a single key, none for ``none`` and ``tic``.
    :ivar normalise_complete: The share of the window's samples in which
        every member has a value, from 0 to 1.
    :ivar mixed_runs: Whether the window's batches carry more than one
        ledger reading.
    :ivar run_changes: Where the reading changes: each an object of
        :data:`RUN_CHANGE_FIELDS`, ``at`` a time.
    """

    cadence: str | None = None
    stat: str | None = None
    band: list[float] | None = None
    tz: str | None = None
    start: str | None = None
    end: str | None = None
    normalise: str | list[str] | None = None
    normalise_name: str | None = None
    normalise_members: list[str] | None = None
    normalise_complete: float | None = None
    mixed_runs: bool | None = None
    run_changes: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        for name in ("cadence", "stat", "tz", "normalise_name"):
            setattr(self, name, _maybe_text(getattr(self, name), f"context.{name}"))
        self.band = _band(self.band)
        self.start = _moment(self.start, "context.start")
        self.end = _moment(self.end, "context.end")
        if self.start is not None and self.end is not None:
            if not pd.Timestamp(self.start) < pd.Timestamp(self.end):
                raise ValueError("context: the window's start is not before its end")
        self.normalise = _normaliser(self.normalise)
        if self.normalise_members is not None:
            self.normalise_members = _texts(
                self.normalise_members, "context.normalise_members"
            )
        self.normalise_complete = _share(self.normalise_complete)
        if self.mixed_runs is not None:
            if not isinstance(self.mixed_runs, (bool, np.bool_)):
                raise ValueError(
                    f"context.mixed_runs={self.mixed_runs!r} is not true or false"
                )
            self.mixed_runs = bool(self.mixed_runs)
        if self.run_changes is not None:
            self.run_changes = [
                _run_change(change)
                for change in _list(self.run_changes, "context.run_changes")
            ]

    @classmethod
    def from_attrs(cls, attrs: Mapping[str, Any]) -> "Context":
        """The context a view frame's ``attrs`` give: the fields of
        :data:`CONTEXT_FIELDS` it has, a tuple as a list; the rest of its
        attrs (the view's name, its keys, the provenance) are not the
        figure's to carry."""
        if not isinstance(attrs, Mapping):
            raise ValueError("a view's attrs are a mapping")
        return cls(**{name: attrs.get(name) for name in CONTEXT_FIELDS})

    def to_dict(self) -> dict[str, Any]:
        return {
            name: (list(value) if isinstance(value, list) else value)
            for name, value in ((n, getattr(self, n)) for n in CONTEXT_FIELDS)
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "Context":
        if not isinstance(document, Mapping):
            raise ValueError("the context is a JSON object")
        _keys(document, set(CONTEXT_FIELDS), "the context")
        return cls(**document)


@dataclass(eq=False)
class Figure:
    """A chart: what every renderer draws, and the numbers behind it.

    Two figures are equal when their documents are (:meth:`to_dict`).

    :ivar id: The chart's id, as a file name takes it (``species_series``).
    :ivar title: What it shows.
    :ivar subtitle: How: the cadence, the normalisation, the window and the
        time zone of a chart over time. None, the default, writes it from
        the context (:func:`subtitle`); a text is drawn as given.
    :ivar panels: Its plots, in reading order.
    :ivar summary: The numbers a caption may cite, as plain JSON values.
    :ivar data: The frame behind it; every panel draws columns of it. Kept
        with a fresh index and no ``attrs``: what the figure holds is what
        its document says, the view's attrs in :attr:`context`.
    :ivar notes: Lines printed under it.
    :ivar columns: How many panels a row of it holds.
    :ivar share_x: Whether its panels share the x axis, zoom included:
        every plotted panel's, which are then all time or all numbers.
    :ivar context: What the view behind it says of its values
        (:class:`Context`, or a mapping of its fields).
    """

    id: str
    title: str
    subtitle: str | None = None
    panels: list[Panel] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: list[str] = field(default_factory=list)
    columns: int = 1
    share_x: bool = False
    context: Context = field(default_factory=Context)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID.match(self.id):
            raise ValueError(
                f"id={self.id!r} is not a name of letters, digits, '_', '.' and '-'"
            )
        self.title = _text(self.title, "the title")
        if isinstance(self.context, Mapping):
            self.context = Context.from_dict(self.context)
        elif not isinstance(self.context, Context):
            raise TypeError(f"context is not a Context: {self.context!r}")
        if self.subtitle is None:
            self.subtitle = subtitle(self.context)
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
        if self.share_x:
            _check_shared_x(self.panels, kinds)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Figure):
            return NotImplemented
        return self.to_dict() == other.to_dict()

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
            "context": self.context.to_dict(),
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
                "context",
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
            for key in ("id", "title", "summary", "notes", "columns", "share_x")
            if key in document
        }
        # A document's subtitle is drawn as it says, its absence as none:
        # only a figure made in code writes one from its context.
        fields["subtitle"] = document.get("subtitle", "")
        context = Context.from_dict(document.get("context", {}))
        return cls(panels=panels, data=data, context=context, **fields)

    @classmethod
    def from_json(cls, text: str | bytes) -> "Figure":
        """A figure from its JSON document (:meth:`to_json`)."""
        return cls.from_dict(json.loads(text))


#: A bin's statistic as a subtitle counts it.
_STAT_PLURALS = {"median": "medians", "mean": "means", "min": "minima", "max": "maxima"}


def subtitle(
    context: "Context | Mapping[str, Any] | None" = None,
    *,
    extra: Sequence[str] = (),
) -> str:
    """The subtitle a chart over time carries, written from what its view
    says, so that ten charts say it the same way.

    :param context: A figure's :class:`Context`, or a view frame's
        ``attrs`` (read as :meth:`Context.from_attrs` reads them).
    :param extra: More parts, in order.
    :return: The parts the context has, in this order: the cadence with
        the statistic and the band (``1h medians, p10-p90 band``, or
        ``every sample``); the normalisation, by name (``raw values``,
        ``normalised to I- + IH2O-``); the window, in the zone
        (``from 2026-07-01 00:00 to 2026-07-03 00:00``); the zone
        (``times in Europe/Helsinki``); then *extra*. ``"; "`` between.
    """
    if context is None:
        context = Context()
    elif not isinstance(context, Context):
        context = Context.from_attrs(context)
    parts = []
    stat = _STAT_PLURALS.get(context.stat, context.stat) if context.stat else None
    if context.cadence == "raw":
        parts.append("every sample")
    elif context.cadence or stat:
        if context.cadence and stat:
            how = f"{context.cadence} {stat}"
        else:
            how = stat or f"cadence {context.cadence}"
        if context.band is not None:
            low, high = context.band
            how += f", p{low:g}-p{high:g} band"
        parts.append(how)
    if context.normalise is not None:
        parts.append(
            "raw values"
            if context.normalise == "none"
            else f"normalised to {_normaliser_name(context)}"
        )
    window = _window_text(context)
    if window:
        parts.append(window)
    if context.tz:
        parts.append(f"times in {context.tz}")
    parts.extend(_text(part, "a subtitle part") for part in extra)
    return "; ".join(parts)


def _normaliser_name(context: Context) -> str:
    """What the values are divided by, as a reader knows it: the view's
    name for it, else its members' or its keys joined by ``" + "``."""
    if context.normalise_name:
        return context.normalise_name
    if context.normalise_members:
        return " + ".join(context.normalise_members)
    normalise = context.normalise
    return normalise if isinstance(normalise, str) else " + ".join(normalise or [])


def _window_text(context: Context) -> str:
    def moment(iso: str) -> str:
        at = pd.Timestamp(iso)
        if context.tz:
            try:
                return at.tz_convert(context.tz).strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError, KeyError):
                pass
        return at.strftime("%Y-%m-%d %H:%M%z")

    if context.start and context.end:
        return f"from {moment(context.start)} to {moment(context.end)}"
    if context.start:
        return f"from {moment(context.start)}"
    if context.end:
        return f"until {moment(context.end)}"
    return ""


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
            kind = axis_kind(panel, kinds, axis)
            if kind == "number":
                continue
            column = panel.encodings[axis]
            drawn = (
                f"{axis} draws {column!r} as categories"
                if kind == "category"
                else f"{axis} draws {column!r}, which is {kind}"
            )
            spec = getattr(panel.axes, axis)
            if spec.scale == "log":
                raise ValueError(f"{where}: a log {axis} axis draws numbers; {drawn}")
            if spec.range is not None:
                raise ValueError(
                    f"{where}: a range sets the ends of a number axis; {drawn}"
                )
        if panel.primitive in ("sticks", "bars") and panel.axes.y.scale == "log":
            raise ValueError(
                f"{where}: {panel.primitive} rise from zero, which a log axis does "
                "not have"
            )
    encodings = panel.encodings
    if panel.primitive == "series":
        if not isinstance(encodings["y"], str) and (
            {"group", "name", "lo", "hi"} & set(encodings)
        ):
            raise ValueError(
                f"{where}: a list of y draws one line per column, with no group "
                "and no band; the option names names its lines"
            )
        names = panel.options.get("names") or {}
        if names:
            columns = [] if isinstance(encodings["y"], str) else encodings["y"]
            unknown = [column for column in names if column not in columns]
            if unknown:
                raise ValueError(
                    f"{where}: the option names names the lines of a list of y; "
                    f"{', '.join(map(repr, unknown))} is none of them"
                )
    if "name" in encodings:
        grouped_by = "colour" if panel.primitive == "scatter" else "group"
        column = encodings.get(grouped_by)
        if column is None or (grouped_by == "colour" and kinds[column] == "number"):
            raise ValueError(
                f"{where}: name names the groups of its {grouped_by} column, "
                f"and the panel has no {'categorical ' * (grouped_by == 'colour')}"
                f"{grouped_by}"
            )
    if panel.primitive == "heatmap" and panel.options.get("rules"):
        if axis_kind(panel, kinds, "x") == "category":
            raise ValueError(
                f"{where}: a rule marks a moment of a time or number x; "
                f"x draws {panel.encodings['x']!r} as categories"
            )
    if panel.primitive == "sticks" and panel.options.get("by_sign"):
        if "group" in encodings:
            raise ValueError(
                f"{where}: by_sign colours sticks by their sign, a group by its "
                "own colour: one or the other"
            )
    if panel.primitive == "bars" and panel.options.get("stack"):
        if {"lo", "hi"} & set(encodings):
            raise ValueError(f"{where}: stacked bars draw no error bars")
    if ("lo" in encodings) != ("hi" in encodings) and panel.primitive in (
        "series",
        "bars",
    ):
        raise ValueError(f"{where}: lo and hi come together")
    panel.options = _options(panel, _x_axis(panel, kinds, zones), where)


def _check_shared_x(panels: Sequence[Panel], kinds: Mapping[str, str]) -> None:
    """Panels that share their x axis draw one kind of x: all time or all
    numbers. A categorical axis (bars, boxes, a heatmap's text) places its
    categories at 0, 1, 2, which another panel's x would not share."""
    seen: dict[str, str] = {}
    for index, panel in enumerate(panels):
        if panel.primitive not in PLOTTED:
            continue
        where = f"panel {index + 1} ({panel.primitive})"
        kind = axis_kind(panel, kinds, "x")
        if kind == "category":
            raise ValueError(
                f"share_x joins time or number x axes; {where} draws categories"
            )
        seen.setdefault(kind, where)
    if len(seen) > 1:
        raise ValueError(
            "share_x joins x axes of one kind; "
            + " and ".join(f"{where} draws {kind}" for kind, where in seen.items())
        )


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
        # A count and pandas' unit (``1h``, ``90min``, ``1D``), so that a
        # Timedelta or an offset given in code reads back from the document.
        offset = pd.tseries.frequencies.to_offset(value)
        return f"{offset.n}{offset.rule_code}"
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


def _maybe_text(value: Any, label: str) -> str | None:
    return None if value is None else _text(value, label)


def _texts(values: Any, label: str) -> list[str]:
    return [_text(value, f"{label} item") for value in _list(values, label)]


def _band(band: Any) -> list[float] | None:
    """A band's two percentiles, low then high, from 0 to 100."""
    if band is None:
        return None
    ends = _list(band, "context.band")
    if len(ends) != 2 or not all(_finite(end) for end in ends):
        raise ValueError(f"context.band={band!r} is not two percentiles")
    low, high = (float(end) for end in ends)
    if not 0 <= low < high <= 100:
        raise ValueError(
            f"context.band={band!r}: percentiles go from 0 to 100, low first"
        )
    return [low, high]


def _moment(value: Any, label: str) -> str | None:
    """A time as ISO 8601 with its offset: a time without one is no
    instant."""
    if value is None:
        return None
    if not isinstance(value, (str, datetime.datetime)):
        raise ValueError(f"{label}={value!r} is not a time")
    try:
        moment = pd.Timestamp(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{label}={value!r} is not a time") from e
    if pd.isna(moment):
        raise ValueError(f"{label} is not a time")
    if moment.tz is None:
        raise ValueError(f"{label}={value!r} has no offset")
    return moment.isoformat()


def _normaliser(value: Any) -> str | list[str] | None:
    """``normalise``: a text (``none``, ``tic``, a key) or a list of keys
    whose sum is the denominator."""
    if value is None or isinstance(value, str):
        return value
    keys = _texts(value, "context.normalise")
    if not keys:
        raise ValueError("context.normalise is an empty list: a sum needs a key")
    return keys


def _share(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, np.floating)) and math.isnan(value):
        return None
    if not _finite(value) or not 0 <= float(value) <= 1:
        raise ValueError(f"context.normalise_complete={value!r} is no share in [0, 1]")
    return float(value)


def _run_change(change: Any) -> dict[str, Any]:
    if not isinstance(change, Mapping):
        raise ValueError("context.run_changes: a run change is an object")
    _keys(change, set(RUN_CHANGE_FIELDS), "context.run_changes: a run change")
    if change.get("at") is None:
        raise ValueError("context.run_changes: a run change has no time")
    out: dict[str, Any] = {"at": _moment(change["at"], "context.run_changes: at")}
    for name in RUN_CHANGE_FIELDS[1:]:
        out[name] = _maybe_text(change.get(name), f"context.run_changes: {name}")
    return out


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
    number that is not finite as None, a time as ISO 8601 (a NumPy time in
    UTC), a duration as ISO 8601, a tuple as a list."""
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{where}: the key {key!r} is not text")
            out[key] = _plain(item, f"{where}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(item, f"{where}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, np.datetime64):
        # NumPy keeps a zoned column's times in UTC and names no zone, so
        # its time is read in UTC; .item() would give its nanoseconds.
        return None if np.isnat(value) else pd.Timestamp(value, tz="UTC").isoformat()
    if isinstance(value, np.timedelta64):
        value = pd.Timedelta(value)
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
