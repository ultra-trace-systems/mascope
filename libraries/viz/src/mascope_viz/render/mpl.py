"""The matplotlib renderer: a Figure spec as a matplotlib figure, for PNG,
SVG and the PDF report.

Built on :class:`matplotlib.figure.Figure` without pyplot, so it holds no
global state and needs no display. A time axis places each value at its
instant and labels it in the column's zone. Text from the spec is drawn
as written: a ``$`` is not mathtext.

matplotlib is imported here alone, when a figure is drawn.
"""

import datetime
import functools
import math
from typing import Any

import numpy as np
import pandas as pd

from ..charts.spec import Figure, Panel
from . import common
from . import theme as themes


#: CSS pixels per inch, as the theme's sizes are written.
PX_PER_INCH = 96.0

#: Points per CSS pixel.
PT_PER_PX = 0.75

#: The margin of the header's and the footer's text, in CSS pixels.
MARGIN = 12.0

#: The least space between two category labels, in CSS pixels.
LABEL_GAP = 4.0

#: The angle category labels slant at when level they would overlap.
SLANT = 45.0


def render(fig: Figure, *, theme: "str | themes.Theme" = "light") -> Any:
    """*fig* drawn with matplotlib in *theme* (``light`` or ``dark``).

    :return: A :class:`matplotlib.figure.Figure`. Save it with
        :func:`mascope_viz.render.save`, which applies the theme's rc
        settings and keeps the output byte-deterministic, or with its own
        ``savefig``.
    """
    import matplotlib

    theme = themes.get(theme)
    with matplotlib.rc_context(rc(theme)):
        return _Drawing(fig, theme).draw()


def rc(theme: themes.Theme) -> dict[str, Any]:
    """The rc settings a figure is drawn and saved under: the theme's type
    and grounds, no mathtext or TeX, and a fixed SVG hash salt so an SVG is
    the same bytes on every run."""
    family = font_family(theme.type.families)
    return {
        "font.family": "sans-serif",
        "font.sans-serif": [family],
        "font.size": theme.type.base * PT_PER_PX,
        "text.usetex": False,
        "text.color": theme.text,
        "axes.edgecolor": theme.axis,
        "axes.labelcolor": theme.text,
        "axes.facecolor": theme.plot,
        "figure.facecolor": theme.paper,
        "savefig.facecolor": theme.paper,
        "xtick.color": theme.axis,
        "ytick.color": theme.axis,
        "xtick.labelcolor": theme.text,
        "ytick.labelcolor": theme.text,
        "legend.frameon": False,
        "svg.hashsalt": "mascope_viz",
        "svg.fonttype": "path",
        "pdf.fonttype": 42,
        "path.simplify": False,
    }


@functools.cache
def font_family(families: tuple[str, ...]) -> str:
    """The first of *families* matplotlib has, else its default sans-serif
    (DejaVu Sans, which it ships)."""
    from matplotlib import font_manager

    for family in families:
        if family in ("sans-serif", "serif", "monospace"):
            continue
        try:
            font_manager.findfont(
                font_manager.FontProperties(family=family), fallback_to_default=False
            )
        except ValueError:
            continue
        return family
    return "DejaVu Sans"


def escape(text: str) -> str:
    """*text* as matplotlib draws it literally: a ``$`` escaped, so a name
    with two of them is not read as mathtext."""
    return text.replace("$", r"\$")


def px(value: float) -> float:
    """CSS pixels as points."""
    return value * PT_PER_PX


def fitted(widest: float, line: float, slot: float, gap: float) -> int | None:
    """How category labels fit their slots, each *slot* wide: None when
    the *widest*, level, leaves *gap* to the next; else every how many of
    them to keep, slanted at :data:`SLANT`. Two slanted labels stand a
    *line*'s height apart across the slant when their ticks are that height
    over sin(slant) apart, and *gap* more."""
    if widest + gap <= slot:
        return None
    return math.ceil((line / math.sin(math.radians(SLANT)) + gap) / slot)


def _rgba(colour: str, alpha: float) -> tuple[float, float, float, float]:
    r, g, b = themes.rgb(colour)
    return (r, g, b, alpha)


class _Drawing:
    def __init__(self, fig: Figure, theme: themes.Theme):
        self.fig = fig
        self.theme = theme
        self.colours = common.colours(fig, theme)
        # A legend entry per identity (a group, a mark, a size legend's
        # row), each its text and its handle, in the order first drawn.
        self.legend: dict[Any, tuple[str, Any]] = {}
        # The panels whose x axis draws categories, each with its labels
        self.categorised: list[tuple[Any, list[str]]] = []

    # --- the figure ---------------------------------------------------------

    def draw(self) -> Any:
        from matplotlib.figure import Figure as MplFigure

        fig, theme = self.fig, self.theme
        arrangement = common.arrange(fig, theme)
        drawn = MplFigure(
            figsize=(theme.layout.width / PX_PER_INCH, arrangement.total / PX_PER_INCH),
            dpi=PX_PER_INCH * theme.layout.scale,
            layout="constrained",
            facecolor=theme.paper,
        )
        grid = drawn.add_gridspec(
            arrangement.rows, arrangement.columns, height_ratios=arrangement.heights
        )
        self.panel_width = theme.layout.width / arrangement.columns
        shared = None
        for panel, (row, column) in zip(fig.panels, arrangement.cells):
            plotted = fig.share_x and panel.primitive != "table"
            ax = drawn.add_subplot(
                grid[row, column], sharex=shared if plotted else None
            )
            if plotted and shared is None:
                shared = ax
            self._panel(drawn, ax, panel)
        room = 0.0
        if self.legend:
            legend = drawn.legend(
                [handle for _, handle in self.legend.values()],
                [escape(text) for text, _ in self.legend.values()],
                loc="outside right upper",
                fontsize=px(theme.type.small),
                labelcolor=theme.text,
                frameon=False,
            )
            # The legend stands in the figure's top right corner, beside
            # the header: the subtitle wraps in the width it leaves.
            room = legend.get_window_extent().width / theme.layout.scale + MARGIN
        lines = common.subtitle_lines(fig, theme, room)
        arrangement = common.arrange(fig, theme, subtitle=len(lines))
        total = arrangement.total
        drawn.set_size_inches(theme.layout.width / PX_PER_INCH, total / PX_PER_INCH)
        # The panels are laid out between the header and the footer, whose
        # text the layout leaves alone.
        bottom = arrangement.footer / total
        top = arrangement.header / total
        drawn.get_layout_engine().set(
            w_pad=4 / 72,
            h_pad=4 / 72,
            hspace=0.04,
            wspace=0.04,
            rect=(0, bottom, 1, 1 - top - bottom),
        )
        self._header(drawn, total, lines)
        notes = common.note_lines(fig, theme)
        if notes:
            self._footer(drawn, notes, arrangement.footer, total)
        if self.categorised:
            self._fit_categories(drawn)
        return drawn

    def _fit_categories(self, drawn: Any) -> None:
        """Category labels that would overlap, slanted; and when even
        slanted they would, every so many of them, as plotly turns and thins
        its own. Measured on the laid-out figure, where the panels have their
        widths."""
        drawn.draw_without_rendering()
        gap = LABEL_GAP * self.theme.layout.scale
        for ax, names in self.categorised:
            if len(names) < 2:
                continue
            boxes = [t.get_window_extent() for t in ax.get_xticklabels()]
            every = fitted(
                max(box.width for box in boxes),
                max(box.height for box in boxes),
                ax.get_window_extent().width / len(names),
                gap,
            )
            if every is None:
                continue
            kept = range(0, len(names), every)
            ax.set_xticks(kept, [escape(names[i]) for i in kept])
            # Each ends at its tick, as matplotlib's own dates slant
            for label in ax.get_xticklabels():
                label.set_rotation(SLANT)
                label.set_horizontalalignment("right")

    def _header(self, drawn: Any, total: float, lines: list[str]) -> None:
        theme = self.theme
        left = MARGIN / theme.layout.width
        drawn.text(
            left,
            1 - MARGIN / total,
            escape(self.fig.title),
            ha="left",
            va="top",
            fontsize=px(theme.type.title),
            fontweight="bold",
            color=theme.text,
            in_layout=False,
        )
        if lines:
            drawn.text(
                left,
                1 - (MARGIN + theme.type.title + 8) / total,
                "\n".join(escape(line) for line in lines),
                ha="left",
                va="top",
                fontsize=px(theme.type.subtitle),
                color=theme.muted,
                linespacing=common.LINE_HEIGHT,
                in_layout=False,
            )

    def _footer(
        self, drawn: Any, notes: list[str], height: float, total: float
    ) -> None:
        drawn.text(
            MARGIN / self.theme.layout.width,
            (height - 4) / total,
            "\n".join(escape(note) for note in notes),
            ha="left",
            va="top",
            fontsize=px(self.theme.type.small),
            color=self.theme.muted,
            linespacing=1.4,
            in_layout=False,
        )

    # --- a panel -----------------------------------------------------------------

    def _panel(self, drawn: Any, ax: Any, panel: Panel) -> None:
        theme = self.theme
        if panel.title:
            ax.set_title(
                escape(panel.title),
                loc="left",
                fontsize=px(theme.type.base),
                color=theme.text,
            )
        if panel.primitive == "table":
            self._table(ax, panel)
            return
        self._style(ax)
        getattr(self, f"_{panel.primitive}")(drawn, ax, panel)
        for axis in ("x", "y"):
            spec = getattr(panel.axes, axis)
            setter = ax.set_xlabel if axis == "x" else ax.set_ylabel
            if spec.label:
                setter(
                    escape(spec.label), fontsize=px(theme.type.small), color=theme.text
                )
            kind = common.kind_of(self.fig, panel, axis)
            if spec.scale == "log":
                (ax.set_xscale if axis == "x" else ax.set_yscale)("log")
            if spec.range is not None and kind == "number":
                (ax.set_xlim if axis == "x" else ax.set_ylim)(*spec.range)
        if common.time_kind(self.fig, panel, "x"):
            self._time_axis(ax, self.fig.data[panel.encodings["x"]])
        if panel.primitive in ("series", "strip"):
            self._spans(ax, panel)

    def _style(self, ax: Any) -> None:
        theme = self.theme
        ax.set_facecolor(theme.plot)
        for spine in ax.spines.values():
            spine.set_color(theme.axis)
            spine.set_linewidth(px(1))
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(
            colors=theme.axis,
            labelcolor=theme.text,
            labelsize=px(theme.type.small),
            labelfontfamily=font_family(theme.type.families),
            width=px(1),
        )
        ax.grid(True, color=theme.grid, linewidth=px(1))
        ax.set_axisbelow(True)

    def _time_axis(self, ax: Any, column: pd.Series) -> None:
        import matplotlib.dates as mdates

        zone = column.dt.tz or datetime.timezone.utc
        # A tick for every 110 pixels or so of the panel's width; the zone
        # is the axis label's to name, and the year the subtitle's.
        ticks = max(3, int(self.panel_width / 110))
        locator = mdates.AutoDateLocator(tz=zone, minticks=2, maxticks=ticks)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(
            mdates.ConciseDateFormatter(locator, tz=zone, show_offset=False)
        )

    def _x(self, column: pd.Series) -> np.ndarray:
        """x values as matplotlib draws them: a time as its date number."""
        if pd.api.types.is_datetime64_any_dtype(column.dtype):
            return _dates(column)
        return common.numbers(column)

    def _spans(self, ax: Any, panel: Panel) -> None:
        theme = self.theme
        time = common.time_kind(self.fig, panel, "x")
        place = _date if time else float
        for span in panel.options.get("spans", []):
            kind = span["kind"]
            opacity = (
                theme.marks.gap_opacity if kind == "gap" else theme.marks.span_opacity
            )
            x0, x1 = place(span["start"]), place(span["end"])
            ax.axvspan(
                x0,
                x1,
                facecolor=_rgba(theme.token(kind), opacity),
                edgecolor="none",
                zorder=0.5,
            )
            if span["label"]:
                self._mark_label(ax, x0, span["label"])
        for rule in panel.options.get("rules", []):
            at = place(rule["at"])
            ax.axvline(
                at, color=theme.muted, linewidth=px(theme.marks.rule), zorder=1.5
            )
            if rule["label"]:
                self._mark_label(ax, at, rule["label"])

    def _mark_label(self, ax: Any, x: float, text: str) -> None:
        ax.text(
            x,
            1,
            " " + escape(text),
            transform=ax.get_xaxis_transform(),
            ha="left",
            va="top",
            fontsize=px(self.theme.type.small),
            color=self.theme.muted,
            clip_on=True,
            in_layout=False,
        )

    def _entry(self, key: Any, text: str, handle: Any) -> None:
        if key is not None and key not in self.legend:
            self.legend[key] = (text, handle)

    def _group(self, group: str | None, handle: Any) -> None:
        """A group's legend entry, under its display name."""
        if group is not None:
            self._entry(("group", group), self.colours.name(group), handle)

    def _colourbar(self, drawn: Any, ax: Any, scale: common.ColourScale) -> None:
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize

        mappable = ScalarMappable(
            norm=Normalize(scale.low, scale.high), cmap=_colourmap(scale.stops)
        )
        bar = drawn.colorbar(mappable, ax=ax, pad=0.01, fraction=0.05, aspect=25)
        bar.outline.set_edgecolor(self.theme.axis)
        bar.ax.tick_params(
            colors=self.theme.axis,
            labelcolor=self.theme.text,
            labelsize=px(self.theme.type.small),
        )
        if scale.ticks:
            bar.set_ticks(
                [at for at, _ in scale.ticks], labels=[t for _, t in scale.ticks]
            )
        if scale.title:
            bar.set_label(
                escape(scale.title),
                fontsize=px(self.theme.type.small),
                color=self.theme.text,
            )

    # --- the primitives ---------------------------------------------------------

    def _series(self, drawn: Any, ax: Any, panel: Panel) -> None:
        from matplotlib.lines import Line2D

        theme = self.theme
        markers = panel.options.get("markers")
        width = px(theme.marks.line)
        size = px(theme.marks.marker)
        for line in common.lines(self.fig, panel):
            colour = self.colours.of(line.group)
            x = self._x(line.x)
            if line.lo is not None:
                for start, stop in common.runs(
                    np.isfinite(line.lo) & np.isfinite(line.hi)
                ):
                    ax.fill_between(
                        x[start:stop],
                        line.lo[start:stop],
                        line.hi[start:stop],
                        facecolor=_rgba(colour, theme.marks.band_opacity),
                        edgecolor="none",
                        zorder=1,
                    )
            ax.plot(
                x,
                line.y,
                color=colour,
                linewidth=width,
                marker="o" if markers else None,
                markersize=size,
                markeredgewidth=0,
                zorder=2,
            )
            alone = common.isolated(line.y)
            if not markers and alone.any():
                ax.plot(
                    x[alone],
                    line.y[alone],
                    linestyle="none",
                    marker="o",
                    markersize=size,
                    markeredgewidth=0,
                    color=colour,
                    zorder=2,
                )
            self._group(
                line.group,
                Line2D(
                    [],
                    [],
                    color=colour,
                    linewidth=width,
                    marker="o" if markers else None,
                ),
            )

    def _strip(self, drawn: Any, ax: Any, panel: Panel) -> None:
        from matplotlib.lines import Line2D

        theme = self.theme
        strip = common.strip(self.fig, panel, theme)
        cells = strip.cells
        drawn_cells = cells[cells["colour"].notna()]
        starts = _dates(drawn_cells["start"])
        ends = _dates(drawn_cells["end"])
        for row in range(len(strip.rows)):
            mine = (drawn_cells["row"] == row).to_numpy()
            if mine.any():
                ax.broken_barh(
                    list(zip(starts[mine], ends[mine] - starts[mine])),
                    (row - 0.4, 0.8),
                    facecolors=list(drawn_cells["colour"][mine]),
                    edgecolor="none",
                    zorder=2,
                )
        marked = cells[cells["marked"].to_numpy(dtype=bool)]
        if len(marked):
            centres = (_dates(marked["start"]) + _dates(marked["end"])) / 2
            ax.plot(
                centres,
                marked["row"].to_numpy(dtype=float),
                linestyle="none",
                marker="v",
                markersize=px(theme.marks.marker + 2),
                color=theme.warning,
                markeredgecolor=theme.paper,
                markeredgewidth=px(0.75),
                zorder=3,
            )
            label = panel.options["mark_label"]
            self._entry(
                ("mark", label),
                label,
                Line2D(
                    [],
                    [],
                    linestyle="none",
                    marker="v",
                    color=theme.warning,
                    markeredgecolor=theme.paper,
                ),
            )
        ax.set_yticks(range(len(strip.rows)), [escape(r) for r in strip.rows])
        ax.set_ylim(len(strip.rows) - 0.5, -0.5)
        ax.grid(False, axis="y")
        if len(cells):
            ax.set_xlim(
                float(_dates(cells["start"]).min()), float(_dates(cells["end"]).max())
            )
        if strip.scale is not None and np.isfinite(strip.scale.values).any():
            self._colourbar(drawn, ax, strip.scale)

    def _heatmap(self, drawn: Any, ax: Any, panel: Panel) -> None:
        from matplotlib.colors import ListedColormap
        from matplotlib.patches import Patch

        theme = self.theme
        cells = common.grid(self.fig, panel)
        scale = common.colour_scale(
            cells.values.ravel(), panel, theme, panel.options["palette"]
        )
        values = scale.values.reshape(cells.values.shape)
        xs = self._grid_axis(ax, "x", cells.xs, common.kind_of(self.fig, panel, "x"))
        ys = self._grid_axis(ax, "y", cells.ys, common.kind_of(self.fig, panel, "y"))
        if np.isfinite(values).any():
            ax.pcolormesh(
                common.edges(xs, panel.axes.x.scale),
                common.edges(ys, panel.axes.y.scale),
                np.ma.masked_invalid(values),
                cmap=_colourmap(scale.stops),
                vmin=scale.low,
                vmax=scale.high,
                shading="flat",
                zorder=1,
            )
            self._colourbar(drawn, ax, scale)
        if cells.gaps.any():
            gap = _rgba(theme.token("gap"), theme.marks.gap_cell_opacity)
            ax.pcolormesh(
                common.edges(xs, panel.axes.x.scale),
                common.edges(ys, panel.axes.y.scale),
                np.ma.masked_where(~cells.gaps, np.ones(cells.gaps.shape)),
                cmap=ListedColormap([gap]),
                vmin=0,
                vmax=1,
                shading="flat",
                zorder=1,
            )
            label = panel.options["gap_label"]
            self._entry(("gap", label), label, Patch(facecolor=gap, edgecolor="none"))
        ax.grid(False)

    def _grid_axis(
        self, ax: Any, axis: str, values: list[Any], kind: str
    ) -> np.ndarray:
        """A heatmap axis' cell centres: dates and numbers as they are, a
        category at its index, labelled."""
        if kind == "time":
            return _dates(pd.Series(values))
        if kind == "number":
            return np.asarray(values, dtype="float64")
        positions = np.arange(len(values), dtype="float64")
        names = [escape(common.label(v)) for v in values]
        if axis == "x":
            ax.set_xticks(positions, names)
        else:
            ax.set_yticks(positions, names)
        return positions

    def _scatter(self, drawn: Any, ax: Any, panel: Panel) -> None:
        """Every point in one call, so that the larger lie behind the
        smaller whatever their group; legend entries for the groups, the
        hollow points and the sizes."""
        from matplotlib.lines import Line2D

        theme = self.theme
        drawn_points = common.points(self.fig, panel, theme, self.colours)
        points = drawn_points.points
        hollow = points["hollow"].to_numpy(dtype=bool)
        opacity = theme.marks.point_opacity
        ax.scatter(
            points["x"],
            points["y"],
            s=px(points["diameter"].to_numpy(dtype=float)) ** 2,
            facecolors=[
                (0, 0, 0, 0) if h else _rgba(c, opacity)
                for c, h in zip(points["colour"], hollow)
            ],
            edgecolors=[c if h else "none" for c, h in zip(points["colour"], hollow)],
            linewidths=[px(theme.marks.line) if h else 0 for h in hollow],
            zorder=2,
        )
        if (
            drawn_points.scale is not None
            and np.isfinite(drawn_points.scale.values).any()
        ):
            self._colourbar(drawn, ax, drawn_points.scale)
        for group in drawn_points.groups:
            self._group(
                group,
                Line2D(
                    [],
                    [],
                    linestyle="none",
                    marker="o",
                    color=_rgba(self.colours.of(group), opacity),
                    markeredgewidth=0,
                ),
            )
        label = panel.options["hollow_label"]
        if label and hollow.any():
            self._entry(("hollow", label), label, _hollow_marker(theme))
        if drawn_points.sizes:
            title = panel.axes.size.label
            index = self.fig.panels.index(panel)
            if title:
                self._entry(("size", index), title, Line2D([], [], linestyle="none"))
            for value, diameter in drawn_points.sizes:
                self._entry(
                    ("size", index, value),
                    common.number_text(value),
                    Line2D(
                        [],
                        [],
                        linestyle="none",
                        marker="o",
                        markersize=px(diameter),
                        color=_rgba(theme.muted, opacity),
                        markeredgewidth=0,
                    ),
                )
        self._labels(ax, panel)

    def _labels(self, ax: Any, panel: Panel) -> None:
        for _, point in common.labelled(self.fig, panel).iterrows():
            ax.annotate(
                escape(point["text"]),
                (point["x"], point["y"]),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=px(self.theme.type.small),
                color=self.theme.text,
                zorder=4,
            )

    def _sticks(self, drawn: Any, ax: Any, panel: Panel) -> None:
        from matplotlib.lines import Line2D

        theme = self.theme
        sticks = common.sticks(self.fig, panel, theme, self.colours)
        ax.axhline(0, color=theme.muted, linewidth=px(theme.marks.rule), zorder=1)
        if len(sticks):
            ax.vlines(
                sticks["x"],
                0,
                sticks["y"],
                colors=list(sticks["colour"]),
                linewidth=px(theme.marks.line),
                zorder=2,
            )
        for group in common.ordered(sticks["group"]):
            colour = self.colours.of(group)
            self._group(
                group, Line2D([], [], color=colour, linewidth=px(theme.marks.line))
            )
        marked = sticks[sticks["marked"].to_numpy(dtype=bool)]
        if len(marked):
            ax.plot(
                marked["x"],
                marked["y"],
                linestyle="none",
                marker="D",
                markersize=px(theme.marks.marker + 1),
                color=theme.warning,
                markeredgecolor=theme.paper,
                markeredgewidth=px(0.75),
                zorder=3,
            )
            label = panel.options["mark_label"]
            self._entry(
                ("mark", label),
                label,
                Line2D(
                    [],
                    [],
                    linestyle="none",
                    marker="D",
                    color=theme.warning,
                    markeredgecolor=theme.paper,
                ),
            )
        self._labels(ax, panel)

    def _bars(self, drawn: Any, ax: Any, panel: Panel) -> None:
        from matplotlib.patches import Patch

        theme = self.theme
        place, bars = common.bars(self.fig, panel, theme)
        for group in place.groups:
            mine = bars[[g == group for g in bars["group"]]]
            if not len(mine):
                continue
            colour = self.colours.of(group)
            ax.bar(
                mine["position"],
                mine["height"],
                width=place.width,
                bottom=mine["bottom"],
                color=colour,
                zorder=2,
            )
            self._group(group, Patch(facecolor=colour))
        spans = common.intervals(bars)
        if len(spans):
            ax.errorbar(
                spans["position"],
                spans["middle"],
                yerr=spans["half"],
                fmt="none",
                ecolor=theme.text,
                elinewidth=px(1),
                capsize=px(3),
                capthick=px(1),
                zorder=3,
            )
        self._categories(ax, place)

    def _box(self, drawn: Any, ax: Any, panel: Panel) -> None:
        from matplotlib.patches import Patch

        theme = self.theme
        place, boxes = common.boxes(self.fig, panel, theme)
        means = "mean" in panel.encodings
        fill = theme.marks.box_opacity
        for group in place.groups:
            mine = boxes[[g == group for g in boxes["group"]]]
            if not len(mine):
                continue
            colour = self.colours.of(group)
            line = {"color": colour, "linewidth": px(theme.marks.line)}
            for hollow in (False, True):
                part = mine[mine["hollow"].to_numpy(dtype=bool) == hollow]
                if not len(part):
                    continue
                ax.bxp(
                    [
                        {
                            "q1": row["q1"],
                            "med": row["median"],
                            "q3": row["q3"],
                            "whislo": row["lo"],
                            "whishi": row["hi"],
                            "mean": row["mean"],
                            "fliers": [],
                        }
                        for _, row in part.iterrows()
                    ],
                    positions=part["position"].tolist(),
                    widths=place.width * 0.9,
                    patch_artist=True,
                    showmeans=means,
                    meanline=True,
                    showfliers=False,
                    manage_ticks=False,
                    boxprops={
                        "facecolor": (0, 0, 0, 0) if hollow else _rgba(colour, fill),
                        "edgecolor": colour,
                        "linewidth": px(theme.marks.line),
                    },
                    medianprops={
                        "color": colour,
                        "linewidth": px(2 * theme.marks.line),
                    },
                    whiskerprops=line,
                    capprops=line,
                    meanprops={
                        "color": colour,
                        "linestyle": "--",
                        "linewidth": px(theme.marks.line),
                    },
                    zorder=2,
                )
            self._group(group, Patch(facecolor=_rgba(colour, fill), edgecolor=colour))
        label = panel.options["hollow_label"]
        if label and boxes["hollow"].to_numpy(dtype=bool).any():
            self._entry(
                ("hollow", label),
                label,
                Patch(facecolor=(0, 0, 0, 0), edgecolor=theme.muted),
            )
        self._categories(ax, place)

    def _categories(self, ax: Any, place: common.Places) -> None:
        ax.set_xticks(
            range(len(place.categories)), [escape(c) for c in place.categories]
        )
        ax.set_xlim(-0.5, len(place.categories) - 0.5)
        ax.grid(False, axis="x")
        self.categorised.append((ax, list(place.categories)))

    def _table(self, ax: Any, panel: Panel) -> None:
        """A table filling the panel: a row per frame row and one for the
        headers, each column as wide as its widest text, numbers set to
        the right."""
        from matplotlib.table import Table

        theme = self.theme
        ax.set_axis_off()
        table = common.table(self.fig, panel)
        if not table.headers:
            return
        drawn = Table(ax, bbox=[0, 0, 1, 1])
        family = font_family(theme.type.families)
        marked = themes.blend(theme.warning, theme.paper, theme.marks.span_opacity)
        lines = [table.headers, *table.rows]
        for row, cells in enumerate(lines):
            for column, text in enumerate(cells):
                cell = drawn.add_cell(
                    row,
                    column,
                    width=1,
                    height=1,
                    text=escape(text),
                    loc="right" if table.numeric[column] else "left",
                    facecolor=(
                        theme.plot
                        if row == 0
                        else marked
                        if table.marked[row - 1]
                        else theme.paper
                    ),
                    edgecolor=theme.axis,
                )
                cell.set_linewidth(px(0.5))
                cell.PAD = 0.04
                cell.get_text().set(
                    color=theme.text,
                    fontfamily=family,
                    fontweight="bold" if row == 0 else "normal",
                )
        drawn.auto_set_font_size(False)
        drawn.set_fontsize(px(theme.type.small))
        drawn.auto_set_column_width(list(range(len(table.headers))))
        ax.add_table(drawn)


def _hollow_marker(theme: themes.Theme) -> Any:
    from matplotlib.lines import Line2D

    return Line2D(
        [],
        [],
        linestyle="none",
        marker="o",
        markerfacecolor="none",
        markeredgecolor=theme.muted,
        markeredgewidth=px(theme.marks.line),
    )


@functools.cache
def _colourmap(stops: tuple[str, ...]) -> Any:
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list("mascope_viz", list(stops), N=256)


def _dates(column: pd.Series) -> np.ndarray:
    """Times as matplotlib's date numbers, from their instants."""
    import matplotlib.dates as mdates

    column = pd.Series(column)
    if column.dt.tz is not None:
        column = column.dt.tz_convert("UTC").dt.tz_localize(None)
    return np.asarray(
        mdates.date2num(column.to_numpy(dtype="datetime64[ns]")), dtype=float
    )


def _date(moment: pd.Timestamp) -> float:
    return float(_dates(pd.Series([moment]))[0])
