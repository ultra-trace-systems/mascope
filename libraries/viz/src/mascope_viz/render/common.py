"""What both renderers compute alike, so a spec draws the same in each.

The colour of each group, the order of categories, where a line breaks,
which points carry labels, how a number maps to a colour, where a bar or a
box stands, how a table cell reads and which notes a figure prints: each is
decided here once, from the spec alone, and each renderer only draws the
result. A primitive that drifts between the backends is a bug (the note's
section 5); keeping the decisions here is what keeps them from drifting.

Pandas and NumPy only.
"""

import math
import textwrap
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..charts.spec import Figure, Panel
from . import theme as themes


#: The note a figure carries for a panel that asked for the raster mode.
RASTER_NOTE = (
    "{where} asks to be rasterised, which needs the [big] extra's datashader "
    "path; this version has none and draws it as marks."
)


# --- groups and their colours ---------------------------------------------------


def label(value: Any) -> str:
    """A group's or a category's value as the text that names it."""
    if isinstance(value, (bool, np.bool_)):
        return "true" if value else "false"
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value)


def ordered(column: pd.Series) -> list[Any]:
    """*column*'s distinct values in the order they first appear, missing
    values left out."""
    return list(pd.unique(column.dropna()))


def _grouping(fig: Figure, panel: Panel) -> list[str]:
    """The group names *panel* colours, in the order it draws them."""
    encodings = panel.encodings
    frame = fig.frame(panel)
    if panel.primitive == "series" and not isinstance(encodings["y"], str):
        return list(encodings["y"])
    column = None
    if panel.primitive in ("series", "sticks", "bars", "box"):
        column = encodings.get("group")
    elif panel.primitive == "scatter":
        colour = encodings.get("colour")
        if colour is not None and fig.kinds()[colour] != "number":
            column = colour
    if column is None:
        return []
    return [label(value) for value in ordered(frame[column])]


@dataclass(frozen=True)
class Colours:
    """The colour of every group of a figure: the same group, the same
    colour, in every panel.

    :ivar groups: Each group's colour, in the order the groups first
        appear.
    :ivar single: The colour of a panel that draws no groups.
    """

    groups: dict[str, str]
    single: str

    def of(self, group: str | None) -> str:
        return self.single if group is None else self.groups[group]


def colours(fig: Figure, theme: themes.Theme) -> Colours:
    """Each group's colour across *fig*: the colour a panel's ``colours``
    option names for it (the first panel that names one), else the next
    categorical colour, in the order the groups first appear panel by
    panel."""
    named: dict[str, str] = {}
    order: list[str] = []
    for panel in fig.panels:
        tokens = panel.options.get("colours") or {}
        for group in _grouping(fig, panel):
            if group not in order:
                order.append(group)
            if group in tokens and group not in named:
                named[group] = theme.token(tokens[group])
    groups = {}
    palette = theme.categorical
    index = 0
    for group in order:
        if group in named:
            groups[group] = named[group]
        else:
            groups[group] = palette[index % len(palette)]
            index += 1
    return Colours(groups=groups, single=palette[0])


# --- numbers --------------------------------------------------------------------


def numbers(column: pd.Series) -> np.ndarray:
    """A number column as floats, a missing value NaN: a copy, which a
    caller may write."""
    values = pd.to_numeric(column, errors="coerce")
    return np.array(values.to_numpy(dtype="float64", na_value=np.nan), copy=True)


def on_scale(values: np.ndarray, scale: str) -> np.ndarray:
    """*values* as an axis of *scale* draws them: on a log axis a value that
    is not positive is a gap."""
    if scale != "log":
        return values
    out = values.astype("float64", copy=True)
    with np.errstate(invalid="ignore"):
        out[~(out > 0)] = np.nan
    return out


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """The ``[start, stop)`` of every run of True in *mask*."""
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    change = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(change[::2].tolist(), change[1::2].tolist()))


def isolated(values: np.ndarray) -> np.ndarray:
    """Where a value stands alone between gaps: a line through it would
    draw nothing, so it is drawn as a point."""
    finite = np.isfinite(values)
    before = np.concatenate([[False], finite[:-1]])
    after = np.concatenate([finite[1:], [False]])
    return finite & ~before & ~after


def top(values: np.ndarray, n: int) -> np.ndarray:
    """The positions of the *n* largest finite *values*, largest first, a
    tie in the order the values come."""
    if n <= 0:
        return np.array([], dtype=int)
    finite = np.flatnonzero(np.isfinite(values))
    ranked = finite[np.argsort(-values[finite], kind="stable")]
    return ranked[:n]


def sizes(values: np.ndarray, theme: themes.Theme) -> np.ndarray:
    """Marker diameters in CSS pixels, their area in proportion to *values*
    between the theme's smallest and largest; a missing or negative value
    at the smallest."""
    low, high = theme.marks.size_min, theme.marks.size_max
    finite = np.isfinite(values) & (values >= 0)
    out = np.full(len(values), low, dtype="float64")
    if not finite.any():
        return out
    roots = np.sqrt(values[finite])
    top_root = roots.max()
    if top_root == 0:
        return out
    out[finite] = low + (high - low) * roots / top_root
    return out


# --- colour scales --------------------------------------------------------------


@dataclass(frozen=True)
class ColourScale:
    """A number's colour: the values on the scale (log10 of them on a log
    scale), the scale's ends, the colour stops, and the ticks of a log
    scale's bar as ``(position, text)``."""

    values: np.ndarray
    low: float
    high: float
    stops: tuple[str, ...]
    ticks: tuple[tuple[float, str], ...]
    title: str

    def colour(self, value: float) -> str | None:
        """The colour of a value on the scale; None for a gap."""
        if not math.isfinite(value):
            return None
        share = (value - self.low) / (self.high - self.low)
        return themes.interpolate(self.stops, share)


def colour_scale(
    values: np.ndarray,
    panel: Panel,
    theme: themes.Theme,
    palette: str = "sequential",
) -> ColourScale:
    """The colour scale of *values* as *panel*'s ``colour`` axis says: its
    scale, its range, and for a diverging palette its centre (the option
    ``centre``; 0, or 1 on a log scale, without it), which the scale's ends
    stand around evenly."""
    axis = panel.axes.colour
    log = axis.scale == "log"
    scaled = np.log10(on_scale(values, "log")) if log else values.astype("float64")
    finite = scaled[np.isfinite(scaled)]
    if axis.range is not None:
        low, high = (math.log10(end) if log else end for end in axis.range)
    elif palette == "diverging":
        centre = panel.options.get("centre")
        if centre is None:
            centre = 1.0 if log else 0.0
        if log and centre <= 0:
            centre = 1.0
        middle = math.log10(centre) if log else centre
        reach = float(np.max(np.abs(finite - middle))) if finite.size else 0.0
        reach = reach or 1.0
        low, high = middle - reach, middle + reach
    elif finite.size:
        low, high = float(finite.min()), float(finite.max())
    else:
        low, high = 0.0, 1.0
    if not high > low:
        low, high = low - 0.5, high + 0.5
    ticks = _log_ticks(low, high) if log else ()
    return ColourScale(
        values=scaled,
        low=low,
        high=high,
        stops=theme.palette(palette),
        ticks=ticks,
        title=axis.label,
    )


def _log_ticks(low: float, high: float) -> tuple[tuple[float, str], ...]:
    """Ticks of a log scale from *low* to *high* (log10): the powers of ten,
    and 2 and 5 times them when fewer than two powers fall inside."""
    decades = range(math.ceil(low - 1e-9), math.floor(high + 1e-9) + 1)
    multiples = (1,) if len(decades) >= 2 else (1, 2, 5)
    ticks = []
    for k in range(math.floor(low) - 1, math.ceil(high) + 1):
        for m in multiples:
            at = k + math.log10(m)
            if low - 1e-9 <= at <= high + 1e-9:
                ticks.append((at, _power_text(m * 10.0**k)))
    return tuple(ticks)


def _power_text(value: float) -> str:
    if 1e-3 <= value < 1e6:
        return f"{value:g}"
    exponent = math.floor(math.log10(value) + 1e-9)
    mantissa = value / 10**exponent
    return f"{mantissa:g}e{exponent}"


# --- grids -----------------------------------------------------------------------


@dataclass(frozen=True)
class Grid:
    """A heatmap's cells: the x and y values in drawing order (times and
    numbers ascending, categories as they first appear) and the value of
    each cell, NaN where the frame has none. A cell the frame repeats keeps
    its last row."""

    xs: list[Any]
    ys: list[Any]
    values: np.ndarray


def grid(fig: Figure, panel: Panel) -> Grid:
    frame = fig.frame(panel)
    kinds = fig.kinds()
    x, y, value = (panel.encodings[name] for name in ("x", "y", "value"))
    xs, x_at = _axis_values(frame[x], kinds[x])
    ys, y_at = _axis_values(frame[y], kinds[y])
    values = np.full((len(ys), len(xs)), np.nan)
    present = (x_at >= 0) & (y_at >= 0)
    values[y_at[present], x_at[present]] = numbers(frame[value])[present]
    return Grid(xs=xs, ys=ys, values=values)


def _axis_values(column: pd.Series, kind: str) -> tuple[list[Any], np.ndarray]:
    if kind in ("time", "number"):
        distinct = column.dropna().drop_duplicates().sort_values()
    else:
        distinct = pd.Series(ordered(column), dtype=object)
    index = pd.Index(distinct.tolist())
    at = index.get_indexer(column.tolist())
    return distinct.tolist(), np.asarray(at)


def edges(centres: np.ndarray) -> np.ndarray:
    """The edges of cells centred on *centres* (ascending): halfway between
    two centres, and half a step out at the ends, as plotly places a
    heatmap's cells."""
    centres = np.asarray(centres, dtype="float64")
    if len(centres) == 0:
        return np.array([0.0, 1.0])
    if len(centres) == 1:
        return np.array([centres[0] - 0.5, centres[0] + 0.5])
    middle = (centres[:-1] + centres[1:]) / 2
    first = centres[0] - (middle[0] - centres[0])
    last = centres[-1] + (centres[-1] - middle[-1])
    return np.concatenate([[first], middle, [last]])


# --- strips ------------------------------------------------------------------------


@dataclass(frozen=True)
class Strip:
    """A strip's rows, top to bottom, and its cells: ``row`` (its index),
    ``start`` and ``end`` (times), ``colour`` (None where the row has no
    value there, which is drawn as no cell) and ``marked``."""

    rows: list[str]
    cells: pd.DataFrame
    scale: ColourScale | None
    width: pd.Timedelta


def strip(fig: Figure, panel: Panel, theme: themes.Theme) -> Strip:
    """A strip's cells: one per row of the frame, as wide as the ``width``
    option, else the smallest step between two of the frame's times (an
    hour when it has one time). A row with a ``value`` has a cell where the
    value is positive, shaded on the sequential palette; without one, every
    row of the frame is a cell in the first categorical colour."""
    frame = fig.frame(panel)
    x, y = panel.encodings["x"], panel.encodings["y"]
    rows = [label(value) for value in ordered(frame[y])]
    width = _strip_width(frame[x], panel.options.get("width"))
    keep = frame[y].notna().to_numpy() & frame[x].notna().to_numpy()
    frame = frame[keep]
    row = pd.Index(rows).get_indexer([label(value) for value in frame[y]])
    scale = None
    if "value" in panel.encodings:
        values = numbers(frame[panel.encodings["value"]])
        present = np.isfinite(values) & (values > 0)
        scale = colour_scale(np.where(present, values, np.nan), panel, theme)
        cell_colours = [scale.colour(v) for v in scale.values]
    else:
        cell_colours = [theme.categorical[0]] * len(frame)
    marked = (
        frame[panel.encodings["mark"]].fillna(False).to_numpy(dtype=bool)
        if "mark" in panel.encodings
        else np.zeros(len(frame), dtype=bool)
    )
    start = frame[x].reset_index(drop=True)
    cells = pd.DataFrame(
        {
            "row": row,
            "start": start,
            "end": start + width,
            "colour": pd.Series(cell_colours, dtype=object),
            "marked": marked,
        }
    )
    return Strip(rows=rows, cells=cells, scale=scale, width=width)


def _strip_width(times: pd.Series, option: str | None) -> pd.Timedelta:
    if option is not None:
        return pd.Timedelta(pd.tseries.frequencies.to_offset(option))
    distinct = times.dropna().drop_duplicates().sort_values()
    steps = distinct.diff().dropna()
    steps = steps[steps > pd.Timedelta(0)]
    return steps.min() if len(steps) else pd.Timedelta("1h")


# --- bars and boxes ------------------------------------------------------------------


@dataclass(frozen=True)
class Places:
    """Where a categorical panel's marks stand: the categories in order
    (their position is their index), the groups in order (one None for a
    panel without), and each group's offset from its category and width."""

    categories: list[str]
    groups: list[str | None]
    offsets: list[float]
    width: float


def places(fig: Figure, panel: Panel, theme: themes.Theme) -> Places:
    """Categories side by side, the groups of one category side by side in
    the theme's bar width, or on one another for stacked bars."""
    frame = fig.frame(panel)
    categories = [label(v) for v in ordered(frame[panel.encodings["x"]])]
    group = panel.encodings.get("group")
    groups: list[str | None] = (
        [label(v) for v in ordered(frame[group])] if group else [None]
    )
    total = theme.marks.bar_width
    if panel.options.get("stack") or len(groups) == 1:
        return Places(categories, groups, [0.0] * len(groups), total)
    width = total / len(groups)
    offsets = [(i - (len(groups) - 1) / 2) * width for i in range(len(groups))]
    return Places(categories, groups, offsets, width)


def rows_of(frame: pd.DataFrame, panel: Panel, group: str | None) -> pd.DataFrame:
    """The rows of *frame* in *group* (every row for None)."""
    column = panel.encodings.get("group")
    if group is None or column is None:
        return frame
    return frame[[label(v) == group for v in frame[column]]]


def bars(fig: Figure, panel: Panel, theme: themes.Theme) -> tuple[Places, pd.DataFrame]:
    """Every bar: its ``group``, ``position`` (category index plus the
    group's offset), ``category``, ``bottom``, ``height``, ``err_lo`` and
    ``err_hi`` (NaN without lo and hi). A category's last row in a group
    gives its bar; a stacked bar sits on the group before it on its side of
    zero."""
    place = places(fig, panel, theme)
    frame = fig.frame(panel)
    x = panel.encodings["x"]
    stack = panel.options.get("stack")
    up = np.zeros(len(place.categories))
    down = np.zeros(len(place.categories))
    records = []
    for group, offset in zip(place.groups, place.offsets):
        rows = rows_of(frame, panel, group)
        by_category = {label(v): i for i, v in enumerate(rows[x])}
        values = numbers(rows[panel.encodings["y"]])
        lo = numbers(rows[panel.encodings["lo"]]) if "lo" in panel.encodings else None
        hi = numbers(rows[panel.encodings["hi"]]) if "hi" in panel.encodings else None
        for c, category in enumerate(place.categories):
            if category not in by_category:
                continue
            i = by_category[category]
            height = values[i]
            if not math.isfinite(height):
                continue
            bottom = 0.0
            if stack:
                if height >= 0:
                    bottom, up[c] = up[c], up[c] + height
                else:
                    bottom, down[c] = down[c], down[c] + height
            records.append(
                {
                    "group": group,
                    "category": category,
                    "position": c + offset,
                    "bottom": bottom,
                    "height": height,
                    "err_lo": height - lo[i] if lo is not None else np.nan,
                    "err_hi": hi[i] - height if hi is not None else np.nan,
                }
            )
    columns = ["group", "category", "position", "bottom", "height", "err_lo", "err_hi"]
    return place, pd.DataFrame(records, columns=columns)


#: The statistics of a box, by encoding.
BOX_STATS = ("q1", "median", "q3", "lo", "hi")


def boxes(
    fig: Figure, panel: Panel, theme: themes.Theme
) -> tuple[Places, pd.DataFrame]:
    """Every box: its ``group``, ``category``, ``position`` and its
    statistics (``q1``, ``median``, ``q3``, ``lo``, ``hi``, ``mean``, NaN
    without one). A box missing any of the five is not drawn."""
    place = places(fig, panel, theme)
    frame = fig.frame(panel)
    x = panel.encodings["x"]
    records = []
    for group, offset in zip(place.groups, place.offsets):
        rows = rows_of(frame, panel, group)
        stats = {name: numbers(rows[panel.encodings[name]]) for name in BOX_STATS}
        mean = (
            numbers(rows[panel.encodings["mean"]])
            if "mean" in panel.encodings
            else None
        )
        by_category = {label(v): i for i, v in enumerate(rows[x])}
        for c, category in enumerate(place.categories):
            if category not in by_category:
                continue
            i = by_category[category]
            values = {name: stats[name][i] for name in BOX_STATS}
            if not all(math.isfinite(v) for v in values.values()):
                continue
            records.append(
                {
                    "group": group,
                    "category": category,
                    "position": c + offset,
                    **values,
                    "mean": mean[i] if mean is not None else np.nan,
                }
            )
    columns = ["group", "category", "position", *BOX_STATS, "mean"]
    return place, pd.DataFrame(records, columns=columns)


# --- lines -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One line of a series panel, in x order: its group (None for a panel
    without), its x, its y and its band (None without one), each value a
    gap where the axis cannot draw it."""

    group: str | None
    x: pd.Series
    y: np.ndarray
    lo: np.ndarray | None
    hi: np.ndarray | None


def lines(fig: Figure, panel: Panel) -> list[Line]:
    """A series panel's lines: one per column of a list of y, else one per
    group, else one."""
    frame = fig.frame(panel)
    encodings = panel.encodings
    x = encodings["x"]
    scale = panel.axes.y.scale
    frame = frame.sort_values(x, kind="stable")
    if not isinstance(encodings["y"], str):
        return [
            Line(
                group=column,
                x=frame[x].reset_index(drop=True),
                y=on_scale(numbers(frame[column]), scale),
                lo=None,
                hi=None,
            )
            for column in encodings["y"]
        ]
    if "group" in encodings:
        column = frame[encodings["group"]]
        names = [label(v) if not _missing(v) else None for v in column]
        parts = [
            (name, frame[[n == name for n in names]])
            for name in (label(v) for v in ordered(column))
        ]
    else:
        parts = [(None, frame)]
    out = []
    for name, rows in parts:
        band = "lo" in encodings
        out.append(
            Line(
                group=name,
                x=rows[x].reset_index(drop=True),
                y=on_scale(numbers(rows[encodings["y"]]), scale),
                lo=on_scale(numbers(rows[encodings["lo"]]), scale) if band else None,
                hi=on_scale(numbers(rows[encodings["hi"]]), scale) if band else None,
            )
        )
    return out


def _missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


# --- labels -------------------------------------------------------------------------------


def labelled(fig: Figure, panel: Panel) -> pd.DataFrame:
    """The points a scatter or sticks panel labels: the ``top_n`` largest,
    by size where it has one, by |y| for sticks, else by y; ``x``, ``y``
    and ``text``."""
    n = panel.options.get("top_n", 0)
    encodings = panel.encodings
    if not n or "label" not in encodings:
        return pd.DataFrame({"x": [], "y": [], "text": []})
    frame = fig.frame(panel)
    x = on_scale(numbers(frame[encodings["x"]]), panel.axes.x.scale)
    y = on_scale(numbers(frame[encodings["y"]]), panel.axes.y.scale)
    if "size" in encodings:
        by = numbers(frame[encodings["size"]])
    elif panel.primitive == "sticks":
        by = np.abs(y)
    else:
        by = y.copy()
    by[~(np.isfinite(x) & np.isfinite(y))] = np.nan
    texts = frame[encodings["label"]]
    chosen = [i for i in top(by, n) if not _missing(texts.iloc[i])]
    return pd.DataFrame(
        {
            "x": x[chosen],
            "y": y[chosen],
            "text": [label(texts.iloc[i]) for i in chosen],
        }
    )


# --- tables ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Table:
    """A table panel as text: its headers, its rows, which columns are
    numbers (set to the right), and how many rows the frame had."""

    headers: list[str]
    rows: list[list[str]]
    numeric: list[bool]
    total: int


def table(fig: Figure, panel: Panel) -> Table:
    """A table's cells: each column formatted by its ``formats`` entry
    (numbers ``g``, integers ``d`` without one), a time to the minute in
    its zone (to the second when one has seconds), a boolean ``yes`` or
    ``no``, a missing value empty; at most ``max_rows`` rows when it is not
    0."""
    frame = fig.frame(panel)
    columns = panel.encodings["columns"]
    headers = panel.options.get("headers") or {}
    formats = panel.options.get("formats") or {}
    limit = panel.options.get("max_rows") or 0
    shown = frame.iloc[:limit] if limit else frame
    kinds = fig.kinds()
    cells = [
        _cells(shown[column], kinds[column], formats.get(column)) for column in columns
    ]
    return Table(
        headers=[headers.get(column, column) for column in columns],
        rows=[list(row) for row in zip(*cells)] if cells else [],
        numeric=[kinds[column] == "number" for column in columns],
        total=len(frame),
    )


def _cells(column: pd.Series, kind: str, spec: str | None) -> list[str]:
    if kind == "time":
        seconds = any(
            v is not pd.NaT and (v.second or v.microsecond or v.nanosecond)
            for v in column
        )
        pattern = "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M"
        return ["" if v is pd.NaT else v.strftime(pattern) for v in column]
    out = []
    for value in column.astype(object):
        if _missing(value):
            out.append("")
        elif kind == "boolean":
            out.append("yes" if value else "no")
        elif kind == "number":
            integer = isinstance(value, (int, np.integer))
            out.append(format(value, spec or ("d" if integer else "g")))
        else:
            out.append(str(value))
    return out


# --- notes ------------------------------------------------------------------------------------


def notes(fig: Figure) -> list[str]:
    """What a renderer prints under the figure: its notes, then a line for
    each panel that asked for the raster mode and was drawn as marks, and
    for each table that shows only its first rows."""
    out = list(fig.notes)
    for index, panel in enumerate(fig.panels):
        where = where_label(index, panel)
        if panel.options.get("mode") == "raster":
            out.append(RASTER_NOTE.format(where=where))
        if panel.primitive == "table":
            limit = panel.options.get("max_rows") or 0
            total = len(fig.frame(panel))
            if limit and total > limit:
                out.append(f"{where} shows the first {limit} of {total} rows.")
    return out


def note_lines(fig: Figure, theme: themes.Theme) -> list[str]:
    """:func:`notes` as the lines a renderer prints: each note wrapped to
    the figure's width at the theme's small type."""
    width = max(40, int(theme.layout.width / (theme.type.small * 0.6)))
    lines = []
    for note in notes(fig):
        lines.extend(textwrap.wrap(note, width=width) or [""])
    return lines


def drawn_mode(panel: Panel) -> str:
    """The mode a renderer of this version draws *panel* in: always marks
    (:data:`RASTER_NOTE`)."""
    return "marks"


# --- layout ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Arrangement:
    """Where each panel goes, ``(row, column)`` from 0, and each row's
    height and the header's and footer's, in CSS pixels."""

    cells: list[tuple[int, int]]
    rows: int
    columns: int
    heights: list[float]
    header: float
    footer: float

    @property
    def total(self) -> float:
        return self.header + sum(self.heights) + self.footer


def arrange(fig: Figure, theme: themes.Theme) -> Arrangement:
    """Panels in reading order, :attr:`Figure.columns` to a row; a row as
    tall as its tallest panel: a plot the theme's panel height, a table
    its rows' (as many as it shows, its header and its title)."""
    columns = min(fig.columns, len(fig.panels))
    rows = math.ceil(len(fig.panels) / columns)
    cells = [(i // columns, i % columns) for i in range(len(fig.panels))]
    layout = theme.layout
    heights = [0.0] * rows
    for (row, _), panel in zip(cells, fig.panels):
        if panel.primitive == "table":
            shown = len(fig.frame(panel))
            limit = panel.options.get("max_rows") or 0
            if limit:
                shown = min(shown, limit)
            height = layout.table_row * (shown + 1) + (32 if panel.title else 12)
        else:
            height = layout.panel
        heights[row] = max(heights[row], height)
    footer = layout.note * len(note_lines(fig, theme))
    header = layout.header if fig.subtitle else layout.header - theme.type.subtitle - 6
    return Arrangement(
        cells, rows, columns, heights, header, footer + (8 if footer else 0)
    )


def where_label(index: int, panel: Panel) -> str:
    """How a note names a panel: ``Panel 2 (its title)``."""
    return f"Panel {index + 1}" + (f" ({panel.title})" if panel.title else "")


def time_kind(fig: Figure, panel: Panel, axis: str) -> bool:
    column = panel.encodings.get(axis)
    return isinstance(column, str) and fig.kinds()[column] == "time"


def kind_of(fig: Figure, panel: Panel, axis: str) -> str | None:
    column = panel.encodings.get(axis)
    return fig.kinds()[column] if isinstance(column, str) else None
