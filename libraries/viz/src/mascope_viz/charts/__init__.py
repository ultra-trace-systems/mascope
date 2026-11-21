"""Charts: the Figure spec every chart kind returns and every renderer draws
(:mod:`.spec`). Pandas and NumPy only: a server can build specs without the
renderers (``docs/dev/visual_digest.md``, section 2.1)."""

from .spec import (
    FIGURE_SPEC_VERSION,
    PRIMITIVES,
    Axes,
    Axis,
    Figure,
    Panel,
    subtitle,
)


__all__ = [
    "FIGURE_SPEC_VERSION",
    "PRIMITIVES",
    "Axes",
    "Axis",
    "Figure",
    "Panel",
    "subtitle",
]
