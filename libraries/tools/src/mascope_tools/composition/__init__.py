from .finder import assign_compositions
from .heuristic_filter import formula_plausibility
from .models import CompositionSearchConfig, HeuristicFilterConfig


# `formula_plausibility` is exported because it is not an internal detail of the
# filter: it is the second factor in the EVIDENCE a peak assignment is tiered on
# (fit x plausibility), so anything that wants to reproduce or predict a tier
# needs it - the backend's own `engine.evidence_for` included, and any external
# engine publishing a run into Mascope. Reached through the module path it was
# defined in, that is an uncurated import an outside caller has no promise
# about; named here it is part of the package's surface and moving it becomes a
# breaking change rather than a silent one.
#
# `chemical_plausibility` is deliberately NOT exported alongside it, though the
# two share a name and a paper. It is a filter STAGE - one of five functions
# (with `rule_element_ratio`, `rule_valence`, `rule_senior` and
# `rule_known_chemical_space`) whose `(pl.DataFrame, **kwargs) -> (pl.Series,
# list[str])` shape exists only to slot into `apply_heuristic_rules`, log
# messages and all. None of its four siblings is exported, and naming this one
# would freeze that calling convention as public API by the very rule stated
# above. The argument for exporting does not carry over either: an engine
# reproducing a tier needs `str -> float`, not a frame and a log; bulk scoring
# is a comprehension over the memoized `formula_plausibility`, which is exactly
# what `chemical_plausibility` does inside.
__all__ = [
    "assign_compositions",
    "CompositionSearchConfig",
    "formula_plausibility",
    "HeuristicFilterConfig",
]
