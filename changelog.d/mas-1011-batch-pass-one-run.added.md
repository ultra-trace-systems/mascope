- **Assign a batch through the API and the SDK.**
  `POST /api/batch-peaks/batch/{id}/assign` and
  `mascope.batch_peaks.assign(batch_id, from_stage=None, fold="auto")` run
  a pass of the batch stages as one run, folding every sample first where
  the ledger cannot be a starting point: no batch peaks, an import current,
  a target collection or reference list changed, or another engine version.
  `.../assign/cancel` and `cancel_assign` stop it between stages, keeping
  what it wrote. The pass reports one progress entry, with each stage's
  start as a step of it.
