- **Every member of one batch peak, with what the batch changed.** A new read,
  `GET /api/batch-peaks/batch/{sample_batch_id}/members/{batch_peak_id}`,
  lists each member of a batch peak, brightest first, on the consensus formula
  or another: its sample, intensity, its run row's formula, ion, tier, role and
  level with that level's evidence and lift, and why it states no level of the
  consensus. Per member it names the batch stage that rewrote its run row (the
  known-species claim, the water ladder, the merged-isotopologue strip), the
  batch decisions the sample's own run read onto the row, and the stage whose
  mark the member carries. No screen reads it yet; the batch card will.
