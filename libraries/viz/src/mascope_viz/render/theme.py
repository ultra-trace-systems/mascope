"""The theme both renderers draw in: colours, type and marks, light and dark.

A spec names colours by token (:data:`mascope_viz.charts.spec.COLOUR_TOKENS`)
and palettes by name; a :class:`Theme` gives them values, so one spec draws
in either theme. The values follow the web app's
(``docs/dev/frontend_design_system.md``, section 3), so a figure reads as one
system with the app's own charts:

- **Grounds.** The app's content ground (white on light, the charcoal
  ramp's 10 % rung on dark), its body ground, and the plot area its charts
  draw (``BaseChartPlotly.vue``: the chip ground at 30 % over the content
  ground).
- **Categorical.** The first five are the app's five-series palette in
  order: the theme's accent, the info teal, the guide's Series 3 purple,
  its Series 4 green and its Series 5 pink. Series 4, ``#A6C33C``, reads
  1.8:1 on the light ground, so light takes the same hue darkened in OKLCH
  to clear 3:1. Five more follow, one hue each in both themes (green,
  blue, ochre, plum, coral), each lightness chosen for contrast and for
  distance from the rest. Past ten, the colours repeat: a chart of more
  groups draws small multiples or its top N.
- **Sequential**: magma, dark to light on the dark ground and light to dark
  on the light one, so the high values stand out on either and the low
  values stay apart from a gap, which shows the ground.
- **Diverging**: the app's formula blue through a neutral grey to the
  accent, interpolated in OKLab; the centre is grey, not the ground, so
  "no change" stays apart from "no data".
- **Type**: IBM Plex Sans, as the app; a renderer falls back to the next
  family it finds. Sizes are the app's three, in CSS pixels.

:func:`check` is the contrast check the palettes pass, in both themes:
every categorical colour and both ends of the diverging ramp at 3:1 or
more against every ground (WCAG 2.2, 1.4.11: a graphic's contrast), any
two categorical colours at least :data:`DISTINCT` apart in CIELAB, the
sequential ramp's lightness monotonic with its high end at 3:1 or more,
and both ramps' low ends, and the diverging centre, apart from every
ground.

Pure Python: importing it imports neither renderer.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field


#: The themes, by name.
THEMES = ("light", "dark")

#: The contrast a graphic needs against its ground (WCAG 2.2, 1.4.11).
GRAPHIC_CONTRAST = 3.0

#: The CIELAB distance (CIE76) two categorical colours keep at least.
DISTINCT = 20.0

#: The CIELAB distance a ramp's low end, and the diverging centre, keep
#: from every ground, so a cell of it is told from a gap.
APART = 5.0


@dataclass(frozen=True)
class Marks:
    """Mark sizes in CSS pixels, and the opacities of fills."""

    line: float = 1.5
    rule: float = 1.0
    marker: float = 5.0
    size_min: float = 4.0
    size_max: float = 18.0
    band_opacity: float = 0.2
    span_opacity: float = 0.12
    gap_opacity: float = 0.18
    bar_width: float = 0.8


@dataclass(frozen=True)
class Type:
    """Type: the families in order of preference, sizes in CSS pixels."""

    families: tuple[str, ...] = ("IBM Plex Sans", "DejaVu Sans", "Arial", "sans-serif")
    mono: tuple[str, ...] = ("IBM Plex Mono", "DejaVu Sans Mono", "monospace")
    title: float = 15.0
    subtitle: float = 13.0
    base: float = 13.0
    small: float = 11.0


@dataclass(frozen=True)
class Layout:
    """Sizes of a drawn figure, in CSS pixels."""

    width: float = 768.0
    panel: float = 250.0
    header: float = 64.0
    note: float = 18.0
    table_row: float = 22.0
    #: The PNG's pixels per CSS pixel.
    scale: float = 2.0


@dataclass(frozen=True)
class Theme:
    """One theme's values.

    :ivar paper: The ground around the plots.
    :ivar plot: The ground of a plot's area.
    :ivar grounds: Every ground a colour is checked against.
    :ivar text: Titles, labels and ticks.
    :ivar muted: Subtitles, notes, rules and the gap fill.
    :ivar grid: Grid lines.
    :ivar axis: Axis lines and table rules.
    """

    name: str
    paper: str
    plot: str
    grounds: tuple[str, ...]
    text: str
    muted: str
    grid: str
    axis: str
    accent: str
    success: str
    warning: str
    error: str
    info: str
    categorical: tuple[str, ...]
    sequential: tuple[str, ...]
    diverging: tuple[str, ...]
    type: Type = field(default_factory=Type)
    marks: Marks = field(default_factory=Marks)
    layout: Layout = field(default_factory=Layout)

    def token(self, name: str) -> str:
        """The colour of a token: ``series.N``, ``accent``, ``text``,
        ``muted``, a state, or a span kind (``event``, ``gap``)."""
        if name.startswith("series."):
            index = int(name.split(".", 1)[1]) - 1
            return self.categorical[index % len(self.categorical)]
        if name == "event":
            return self.info
        if name == "gap":
            return self.muted
        if name in ("accent", "text", "muted", "success", "warning", "error", "info"):
            return getattr(self, name)
        raise KeyError(f"no colour token {name!r}")

    def palette(self, name: str) -> tuple[str, ...]:
        """A palette's colours: ``categorical``, ``sequential`` or
        ``diverging``."""
        if name not in ("categorical", "sequential", "diverging"):
            raise KeyError(f"no palette {name!r}")
        return getattr(self, name)

    def tokens(self) -> dict[str, str]:
        """Every colour token's value, for a renderer outside Python."""
        names = [f"series.{i}" for i in range(1, len(self.categorical) + 1)]
        names += ["accent", "text", "muted", "success", "warning", "error", "info"]
        names += ["event", "gap"]
        return {name: self.token(name) for name in names}


LIGHT = Theme(
    name="light",
    paper="#ffffff",
    plot="#f7f7f6",
    grounds=("#ffffff", "#f1f1ee", "#f7f7f6"),
    text="#474744",
    muted="#777774",
    grid="#e3e3e0",
    axis="#c7c7c4",
    accent="#a23f00",
    success="#1f7a46",
    warning="#8a6200",
    error="#b0281a",
    info="#0b7e8e",
    categorical=(
        "#a23f00",
        "#0b7e8e",
        "#8266ea",
        "#758d03",
        "#c93a82",
        "#069763",
        "#2c87d9",
        "#644e02",
        "#793389",
        "#d55e63",
    ),
    sequential=(
        "#fcf4b6",
        "#fec185",
        "#fc8a62",
        "#eb5760",
        "#c23b75",
        "#932b80",
        "#671b80",
        "#38106c",
        "#0e0b2b",
    ),
    diverging=(
        "#2557a7",
        "#5379b6",
        "#7f9ac3",
        "#adbbcf",
        "#dcdcd9",
        "#d1b5a7",
        "#c48f77",
        "#b46846",
        "#a23f00",
    ),
)

DARK = Theme(
    name="dark",
    paper="#1b1c1d",
    plot="#212223",
    grounds=("#1b1c1d", "#101112", "#212223"),
    text="#ffffff",
    muted="#8f9192",
    grid="#2f3032",
    axis="#464748",
    accent="#dd743b",
    success="#3fa96b",
    warning="#e9b33b",
    error="#e2503f",
    info="#16b0c4",
    categorical=(
        "#dd743b",
        "#16b0c4",
        "#8266ea",
        "#a6c33c",
        "#c93a82",
        "#43cf91",
        "#3c95e8",
        "#8c6f04",
        "#e89cf9",
        "#fe9f9f",
    ),
    sequential=(
        "#21114e",
        "#4f127b",
        "#792282",
        "#a6317d",
        "#d2426f",
        "#f3655c",
        "#fd9869",
        "#fecc8f",
        "#fcfdbf",
    ),
    diverging=(
        "#7fa7ec",
        "#718ec0",
        "#627697",
        "#545e6e",
        "#464748",
        "#6b5449",
        "#905f47",
        "#b66a43",
        "#dd743b",
    ),
)

_THEMES = {"light": LIGHT, "dark": DARK}


def get(theme: "str | Theme") -> Theme:
    """A theme by name, or the theme itself."""
    if isinstance(theme, Theme):
        return theme
    try:
        return _THEMES[theme]
    except KeyError:
        raise ValueError(f"theme={theme!r} is none of {', '.join(THEMES)}") from None


# --- colour arithmetic --------------------------------------------------------


def rgb(colour: str) -> tuple[float, float, float]:
    """A ``#rrggbb`` colour's sRGB channels in [0, 1]."""
    if len(colour) != 7 or colour[0] != "#":
        raise ValueError(f"{colour!r} is not a #rrggbb colour")
    return tuple(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def hex_colour(channels: Iterable[float]) -> str:
    """sRGB channels in [0, 1] as ``#rrggbb``."""
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in channels)


def _linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    """WCAG 2.2's relative luminance."""
    r, g, b = (_linear(c) for c in rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2.2's contrast ratio of two colours, 1 to 21."""
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def lab(colour: str) -> tuple[float, float, float]:
    """CIELAB (D65) of an sRGB colour."""
    r, g, b = (_linear(c) for c in rgb(colour))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def distance(a: str, b: str) -> float:
    """The CIELAB distance of two colours (CIE76)."""
    return math.dist(lab(a), lab(b))


def interpolate(stops: tuple[str, ...], at: float) -> str:
    """The colour at *at* in [0, 1] along *stops*, linear in sRGB between
    two stops, as both renderers interpolate a colour scale."""
    at = min(1.0, max(0.0, at))
    if len(stops) == 1:
        return stops[0]
    position = at * (len(stops) - 1)
    low = min(int(position), len(stops) - 2)
    share = position - low
    a, b = rgb(stops[low]), rgb(stops[low + 1])
    return hex_colour(x + (y - x) * share for x, y in zip(a, b))


# --- the check ------------------------------------------------------------------


def check(theme: "str | Theme") -> list[str]:
    """What in *theme*'s palettes misses the contrast it needs; empty when
    every palette passes (the module's docstring says what each must)."""
    theme = get(theme)
    problems = []

    def graphic(colour: str, what: str) -> None:
        for ground in theme.grounds:
            ratio = contrast(colour, ground)
            if ratio < GRAPHIC_CONTRAST:
                problems.append(
                    f"{theme.name}: {what} {colour} reads {ratio:.2f}:1 on {ground}"
                )

    def apart(colour: str, what: str) -> None:
        for ground in theme.grounds:
            gap = distance(colour, ground)
            if gap < APART:
                problems.append(
                    f"{theme.name}: {what} {colour} is {gap:.1f} from the ground "
                    f"{ground}, too close to tell from a gap"
                )

    for index, colour in enumerate(theme.categorical):
        graphic(colour, f"categorical {index + 1}")
    for (i, a), (j, b) in _pairs(theme.categorical):
        gap = distance(a, b)
        if gap < DISTINCT:
            problems.append(
                f"{theme.name}: categorical {i + 1} {a} and {j + 1} {b} are "
                f"{gap:.1f} apart"
            )
    lightness = [lab(colour)[0] for colour in theme.sequential]
    if not all(b > a for a, b in zip(lightness, lightness[1:])) and not all(
        b < a for a, b in zip(lightness, lightness[1:])
    ):
        problems.append(
            f"{theme.name}: the sequential ramp's lightness is not monotonic"
        )
    graphic(theme.sequential[-1], "the sequential ramp's high end")
    apart(theme.sequential[0], "the sequential ramp's low end")
    diverging = theme.diverging
    middle = len(diverging) // 2
    graphic(diverging[0], "the diverging ramp's low end")
    graphic(diverging[-1], "the diverging ramp's high end")
    apart(diverging[middle], "the diverging ramp's centre")
    for side in (diverging[middle::-1], diverging[middle:]):
        steps = [abs(lab(colour)[0] - lab(diverging[middle])[0]) for colour in side]
        if not all(b > a for a, b in zip(steps, steps[1:])):
            problems.append(
                f"{theme.name}: the diverging ramp does not move away from its "
                "centre at every step"
            )
            break
    return problems


def _pairs(colours: tuple[str, ...]) -> Iterable[tuple[tuple[int, str], ...]]:
    indexed = list(enumerate(colours))
    for k, first in enumerate(indexed):
        for second in indexed[k + 1 :]:
            yield first, second
