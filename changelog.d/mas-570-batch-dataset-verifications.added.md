- **The verification verdicts of a whole batch or dataset, in one read.**
  `GET /api/peak-assignments/batch/{sample_batch_id}/verifications` and
  `GET /api/peak-assignments/dataset/{dataset_id}/verifications` answer the
  current verdicts over every sample, one row per judged peak, with the
  sample's filename and acquisition time, the peak's m/z, the evidence level
  and the score snapshot. A batch-level verdict that reaches a sample with
  none of its own on that formula is listed as `borrowed`, as the assignment
  ledger shows it. Filters: `verdict`, `evidence_level`, `since` and
  `include_superseded`. The SDK reads them as
  `peak_assignments.verifications_for_batch` and `verifications_for_dataset`,
  and the batch data spreadsheet carries the same table as its new
  *Verifications* sheet, before *Provenance*.
