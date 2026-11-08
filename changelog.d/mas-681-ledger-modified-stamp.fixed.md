- **A campaign store sees a batch ledger edited by hand.** A batch peak
  curated by hand, or a sample folded again into an older batch, changed the
  batch ledger without moving anything `mascope_viz` compared, so a synced
  store kept serving the old batch-peak series until it was removed. The
  current batch run now carries `ledger_utc_modified`, when its live ledger
  was last written (`GET /api/batch-peaks/batch/{id}/runs`,
  `batch_peaks.runs`), and the next sync fetches a batch whose stamp moved
  again, reporting it as `ledger edited`.
