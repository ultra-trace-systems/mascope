- **Peak assignment reads one measurement model per run.** The centre a mass
  error is read from, the width it is judged in and the tolerance a line's
  share is read at are one model, fitted after Stage A and again on the run's
  corroborated readings. Where the second fit moves the centre or the width
  by more than a quarter of a width, every row is scored again at it before
  any cap, its P(correct) and its stored alternatives' fits with it;
  otherwise the run stays at the first. A row's fit score and its
  *mass z* are now read at the same centre and width, and the search's
  readings are re-scored at the run's model instead of one fitted on the
  readings themselves. A run whose readings are too few for the second fit
  now caps at the first, where its mass gate used to stand down. The run
  records the model once, as `measurement_model` in its configuration, in
  place of `pattern_scoring` and `mass_calibration`; an older run is read
  at what it scored at. A decoy scores and caps at its control's model,
  and a decoy of a control from before the record is refused.
