"""The renderers: a Figure spec drawn with matplotlib (PNG, SVG, PDF) or
plotly (HTML), in the light or the dark theme (:mod:`.theme`).

- :func:`matplotlib` returns a :class:`matplotlib.figure.Figure`.
- :func:`plotly` returns a :class:`plotly.graph_objects.Figure`.
- :func:`save` writes a figure to a file by its extension.

Both draw from the spec alone, every primitive, with what they share
decided once in :mod:`.common`. matplotlib and plotly are imported when a
figure is drawn, not when this package is: :mod:`mascope_viz.data` and
:mod:`mascope_viz.charts` never import them.
"""

import os
from pathlib import Path
from typing import Any

from ..charts.spec import Figure

# The submodules first, then the functions of the same name: importing the
# submodule mascope_viz.render.plotly sets the package's attribute plotly
# to the module, and the function defined after it takes the name back.
# Python imports a submodule once, so the function keeps it.
from . import mpl as _mpl
from . import plotly as _plotly
from . import theme as _themes


#: What :func:`save` writes, by extension.
FORMATS = {
    ".png": "matplotlib",
    ".svg": "matplotlib",
    ".pdf": "matplotlib",
    ".html": "plotly",
    ".json": "the spec",
}


def matplotlib(fig: Figure, *, theme: "str | _themes.Theme" = "light") -> Any:
    """*fig* drawn with matplotlib: a :class:`matplotlib.figure.Figure`."""
    return _mpl.render(fig, theme=theme)


def plotly(fig: Figure, *, theme: "str | _themes.Theme" = "light") -> Any:
    """*fig* drawn with plotly: a :class:`plotly.graph_objects.Figure`."""
    return _plotly.render(fig, theme=theme)


def save(
    fig: Figure,
    path: "str | os.PathLike[str]",
    *,
    theme: "str | _themes.Theme" = "light",
    plotlyjs: str = "inline",
) -> Path:
    """Write *fig* to *path*, drawn by its extension (:data:`FORMATS`).

    ``.png``, ``.svg`` and ``.pdf`` are drawn with matplotlib, ``.html``
    with plotly, and ``.json`` is the spec's document
    (:meth:`~mascope_viz.charts.spec.Figure.to_json`). The same figure
    writes the same bytes on every run: a PNG always, an SVG and a PDF
    once ``SOURCE_DATE_EPOCH`` pins the date they record, an HTML file
    always (its plot's element id is the figure's).

    :param theme: ``light`` or ``dark``.
    :param plotlyjs: For HTML, ``inline`` embeds plotly.js, so the file
        opens offline; ``cdn`` loads it from plotly's CDN, for a small file.
    :return: The path written.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in FORMATS:
        raise ValueError(
            f"cannot write {path.name!r}: the formats are {', '.join(FORMATS)}"
        )
    if suffix == ".json":
        path.write_text(fig.to_json(indent=1) + "\n", encoding="ascii")
        return path
    if suffix == ".html":
        if plotlyjs not in ("inline", "cdn"):
            raise ValueError(f"plotlyjs={plotlyjs!r} is neither inline nor cdn")
        drawn = plotly(fig, theme=theme)
        drawn.write_html(
            path,
            include_plotlyjs=True if plotlyjs == "inline" else "cdn",
            full_html=True,
            div_id=f"figure-{fig.id}",
            config=_plotly.CONFIG,
        )
        return path
    import matplotlib as mpl

    resolved = _themes.get(theme)
    with mpl.rc_context(_mpl.rc(resolved)):
        drawn = _mpl.render(fig, theme=resolved)
        drawn.savefig(path, format=suffix[1:], facecolor=resolved.paper)
    return path


__all__ = ["FORMATS", "matplotlib", "plotly", "save", "theme"]
