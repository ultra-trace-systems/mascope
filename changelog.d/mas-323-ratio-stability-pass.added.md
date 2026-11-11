- **Ratio stability on the batch ledger.** A batch peak's isotope lines and
  the same compound's other ionization channels are now read against it
  across a time-ordered batch: the within-sample ratio of the two peaks, its
  spread and its drift over the acquisition time. Each pair is graded
  *stable*, *drift* (the ratio drifts while the two peaks' traces co-vary),
  *red* (it drifts otherwise, or jumps), *inconclusive* or *insufficient*, and
  each batch peak carries its most alarming pair's grade. Rebuilding the
  batch ledger grades the batch at its end, and
  `POST /api/batch-peaks/batch/{id}/recompute-continuity` grades it on
  request. The ledger's records, in the API and the SDK, carry
  `continuity_grade`, the pairs under `continuity`, and `continuity_stale`
  where a sample or a curation has moved the batch peak since. Display-only:
  no tier moves, and the ledger shows no column for it yet.
