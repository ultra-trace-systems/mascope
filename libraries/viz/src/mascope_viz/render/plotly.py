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
            shared_xaxes=fig.share_x,
        )
        self.drawn = drawn
        for panel, (row, column) in zip(fig.panels, arrangement.cells):
            self._panel(panel, row + 1, column + 1)
            if panel.title:
                self._title(panel, row + 1, column + 1)
        self._layout(arrangement)
        return drawn

    def _layout(self, arrangement: common.Arrangement) -> None:
        fig, theme = self.fig, self.theme
        title = escape(fig.title)
        if fig.subtitle:
            title += (
                f"<br><span style='font-size: {theme.type.subtitle:g}px; "
                f"font-weight: 400; opacity: 0.85;'>{escape(fig.subtitle)}</span>"
            )
        notes = common.note_lines(fig, theme)
        bottom = AXIS_ROOM + (arrangement.footer if notes else 0.0)
        layout: dict[str, Any] = {
            "title": {
                "text": title,
                "x": 0,
                "xanchor": "left",
                "xref": "paper",
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
        if panel.primitive in ("series", "strip"):
            self._spans(panel, row, column)

    def _x(self, column: pd.Series) -> list[Any]:
        if pd.api.types.is_datetime64_any_dtype(column.dtype):
            return _wall(column)
        return _values(common.numbers(column))

    def _trace(self, trace: Any, row: int, column: int) -> None:
        self.drawn.add_trace(trace, row=row, col=column)

    def _named(self, group: str | None) -> dict[str, Any]:
        """A trace's legend settings: its group shown once across the
        figure."""
        if group is None:
            return {"showlegend": False}
        first = group not in self.shown
        self.shown.add(group)
        return {
            "name": escape(group),
            "legendgroup": escape(group),
            "showlegend": first,
        }

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
                    **self._named(panel.options["mark_label"]),
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
        if np.isfinite(values).any():
            settings = self._colourbar(scale, row, column)
            self._trace(
                go.Heatmap(
                    x=x,
                    y=y,
                    z=[_values(line) for line in values],
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
        self.drawn.update_xaxes(row=row, col=column, showgrid=False)
        self.drawn.update_yaxes(row=row, col=column, showgrid=False)

    def _grid_axis(
        self, axis: str, values: list[Any], kind: str | None, row: int, column: int
    ) -> list[Any]:
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
        import plotly.graph_objects as go

        theme = self.theme
        frame = self.fig.frame(panel)
        encodings = panel.encodings
        x = common.on_scale(common.numbers(frame[encodings["x"]]), panel.axes.x.scale)
        y = common.on_scale(common.numbers(frame[encodings["y"]]), panel.axes.y.scale)
        if "size" in encodings:
            diameters = common.sizes(common.numbers(frame[encodings["size"]]), theme)
        else:
            diameters = np.full(len(frame), theme.marks.marker)
        hover = (
            [
                escape(common.label(v)) if not pd.isna(v) else ""
                for v in frame[encodings["label"]]
            ]
            if "label" in encodings
            else None
        )
        colour = encodings.get("colour")

        def points(
            rows: np.ndarray, marker: dict[str, Any], named: dict[str, Any]
        ) -> None:
            rows = rows & np.isfinite(x) & np.isfinite(y)
            self._trace(
                go.Scatter(
                    x=_values(x[rows]),
                    y=_values(y[rows]),
                    mode="markers",
                    marker={
                        "size": _values(diameters[rows]),
                        "line": {"width": 0},
                        **marker,
                    },
                    text=[hover[i] for i in np.flatnonzero(rows)] if hover else None,
                    **named,
                ),
                row,
                column,
            )

        everything = np.ones(len(frame), dtype=bool)
        if colour is not None and self.fig.kinds()[colour] == "number":
            scale = common.colour_scale(
                common.numbers(frame[colour]), panel, theme, panel.options["palette"]
            )
            tints = np.array(
                [scale.colour(v) or theme.muted for v in scale.values], dtype=object
            )
            shown = everything & np.isfinite(x) & np.isfinite(y)
            points(everything, {"color": list(tints[shown])}, {"showlegend": False})
            if np.isfinite(scale.values).any():
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
        elif colour is not None:
            names = [common.label(v) if not pd.isna(v) else None for v in frame[colour]]
            for group in common.ordered(frame[colour]):
                name = common.label(group)
                rows = np.array([n == name for n in names], dtype=bool)
                points(rows, {"color": self.colours.of(name)}, self._named(name))
        else:
            points(everything, {"color": self.colours.single}, {"showlegend": False})
        self._labels(panel, row, column)

    def _labels(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        labelled = common.labelled(self.fig, panel)
        if not len(labelled):
            return
        self._trace(
            go.Scatter(
                x=_values(labelled["x"].to_numpy()),
                y=_values(labelled["y"].to_numpy()),
                mode="text",
                text=[escape(t) for t in labelled["text"]],
                textposition="top center",
                textfont={"size": self.theme.type.small, "color": self.theme.text},
                showlegend=False,
                hoverinfo="skip",
            ),
            row,
            column,
        )

    def _sticks(self, panel: Panel, row: int, column: int) -> None:
        import plotly.graph_objects as go

        theme = self.theme
        frame = self.fig.frame(panel)
        x = common.numbers(frame[panel.encodings["x"]])
        y = common.numbers(frame[panel.encodings["y"]])
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
        for group in common.places(self.fig, panel, theme).groups:
            rows = common.rows_of(frame, panel, group)
            positions = frame.index.get_indexer(rows.index)
            shown = positions[np.isfinite(x[positions]) & np.isfinite(y[positions])]
            line_x: list[Any] = []
            line_y: list[Any] = []
            for i in shown:
                line_x += [float(x[i]), float(x[i]), None]
                line_y += [0.0, float(y[i]), None]
            self._trace(
                go.Scatter(
                    x=line_x,
                    y=line_y,
                    mode="lines",
                    line={"color": self.colours.of(group), "width": theme.marks.line},
                    connectgaps=False,
                    **self._named(group),
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
            errors = None
            if "lo" in panel.encodings:
                errors = {
                    "type": "data",
                    "symmetric": False,
                    "array": _values(mine["err_hi"].to_numpy()),
                    "arrayminus": _values(mine["err_lo"].to_numpy()),
                    "color": theme.text,
                    "thickness": 1,
                    "width": 3,
                }
            self._trace(
                go.Bar(
                    x=_values(mine["position"].to_numpy()),
                    y=_values(mine["height"].to_numpy()),
                    base=_values(mine["bottom"].to_numpy()),
                    width=place.width,
                    marker={"color": self.colours.of(group), "line": {"width": 0}},
                    error_y=errors,
                    customdata=[escape(c) for c in mine["category"]],
                    hovertemplate="%{customdata}: %{y}<extra></extra>",
                    **self._named(group),
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
            stats = {
                name: _values(mine[column_name].to_numpy())
                for name, column_name in (
                    ("q1", "q1"),
                    ("median", "median"),
                    ("q3", "q3"),
                    ("lowerfence", "lo"),
                    ("upperfence", "hi"),
                )
            }
            if "mean" in panel.encodings:
                stats["mean"] = _values(mine["mean"].to_numpy())
                stats["boxmean"] = True
            self._trace(
                go.Box(
                    x=_values(mine["position"].to_numpy()),
                    width=place.width * 0.9,
                    fillcolor=_rgba(colour, 0.25),
                    line={"color": colour, "width": theme.marks.line},
                    boxpoints=False,
                    **stats,
                    **self._named(group),
                ),
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
                    "fill_color": theme.paper,
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
