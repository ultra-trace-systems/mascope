- **`mascope_tools` derives what a sample is scored at, and scores candidates
  in bulk.** `mascope_tools.composition.pattern_scoring_for_sample` builds the
  `PatternScoring` a sample is judged at from what Stage A measured of it, its
  instrument class and its match parameters, in the order the assignment
  engine applies them: the fitted width or the class's, the fitted offset or
  the reagent lines', the match window, the envelope floor and the file's
  resolving power. The engine now calls it, so an outside engine reading a
  sample through the SDK computes the same object with the same function.
  `SampleMassAccuracy` and `ReagentOffset`, its input, move into
  `mascope_tools.composition.mass_accuracy` with the stand-in offset rule.
  `score_candidates(candidates, peaks, scoring)` scores any number of
  candidate ions against a peak list as the composition finder ranks them,
  each distinct envelope predicted once and every line matched and scored on
  arrays, and returns each candidate's fit score, matched lines and per-line
  errors.
