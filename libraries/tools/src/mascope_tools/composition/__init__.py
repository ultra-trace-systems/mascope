from .finder import assign_compositions
from .heuristic_filter import chemical_plausibility, formula_plausibility
from .models import CompositionSearchConfig, HeuristicFilterConfig


# `formula_plausibility` is exported because it is not an internal detail of the
# filter: it is the second factor in the EVIDENCE a peak assignment is tiered on
# (fit x plausibility), so anything that wants to reproduce or predict a tier
# needs it - the backend's own `engine.evidence_for` included, and any external
# engine publishing a run into Mascope. Reached through the module path it was
# defined in, that is an uncurated import an outside caller has no promise
# about; named here it is part of the package's surface and moving it becomes a
# breaking change rather than a silent one.
__all__ = [
    "assign_compositions",
    "chemical_plausibility",
    "CompositionSearchConfig",
    "formula_plausibility",
    "HeuristicFilterConfig",
]
