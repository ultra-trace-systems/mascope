"""Charts: the Figure spec every chart kind returns and every renderer draws
(:mod:`.spec`), and the registry of the chart kinds (:mod:`.registry`).
Pandas and NumPy only: a server can build specs without the renderers
(``docs/dev/visual_digest.md``, section 2.1)."""

from . import registry
from .registry import ChartKind, Param
from .spec import (
    CONTEXT_FIELDS,
    FIGURE_SPEC_VERSION,
    PRIMITIVES,
    Axes,
    Axis,
    Context,
    Figure,
    Panel,
    subtitle,
)


__all__ = [
    "CONTEXT_FIELDS",
    "FIGURE_SPEC_VERSION",
    "PRIMITIVES",
    "Axes",
    "Axis",
    "ChartKind",
    "Context",
    "Figure",
    "Panel",
    "Param",
    "registry",
    "subtitle",
]
