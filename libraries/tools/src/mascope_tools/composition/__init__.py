from .finder import assign_compositions
from .heuristic_filter import formula_plausibility
from .mass_accuracy import (
    MASS_ACCURACY_MIN_ANCHORS,
    PRED_SIGMA_PPM,
    fit_mass_accuracy,
    fit_sample_mass_accuracy,
    mass_accuracy_anchors,
    scoring_sigma_ppm,
)
from .models import CompositionSearchConfig, HeuristicFilterConfig, PatternScoring
from .profiles import resolve_fallback_sigma_ppm, resolve_match_tolerance_ppm


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
# `PatternScoring` is exported for the same reason as the two configs beside
# it: a caller that runs a search describes its sample with all three, and the
# scoring one is what makes the answer instrument-correct rather than
# Orbitrap-shaped.
#
# The mass-accuracy names and `resolve_fallback_sigma_ppm` are exported for the
# same reason once removed: `PatternScoring` is only instrument-correct if the
# caller can fill it in, and what fills its width is a fit over the sample's
# own anchors falling back to the instrument class's. An engine that scores a
# sample against Mascope's - a reference run, a comparison, a re-analysis -
# must judge a mass error at the width Mascope judges it at, and a second
# implementation of the fit would make every difference between the two
# engines partly a difference in how each measured the ruler.
__all__ = [
    "assign_compositions",
    "CompositionSearchConfig",
    "fit_mass_accuracy",
    "fit_sample_mass_accuracy",
    "formula_plausibility",
    "HeuristicFilterConfig",
    "mass_accuracy_anchors",
    "MASS_ACCURACY_MIN_ANCHORS",
    "PatternScoring",
    "PRED_SIGMA_PPM",
    "resolve_fallback_sigma_ppm",
    "resolve_match_tolerance_ppm",
    "scoring_sigma_ppm",
]
