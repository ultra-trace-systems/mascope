- **The SDK publishes an external engine's assignment run.**
  `mascope.peak_assignments.import_run(sample_id, df, engine=...,
  engine_version=..., tier_bands=..., calibration=...)` sends a DataFrame, one
  row per peak, to the per-sample import route and returns the new run's id.
  It checks the frame before anything is sent and raises
  `ImportValidationError` naming the column and the rows: a missing required
  column, a value outside a vocabulary, a peak twice, an owner that is not a
  row, a NaN in `sample_peak_mz` or `sample_peak_intensity`, or a stated tier
  the row's fit cannot reach. A NaN elsewhere goes as null, and an `adduct`
  column fills in the mechanism id by exact notation. The rows go in as many
  requests as the route's caps call for; the first and the last are sent
  once, and any that another request on the sample holds up is sent again
  once it finishes. A frame read with `get()` imports as it is, its
  tier sent as the engine's. A refused import deletes its run, and so does a
  chunk between that goes unanswered; `abandon_import` deletes a run a lost
  answer left `importing`, and a frame well short of the sample's latest run
  is warned about. The new tutorial notebook `11_assignment_round_trip.ipynb`
  takes a sample's peaks out, publishes a small engine's run, and compares it
  with Mascope's own. The SDK is version 2026.10.29.
