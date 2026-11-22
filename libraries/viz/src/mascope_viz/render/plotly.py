"""The plotly renderer: a Figure spec as a plotly figure, for the HTML
dashboard.

plotly.js draws times as wall-clock times, without a zone, so a time axis
shows each value at its time in the column's zone: the zone the figure's
subtitle and axis label name. Every text from the spec is HTML-escaped,
since plotly.js reads a subset of HTML in its labels: a name is drawn as
written, never as markup or a link.

plotly is imported here alone, when a figure is drawn.
"""

import html
from typing import Any

import numpy as np
import pandas as pd

from ..charts.spec import Figure, Panel
from . import common
from . import theme as themes


#: CSS pixels between two rows of panels.
ROW_GAP = 96.0

#: CSS pixels under the bottom panels for their tick labels and axis title.
AXIS_ROOM = 72.0

#: plotly.js's configuration for a figure: no logo, no upload to plotly's
#: cloud (the app's BaseChartPlotly.vue turns it off too: a chart draws
#: customer measurements).
CONFIG = {"displaylogo": False, "showSendToCloud": False, "responsive": True}


def render(fig: Figure, *, theme: "str | themes.Theme" = "light") -> Any:
    """*fig* drawn with plotly in *theme* (``light`` or ``dark``).

    :return: A :class:`plotly.graph_objects.Figure`. Save it with
        :func:`mascope_viz.render.save`, which writes one self-contained
        HTML file whose bytes do not change from run to run.
    """
    return _Drawing(fig, themes.get(theme)).draw()


def escape(text: str) -> str:
    """*text* as plotly.js draws it literally."""
    return html.escape(text, quote=False)


def _rgba(colour: str, alpha: float) -> str:
    r, g, b = (round(c * 255) for c in themes.rgb(colour))
    return f"rgba({r},{g},{b},{alpha})"


#: A day in milliseconds, plotly.js's time unit: the width of a lone
#: heatmap cell on a time axis, which :func:`common.edges` makes one unit
#: of matplotlib's, a day.
DAY_MS = 86_400_000


#: A heatmap cell's hover: its x, its y and its value, as the cell's text,
#: since on a log colour scale its z is the value's log10.
HEATMAP_HOVER = "x: %{x}<br>y: %{y}<br>z: %{customdata}<extra></extra>"


def _value_texts(array: np.ndarray) -> list[str | None]:
    """Numbers as a hover shows them, to four significant digits, a gap
    None."""
    return [f"{v:.4g}" if np.isfinite(v) else None for v in np.asarray(array, float)]


def _wall(column: pd.Series) -> list[Any]:
    """Times as plotly.js draws them: the wall-clock time in their zone, a
    gap None."""
    column = pd.Series(column)
    if column.dt.tz is not None:
        column = column.dt.tz_localize(None)
    return [None if v is pd.NaT else v.isoformat() for v in column]


def _wall_at(moment: pd.Timestamp) -> str:
    if moment.tz is not None:
        moment = moment.tz_localize(None)
    return moment.isoformat()


def _values(array: np.ndarray) -> list[Any]:
    """Floats as plotly takes them, a gap None."""
    return [
        float(v) if np.isfinite(v) else None for v in np.asarray(array, dtype=float)
    ]


class _Drawing:
    def __init__(self, fig: Figure, theme: themes.Theme):
        self.fig = fig
        self.theme = theme
        self.colours = common.colours(fig, theme)
        self.shown: set[str] = set()
        self.colourbars = 0

    # --- the figure -----------------------------------------------------------

    def draw(self) -> Any:
        from plotly.subplots import make_subplots

        fig, theme = self.fig, self.theme
        arrangement = common.arrange(fig, theme)
        specs: list[list[Any]] = [
            [None] * arrangement.columns for _ in range(arrangement.rows)
        ]
        for panel, (row, column) in zip(fig.panels, arrangement.cells):
            specs[row][column] = {
                "type": "table" if panel.primitive == "table" else "xy"
            }
        plots = sum(arrangement.heights)
        # Between two rows, room for the upper one's tick labels (two lines
        # on a time axis) and axis title, and the lower one's title.
        gap = ROW_GAP
        spacing = 0.0
        if arrangement.rows > 1:
            spacing = min(
                gap / (plots + gap * (arrangement.rows - 1)),
                0.9 / (arrangement.rows - 1),
            )
        drawn = make_subplots(
            rows=arrangement.rows,
            cols=arrangement.columns,
            specs=specs,
            row_heights=arrangement.heights,
            vertical_spacing=spacing,
            horizontal_spacing=self._column_gap(arrangement),
            # Every plotted panel's x, as matplotlib shares them: plotly's
            # True would share them within a column only.
            shared_xaxes="all" if fig.share_x else False,
        )
        self.drawn = drawn
        self._axis_style()
        for panel, (row, column) in zip(fig.panels, arrangement.cells):
            self._panel(panel, row + 1, column + 1)
            if panel.title:
                self._title(panel, row + 1, column + 1)
        self._layout(arrangement)
        return drawn

    def _layout(self, arrangement: common.Arrangement) -> None:
        fig, theme = self.fig, self.theme
        title = escape(fig.title)
        # plotly.js does not wrap a title: each line of the subtitle as
        # common wraps it is a line of its own.
        for line in common.subtitle_lines(fig, theme):
            title += (
                f"<br><span style='font-size: {theme.type.subtitle:g}px; "
                f"font-weight: 400; opacity: 0.85;'>{escape(line)}</span>"
            )
        notes = common.note_lines(fig, theme)
        bottom = AXIS_ROOM + (arrangement.footer if notes else 0.0)
        layout: dict[str, Any] = {
            "title": {
                "text": title,
                "x": 0,
                "xanchor": "left",
                "xref": "paper",
                "y": 1,
                "yanchor": "top",
                "yref": "container",
                "pad": {"t": 12},
                "font": {"size": theme.type.title, "color": theme.text},
            },
            "paper_bgcolor": theme.paper,
            "plot_bgcolor": theme.plot,
            "font": {
                "family": ", ".join(theme.type.families),
                "size": theme.type.base,
                "color": theme.text,
            },
            "colorway": list(theme.categorical),
            "height": round(arrangement.total + bottom),
            "margin": {
                "t": round(arrangement.header + 40),
                "b": round(bottom),
                "l": 64,
                "r": 24,
            },
            "legend": {
                "font": {"size": theme.type.small, "color": theme.text},
                "bgcolor": "rgba(0,0,0,0)",
                "x": 1.02 + (0.1 if self.colourbars else 0.0),
                "xanchor": "left",
                "y": 1,
                "yanchor": "top",
            },
            "hoverlabel": {"font": {"family": ", ".join(theme.type.families)}},
            "barmode": "overlay",
            "boxmode": "overlay",
        }
        if notes:
            self.drawn.add_annotation(
                text="<br>".join(escape(note) for note in notes),
                xref="paper",
                yref="paper",
                x=0,
                y=0,
                xanchor="left",
                yanchor="top",
                yshift=-AXIS_ROOM + 8,
                align="left",
                showarrow=False,
                font={"size": theme.type.small, "color": theme.muted},
            )
        self.drawn.update_layout(**layout)

    def _axis_style(self) -> None:
        """The theme's style on every axis, set before the panels draw so
        that a panel's own setting wins: a heatmap, a strip's rows and a
        category x hide their grid, as on matplotlib."""
        theme = self.theme
        axis = {
            "showgrid": True,
            "gridcolor": theme.grid,
            "linecolor": theme.axis,
            "showline": True,
            "zeroline": False,
            "tickcolor": theme.axis,
            "tickfont": {"size": theme.type.small, "color": theme.text},
            "title": {"font": {"size": theme.type.small, "color": theme.text}},
        }
        self.drawn.update_xaxes(**axis)
        self.drawn.update_yaxes(**axis)

    def _column_gap(self, arrangement: common.Arrangement) -> float:
        """The gap between two columns, as a share of the width: room for
        a colour bar of the panel on the left and the tick labels and axis
        title of the panel on the right, the longest category label among
        them included."""
        if arrangement.columns == 1:
            return 0.02
        room = 72.0
        for panel, (_, column) in zip(self.fig.panels, arrangement.cells):
            if column + 1 < arrangement.columns and _has_colour_bar(self.fig, panel):
                room += 64.0
            if column > 0:
                longest = max(
                    (len(common.label(v)) for v in _y_categories(self.fig, panel)),
                    default=0,
                )
                room = max(room, 72.0 + longest * self.theme.type.small * 0.6)
        return min(room / self.theme.layout.width, 0.4)

    # --- a panel --------------------------------------------------------------

    def _title(self, panel: Panel, row: int, column: int) -> None:
        """A panel's title over its top left corner, as matplotlib sets it."""
        domain = self._domains(row, column)
        self.drawn.add_annotation(
            text=escape(panel.title),
            xref="paper",
            yref="paper",
            x=domain[0][0],
            y=domain[1][1],
            xanchor="left",
            yanchor="bottom",
            yshift=4,
            showarrow=False,
            font={"size": self.theme.type.base, "color": self.theme.text},
        )

    def _domains(self, row: int, column: int) -> tuple[tuple[float, float], ...]:
        """The x and y domain of the panel at *row*, *column*."""
        sub = self.drawn.get_subplot(row, column)
        if hasattr(sub, "xaxis"):
            return tuple(sub.xaxis.domain), tuple(sub.yaxis.domain)
        return tuple(sub.x), tuple(sub.y)

    def _refs(self, row: int, column: int) -> tuple[str, str]:
        sub = self.drawn.get_subplot(row, column)
        return (
            "x" + sub.xaxis.plotly_name[len("xaxis") :],
            "y" + sub.yaxis.plotly_name[len("yaxis") :],
        )

    def _panel(self, panel: Panel, row: int, column: int) -> None:
        if panel.primitive == "table":
            self._table(panel, row, column)
            return
        getattr(self, f"_{panel.primitive}")(panel, row, column)
        for axis in ("x", "y"):
            spec = getattr(panel.axes, axis)
            update = self.drawn.update_xaxes if axis == "x" else self.drawn.update_yaxes
            settings: dict[str, Any] = {}
            if spec.label:
                settings["title_text"] = escape(spec.label)
            kind = common.kind_of(self.fig, panel, axis)
            if spec.scale == "log":
                settings["type"] = "log"
            elif kind == "time":
                settings["type"] = "date"
            if spec.range is not None and kind == "number":
                low, high = spec.range
                if spec.scale == "log":
                    low, high = np.log10(low), np.log10(high)
                settings["range"] = [float(low), float(high)]
            if settings:
                update(row=row, col=column, **settings)
        if panel.primitive in ("series", "strip", "heatmap"):
            self._spans(panel, row, column)

    def _x(self, column: pd.Series) -> list[Any]:
        if pd.api.types.is_datetime64_any_dtype(column.dtype):
            return _wall(column)
        return _values(common.numbers(column))

    def _trace(self, trace: Any, row: int, column: int) -> None:
        self.drawn.add_trace(trace, row=row, col=column)

    def _named(self, group: str | None) -> dict[str, Any]:
        """A trace's legend settings: its group shown once across the
        figure, under its display name."""
        if group is None:
            return {"showlegend": False}
        return self._legend(("group", group), self.colours.name(group), group)

    def _legend(self, key: Any, text: str, group: str) -> dict[str, Any]:
        """A trace's legend settings for the entry *key*: shown the first
        time, as *text*; *group* toggles with it."""
        first = key not in self.shown
        self.shown.add(key)
        return {
            "name": escape(text),
            "legendgroup": escape(group),
            "showlegend": first,
        }

    def _key(
        self, key: Any, text: str, marker: dict[str, Any], row: int, column: int
    ) -> None:
        """A legend entry with no data of its own, for a mark, the hollow
        points or a size: shown once across the figure."""
        import plotly.graph_objects as go

        if key in self.shown:
            return
        self._trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                marker=marker,
                hoverinfo="skip",
                **self._legend(key, text, f"{key[0]}:{text}"),
            ),
            row,
            column,
        )

    def _spans(self, panel: Panel, row: int, column: int) -> None:
        theme = self.theme
        xref, yref = self._refs(row, column)
        time = common.time_kind(self.fig, panel, "x")
        place = _wall_at if time else float
        for span in panel.options.get("spans", []):
            kind = span["kind"]
            opacity = (
                theme.marks.gap_opacity if kind == "gap" else theme.marks.span_opacity
            )
            x0, x1 = place(span["start"]), place(span["end"])
            self.drawn.add_shape(
                type="rect",
                xref=xref,
                yref=f"{yref} domain",
                x0=x0,
                x1=x1,
                y0=0,
                y1=1,
                fillcolor=_rgba(theme.token(kind), opacity),
                line={"width": 0},
                layer="below",
            )
            if span["label"]:
                self._mark_label(xref, yref, x0, span["label"])
        for rule in panel.options.get("rules", []):
            at = place(rule["at"])
            self.drawn.add_shape(
                type="line",
                xref=xref,
                yref=f"{yref} domain",
                x0=at,
                x1=at,
                y0=0,
                y1=1,
                line={"color": theme.muted, "width": theme.marks.rule},
                layer="above",
            )
            if rule["label"]:
                self._mark_label(xref, yref, at, rule["label"])

    def _mark_label(self, xref: str, yref: str, x: Any, text: str) -> None:
        self.drawn.add_annotation(
            text=escape(text),
            xref=xref,
            yref=f"{yref} domain",
            x=x,
            y=1,
            xanchor="left",
            yanchor="top",
            showarrow=False,
            font={"size": self.theme.type.small, "color": self.theme.muted},
        )

    def _colourbar(
        self, scale: common.ColourScale, row: int, column: int
    ) -> dict[str, Any]:
        """The marker or trace settings that draw *scale*'s bar beside the
        panel at *row*, *column*."""
        theme = self.theme
        sub = self.drawn.get_subplot(row, column)
        x_domain = sub.xaxis.domain
        y_domain = sub.yaxis.domain
        self.colourbars += 1
        bar: dict[str, Any] = {
            "x": x_domain[1] + 0.01,
            "xanchor": "left",
            "y": (y_domain[0] + y_domain[1]) / 2,
            "yanchor": "middle",
            "len": y_domain[1] - y_domain[0],
            "thickness": 12,
            "outlinecolor": theme.axis,
            "tickfont": {"size": theme.type.small, "color": theme.text},
        }
        if scale.title:
            bar["title"] = {
                "text": escape(scale.title),
                "font": {"size": theme.type.small, "color": theme.text},
                "side": "right",
            }
        if scale.ticks:
            bar["tickvals"] = [at for at, _ in scale.ticks]
            bar["ticktext"] = [text for _, text in scale.ticks]
        return {
            "colorscale": _colourscale(scale.stops),
            "cmin": scale.low,
            "cmax": scale.high,
            "colorbar": bar,
        }

    # --- the primitives -----------------------------------------------------------

    def _series(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        theme = self.theme
        markers = panel.options.get("markers")
        for line in common.lines(self.fig, panel):
            colour = self.colours.of(line.group)
            x = self._x(line.x)
            if line.lo is not None:
                polygon_x: list[Any] = []
                polygon_y: list[Any] = []
                for start, stop in common.runs(
                    np.isfinite(line.lo) & np.isfinite(line.hi)
                ):
                    if polygon_x:
                        polygon_x.append(None)
                        polygon_y.append(None)
                    polygon_x += x[start:stop] + x[start:stop][::-1]
                    polygon_y += (
                        _values(line.hi[start:stop])
                        + _values(line.lo[start:stop])[::-1]
                    )
                if polygon_x:
                    self._trace(
                        go.Scatter(
                            x=polygon_x,
                            y=polygon_y,
                            mode="lines",
                            fill="toself",
                            fillcolor=_rgba(colour, theme.marks.band_opacity),
                            line={"width": 0},
                            hoverinfo="skip",
                            showlegend=False,
                            legendgroup=escape(line.group) if line.group else None,
                        ),
                        row,
                        column,
                    )
            self._trace(
                go.Scatter(
                    x=x,
                    y=_values(line.y),
                    mode="lines+markers" if markers else "lines",
                    line={"color": colour, "width": theme.marks.line},
                    marker={"size": theme.marks.marker, "color": colour},
                    connectgaps=False,
                    **self._named(line.group),
                ),
                row,
                column,
            )
            alone = common.isolated(line.y)
            if not markers and alone.any():
                self._trace(
                    go.Scatter(
                        x=[v for v, a in zip(x, alone) if a],
                        y=_values(line.y[alone]),
                        mode="markers",
                        marker={"size": theme.marks.marker, "color": colour},
                        showlegend=False,
                        legendgroup=escape(line.group) if line.group else None,
                    ),
                    row,
                    column,
                )

    def _strip(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        theme = self.theme
        strip = common.strip(self.fig, panel, theme)
        cells = strip.cells
        rows = [escape(r) for r in strip.rows]
        drawn = cells[cells["colour"].notna()]
        if len(drawn):
            milliseconds = (drawn["end"] - drawn["start"]).dt.total_seconds() * 1000
            self._trace(
                go.Bar(
                    orientation="h",
                    y=[rows[r] for r in drawn["row"]],
                    x=milliseconds.tolist(),
                    base=_wall(drawn["start"]),
                    width=0.8,
                    marker={"color": list(drawn["colour"]), "line": {"width": 0}},
                    showlegend=False,
                    hoverinfo="x+y",
                ),
                row,
                column,
            )
        marked = cells[cells["marked"].to_numpy(dtype=bool)]
        if len(marked):
            centres = marked["start"] + (marked["end"] - marked["start"]) / 2
            self._trace(
                go.Scatter(
                    x=_wall(centres),
                    y=[rows[r] for r in marked["row"]],
                    mode="markers",
                    marker={
                        "symbol": "triangle-down",
                        "size": theme.marks.marker + 2,
                        "color": theme.warning,
                        "line": {"color": theme.paper, "width": 0.75},
                    },
                    **self._legend(
                        ("mark", panel.options["mark_label"]),
                        panel.options["mark_label"],
                        "mark:" + panel.options["mark_label"],
                    ),
                ),
                row,
                column,
            )
        if strip.scale is not None and np.isfinite(strip.scale.values).any():
            self._trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="markers",
                    marker={
                        "color": [strip.scale.low],
                        "showscale": True,
                        **self._colourbar(strip.scale, row, column),
                    },
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row,
                column,
            )
        self.drawn.update_yaxes(
            row=row,
            col=column,
            type="category",
            categoryorder="array",
            categoryarray=rows,
            autorange="reversed",
            showgrid=False,
        )
        if len(cells):
            self.drawn.update_xaxes(
                row=row,
                col=column,
                range=[
                    _wall_at(cells["start"].min()),
                    _wall_at(cells["end"].max()),
                ],
            )

    def _heatmap(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        cells = common.grid(self.fig, panel)
        scale = common.colour_scale(
            cells.values.ravel(), panel, self.theme, panel.options["palette"]
        )
        values = scale.values.reshape(cells.values.shape)
        x = self._grid_axis(
            "x", cells.xs, common.kind_of(self.fig, panel, "x"), row, column
        )
        y = self._grid_axis(
            "y", cells.ys, common.kind_of(self.fig, panel, "y"), row, column
        )
        place = self._place(panel, cells, x, y)
        if np.isfinite(values).any():
            settings = self._colourbar(scale, row, column)
            self._trace(
                go.Heatmap(
                    **place,
                    z=[_values(line) for line in values],
                    customdata=[_value_texts(line) for line in cells.values],
                    hovertemplate=HEATMAP_HOVER,
                    colorscale=settings["colorscale"],
                    zmin=settings["cmin"],
                    zmax=settings["cmax"],
                    colorbar=settings["colorbar"],
                    hoverongaps=False,
                    showscale=True,
                ),
                row,
                column,
            )
        if cells.gaps.any():
            gap = _rgba(self.theme.token("gap"), self.theme.marks.gap_cell_opacity)
            label = panel.options["gap_label"]
            self._trace(
                go.Heatmap(
                    **place,
                    z=[[1.0 if g else None for g in line] for line in cells.gaps],
                    colorscale=[[0.0, gap], [1.0, gap]],
                    zmin=0,
                    zmax=1,
                    showscale=False,
                    hoverongaps=False,
                    hovertemplate=f"{escape(label)}<extra></extra>",
                ),
                row,
                column,
            )
            self._key(
                ("gap", label),
                label,
                {"symbol": "square", "size": 12, "color": gap},
                row,
                column,
            )
        self.drawn.update_xaxes(row=row, col=column, showgrid=False)
        self.drawn.update_yaxes(row=row, col=column, showgrid=False)

    def _place(
        self, panel: Panel, cells: common.Grid, x: list[Any], y: list[Any]
    ) -> dict[str, Any]:
        """Where plotly.js draws a heatmap's cells: by their centres *x* and
        *y*, which it places as :func:`common.edges` does, but a lone cell
        on a number or time axis by its centre (``x0``, ``y0``) and width
        (``dx``, ``dy``). Given one centre, plotly.js makes a cell one unit
        wide whatever the axis: a millisecond on a time axis, where
        matplotlib's is a day, and off a log axis for a cell at 0.5 or
        under."""
        place: dict[str, Any] = {"x": x, "y": y}
        for axis, values in (("x", cells.xs), ("y", cells.ys)):
            kind = common.kind_of(self.fig, panel, axis)
            if len(values) != 1 or kind == "category":
                continue
            if kind == "time":
                width = DAY_MS
            else:
                width = common.lone_width(values[0], getattr(panel.axes, axis).scale)
            centre = place.pop(axis)[0]
            place.update({f"{axis}0": centre, f"d{axis}": width})
        return place

    def _grid_axis(
        self, axis: str, values: list[Any], kind: str, row: int, column: int
    ) -> list[Any]:
        if not values:
            # A heatmap without a cell
            return []
        if kind == "time":
            return _wall(pd.Series(values))
        if kind == "number":
            return _values(np.asarray(values, dtype=float))
        names = [escape(common.label(v)) for v in values]
        update = self.drawn.update_xaxes if axis == "x" else self.drawn.update_yaxes
        update(
            row=row,
            col=column,
            type="category",
            categoryorder="array",
            categoryarray=names,
        )
        return names

    def _scatter(self, panel: Panel, row: int, column: int) -> None:
        """Every point in one trace, so that the larger lie behind the
        smaller whatever their group; legend entries of their own for the
        groups, the hollow points and the sizes."""
        import plotly.graph_objects as go

        theme = self.theme
        drawn_points = common.points(self.fig, panel, theme, self.colours)
        points = drawn_points.points
        hollow = points["hollow"].to_numpy(dtype=bool)
        opacity = theme.marks.point_opacity
        texts = [escape(text) for text in points["text"]]
        self._trace(
            go.Scatter(
                x=_values(points["x"].to_numpy()),
                y=_values(points["y"].to_numpy()),
                mode="markers",
                marker={
                    "size": _values(points["diameter"].to_numpy()),
                    "color": [
                        "rgba(0,0,0,0)" if h else _rgba(c, opacity)
                        for c, h in zip(points["colour"], hollow)
                    ],
                    "line": {
                        "color": list(points["colour"]),
                        "width": [theme.marks.line if h else 0 for h in hollow],
                    },
                },
                text=texts if any(texts) else None,
                hoverinfo="x+y+text" if any(texts) else "x+y",
                showlegend=False,
            ),
            row,
            column,
        )
        scale = drawn_points.scale
        if scale is not None and np.isfinite(scale.values).any():
            self._trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="markers",
                    marker={
                        "color": [scale.low],
                        "showscale": True,
                        **self._colourbar(scale, row, column),
                    },
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row,
                column,
            )
        for group in drawn_points.groups:
            self._key(
                ("group", group),
                self.colours.name(group),
                {"size": theme.marks.marker + 3, "color": self.colours.of(group)},
                row,
                column,
            )
        label = panel.options["hollow_label"]
        if label and hollow.any():
            self._key(
                ("hollow", label),
                label,
                {
                    "size": theme.marks.marker + 3,
                    "color": "rgba(0,0,0,0)",
                    "line": {"color": theme.muted, "width": theme.marks.line},
                },
                row,
                column,
            )
        index = self.fig.panels.index(panel)
        for value, diameter in drawn_points.sizes:
            title = panel.axes.size.label
            self._trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="markers",
                    marker={"size": diameter, "color": _rgba(theme.muted, opacity)},
                    name=escape(common.number_text(value)),
                    legendgroup=f"size:{index}",
                    legendgrouptitle={"text": escape(title)} if title else None,
                    showlegend=True,
                    hoverinfo="skip",
                ),
                row,
                column,
            )
        self._labels(panel, row, column)

    def _labels(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        labelled = common.labelled(self.fig, panel)
        if not len(labelled):
            return
        hovers = list(labelled["hover"])
        said = any(h is not None for h in hovers)
        self._trace(
            go.Scatter(
                x=_values(labelled["x"].to_numpy()),
                y=_values(labelled["y"].to_numpy()),
                mode="text",
                text=[escape(t) for t in labelled["text"]],
                textposition="top center",
                textfont={"size": self.theme.type.small, "color": self.theme.text},
                showlegend=False,
                hovertext=[escape(h or "") for h in hovers] if said else None,
                hoverinfo="text" if said else "skip",
            ),
            row,
            column,
        )

    def _sticks(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        theme = self.theme
        sticks = common.sticks(self.fig, panel, theme, self.colours)
        xref, yref = self._refs(row, column)
        self.drawn.add_shape(
            type="line",
            xref=f"{xref} domain",
            yref=yref,
            x0=0,
            x1=1,
            y0=0,
            y1=0,
            line={"color": theme.muted, "width": theme.marks.rule},
            layer="below",
        )
        # One trace per group and colour (a line trace has one colour):
        # a group's, or by sign the ramp's two ends.
        keys = list(zip(sticks["group"], sticks["colour"]))
        for group, colour in dict.fromkeys(keys):
            mine = sticks[[key == (group, colour) for key in keys]]
            line_x: list[Any] = []
            line_y: list[Any] = []
            line_text: list[Any] = []
            for x, y, hover in zip(mine["x"], mine["y"], mine["hover"]):
                line_x += [float(x), float(x), None]
                line_y += [0.0, float(y), None]
                text = escape(hover) if hover is not None else ""
                line_text += [text, text, None]
            said = any(h is not None for h in mine["hover"])
            self._trace(
                go.Scatter(
                    x=line_x,
                    y=line_y,
                    mode="lines",
                    line={"color": colour, "width": theme.marks.line},
                    connectgaps=False,
                    text=line_text if said else None,
                    hoverinfo="x+y+text" if said else None,
                    **self._named(group),
                ),
                row,
                column,
            )
        marked = sticks[sticks["marked"].to_numpy(dtype=bool)]
        if len(marked):
            label = panel.options["mark_label"]
            self._trace(
                go.Scatter(
                    x=_values(marked["x"].to_numpy()),
                    y=_values(marked["y"].to_numpy()),
                    mode="markers",
                    marker={
                        "symbol": "diamond",
                        "size": theme.marks.marker + 2,
                        "color": theme.warning,
                        "line": {"color": theme.paper, "width": 0.75},
                    },
                    **self._legend(("mark", label), label, "mark:" + label),
                ),
                row,
                column,
            )
        self._labels(panel, row, column)

    def _bars(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        theme = self.theme
        place, bars = common.bars(self.fig, panel, theme)
        for group in place.groups:
            mine = bars[[g == group for g in bars["group"]]]
            if not len(mine):
                continue
            name = escape(self.colours.name(group)) if group is not None else ""
            self._trace(
                go.Bar(
                    x=_values(mine["position"].to_numpy()),
                    y=_values(mine["height"].to_numpy()),
                    base=_values(mine["bottom"].to_numpy()),
                    width=place.width,
                    marker={"color": self.colours.of(group), "line": {"width": 0}},
                    customdata=[escape(c) for c in mine["category"]],
                    hovertemplate=f"%{{customdata}}: %{{y}}<extra>{name}</extra>",
                    **self._named(group),
                ),
                row,
                column,
            )
        spans = common.intervals(bars)
        if len(spans):
            # The interval from lo to hi as matplotlib draws it: about its
            # middle, so that it need not hold the bar's top.
            self._trace(
                go.Scatter(
                    x=_values(spans["position"].to_numpy()),
                    y=_values(spans["middle"].to_numpy()),
                    mode="markers",
                    marker={"size": 0, "color": "rgba(0,0,0,0)"},
                    error_y={
                        "type": "data",
                        "symmetric": True,
                        "array": _values(spans["half"].to_numpy()),
                        "color": theme.text,
                        "thickness": 1,
                        "width": 3,
                    },
                    hoverinfo="skip",
                    showlegend=False,
                ),
                row,
                column,
            )
        self._categories(place, row, column)

    def _box(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        theme = self.theme
        place, boxes = common.boxes(self.fig, panel, theme)
        for group in place.groups:
            mine = boxes[[g == group for g in boxes["group"]]]
            if not len(mine):
                continue
            colour = self.colours.of(group)
            # A box trace has one fill: the hollow boxes are a trace of
            # their own, in the group's legend entry.
            named = self._named(group)
            for hollow in (False, True):
                part = mine[mine["hollow"].to_numpy(dtype=bool) == hollow]
                if not len(part):
                    continue
                stats = {
                    name: _values(part[column_name].to_numpy())
                    for name, column_name in (
                        ("q1", "q1"),
                        ("median", "median"),
                        ("q3", "q3"),
                        ("lowerfence", "lo"),
                        ("upperfence", "hi"),
                    )
                }
                if "mean" in panel.encodings:
                    stats["mean"] = _values(part["mean"].to_numpy())
                    stats["boxmean"] = True
                self._trace(
                    go.Box(
                        x=_values(part["position"].to_numpy()),
                        width=place.width * 0.9,
                        fillcolor=(
                            "rgba(0,0,0,0)"
                            if hollow
                            else _rgba(colour, theme.marks.box_opacity)
                        ),
                        line={"color": colour, "width": theme.marks.line},
                        boxpoints=False,
                        **stats,
                        **named,
                    ),
                    row,
                    column,
                )
                named = {**named, "showlegend": False}
        label = panel.options["hollow_label"]
        if label and boxes["hollow"].to_numpy(dtype=bool).any():
            self._key(
                ("hollow", label),
                label,
                {
                    "symbol": "square",
                    "size": 12,
                    "color": "rgba(0,0,0,0)",
                    "line": {"color": theme.muted, "width": theme.marks.line},
                },
                row,
                column,
            )
        self._categories(place, row, column)

    def _categories(self, place: common.Places, row: int, column: int) -> None:
        self.drawn.update_xaxes(
            row=row,
            col=column,
            tickmode="array",
            tickvals=list(range(len(place.categories))),
            ticktext=[escape(c) for c in place.categories],
            range=[-0.5, len(place.categories) - 0.5],
            showgrid=False,
        )

    def _table(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        theme = self.theme
        table = common.table(self.fig, panel)
        if not table.headers:
            return
        columns = [
            [escape(r[i]) for r in table.rows] for i in range(len(table.headers))
        ]
        align = ["right" if numeric else "left" for numeric in table.numeric]
        shade = themes.blend(theme.warning, theme.paper, theme.marks.span_opacity)
        fills = [shade if marked else theme.paper for marked in table.marked]
        self._trace(
            go.Table(
                header={
                    "values": [f"<b>{escape(h)}</b>" for h in table.headers],
                    "fill_color": theme.plot,
                    "line_color": theme.axis,
                    "font": {"size": theme.type.small, "color": theme.text},
                    "align": align,
                    "height": theme.layout.table_row,
                },
                cells={
                    "values": columns,
                    "fill_color": [fills] * len(columns),
                    "line_color": theme.axis,
                    "font": {"size": theme.type.small, "color": theme.text},
                    "align": align,
                    "height": theme.layout.table_row,
                },
            ),
            row,
            column,
        )


def _has_colour_bar(fig: Figure, panel: Panel) -> bool:
    """Whether *panel* draws a colour bar: a heatmap, a strip with values,
    a scatter coloured by a number."""
    if panel.primitive == "heatmap":
        return True
    if panel.primitive == "strip":
        return "value" in panel.encodings
    colour = panel.encodings.get("colour")
    return (
        panel.primitive == "scatter"
        and colour is not None
        and (fig.kinds()[colour] == "number")
    )


def _y_categories(fig: Figure, panel: Panel) -> list[Any]:
    """The categories a panel's y axis labels: a strip's rows, a heatmap's
    text rows."""
    y = panel.encodings.get("y")
    if panel.primitive not in ("strip", "heatmap") or not isinstance(y, str):
        return []
    if panel.primitive == "heatmap" and fig.kinds()[y] in ("time", "number"):
        return []
    return common.ordered(fig.frame(panel)[y])


def _colourscale(stops: tuple[str, ...]) -> list[list[Any]]:
    if len(stops) == 1:
        return [[0.0, stops[0]], [1.0, stops[0]]]
    return [[i / (len(stops) - 1), colour] for i, colour in enumerate(stops)]
