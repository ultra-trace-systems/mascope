- **Where a batch's assignment stands, in one read.**
  `GET /api/batch-peaks/batch/{id}/assignment-status` (the SDK's
  `mascope.batch_peaks.assignment_status`) says whether a batch has no
  batch peaks, holds known compositions only, is being assigned, stopped,
  is up to date or is behind its last pass, and why: samples added or
  removed, samples re-run, a target collection or reference list changed,
  another engine version, or a stage left stale. A pass in flight is named
  with its stage and who started it, so every user and tab sees the same
  state. Curations, verdicts and imports do not put a batch behind.
