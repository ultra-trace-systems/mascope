- **Detect series on the batch ledger.** A new *Detect series* button beside
  *Search untargeted* walks the batch's homologous series - one CH2, O, H2O,
  CO, CO2 or C2H2O further, and the siloxane and CF2 ladders - from every
  batch peak the batch assigns, and proposes the stepped formula wherever a
  batch peak nothing assigned, or one held only at candidate, sits at its
  exact mass. Each kind of step is checked against decoy masses and kept only
  at 12 proposals and ten times what the decoys reach; the run reports the
  batch's false discovery rate. Kept proposals are measured in every sample of
  their batch peak, the row shows a stair mark naming the series, and a
  sample's next assignment run counts the series as a second observation of
  the formula (tiering rule set 12). Runs as a batch run
  (`POST /api/batch-peaks/batch/{id}/detect-series`); the ledger's records,
  in the API and the SDK, carry the series as `series`.
