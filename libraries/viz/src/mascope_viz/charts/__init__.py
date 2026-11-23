"""Charts: the Figure spec every chart kind returns and every renderer draws
(:mod:`.spec`), the registry of the chart kinds (:mod:`.registry`), and the
chart kinds themselves, each registered when its module is imported here.
The species charts, ``species_series`` (:mod:`.series`), ``diurnal``
(:mod:`.diurnal`) and ``period_boxes`` (:mod:`.periods`), draw the species
a ``species`` parameter names (:mod:`.species`). The composition charts,
``spectrogram`` (:mod:`.spectrogram`), ``period_spectrum`` (:mod:`.spectrum`)
and ``van_krevelen`` (:mod:`.van_krevelen`), draw the store's assigned
peaks. Pandas and NumPy only: a server can build specs without the
renderers (``docs/dev/visual_digest.md``, section 2.1)."""

# The chart modules register their kinds as they are imported
from . import (  # noqa: F401
    coverage,
    diurnal,
    periods,
    registry,
    series,
    signal,
    species,
    spectrogram,
    spectrum,
    summary,
    top,
    van_krevelen,
)
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
    "diurnal",
    "periods",
    "registry",
    "series",
    "species",
    "spectrogram",
    "spectrum",
    "subtitle",
    "van_krevelen",
]
