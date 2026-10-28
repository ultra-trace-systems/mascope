from .finder import assign_compositions
from .heuristic_filter import formula_plausibility
from .mass_accuracy import (
    MASS_ACCURACY_MIN_ANCHORS,
    MASS_OFFSET_MIN_ANCHORS,
    PRED_SIGMA_PPM,
    ReagentOffset,
    SampleMassAccuracy,
    fit_mass_accuracy,
    fit_sample_mass_accuracy,
    mass_accuracy_anchors,
    scoring_sigma_ppm,
)
from .mechanism_notation import (
    legacy_notation,
    mechanism_key,
    parse_mechanism,
    standard_notation,
)
from .models import CompositionSearchConfig, HeuristicFilterConfig, PatternScoring
from .profiles import resolve_fallback_sigma_ppm, resolve_match_tolerance_ppm
from .scoring import pattern_scoring_for_sample, score_candidates


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
#
# `pattern_scoring_for_sample` goes one step further and fills the whole
# object in, from what Stage A measured (`SampleMassAccuracy`, with the reagent
# lines' `ReagentOffset` beside it), the instrument class and the match
# parameters, in the order Mascope's engine applies them; the engine calls it,
# so an outside engine that calls it scores the sample where Mascope did.
# `score_candidates` is the finder's ranking measurement for a whole candidate
# list at once, which is what such an engine runs over a file's first pass.
#
# The mechanism notation readers are exported for the same kind of caller: a
# mechanism is the one string an engine publishing into Mascope and Mascope
# itself must spell alike. Mascope stores and shows the standard adduct notation
# (`[M-H]-`), and an engine that still writes the legacy `-H+` converts with
# these rather than restating a grammar that has been read wrong once already.
__all__ = [
    "assign_compositions",
    "CompositionSearchConfig",
    "fit_mass_accuracy",
    "fit_sample_mass_accuracy",
    "formula_plausibility",
    "HeuristicFilterConfig",
    "legacy_notation",
    "mass_accuracy_anchors",
    "MASS_ACCURACY_MIN_ANCHORS",
    "MASS_OFFSET_MIN_ANCHORS",
    "mechanism_key",
    "parse_mechanism",
    "pattern_scoring_for_sample",
    "PatternScoring",
    "PRED_SIGMA_PPM",
    "ReagentOffset",
    "resolve_fallback_sigma_ppm",
    "resolve_match_tolerance_ppm",
    "SampleMassAccuracy",
    "score_candidates",
    "scoring_sigma_ppm",
    "standard_notation",
]
