- **A per-sample import states its engine's identification level.** A row of
  `POST /api/peak-assignments/sample/{id}/runs/import` takes an optional
  `engine_level` beside `engine_tier`, in the level scale (`5a` to `2b`;
  anything else is a 422, level 1 included), stored on the row and served by
  the sample ledger and the detail read. The row's own `level` stays Mascope's. The SDK's
  `import_run` sends it, a ledger read with `peak_assignments.get` sends its
  `level` as `engine_level`, and the peak inspector shows the engine's level
  as a chip beside the run's own. A batch import already took the field.
