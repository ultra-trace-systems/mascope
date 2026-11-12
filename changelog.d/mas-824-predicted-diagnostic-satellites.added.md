- **Stamp isotopologues on the batch ledger.** A new *Stamp isotopologues*
  button beside *Read co-variation* predicts the 15N, 18O, 34S, 29Si, 30Si,
  37Cl and 81Br lines of every batch peak whose consensus commits a formula,
  finds the batch peak at each line's mass, and in every sample holding both
  reads the line's height against the share the formula predicts, inside the
  envelope tolerance that sample's run fitted. A line whose height holds in at
  least eight of every ten samples that can tell, over three or more, is
  stamped as the batch peak's isotopologue: it folds under its main peak in
  the *Batch peaks* ledger with its isotope beside the offset, and the main
  peak's mark lists every isotope line predicted, found and stamped. No tier
  moves. Runs as a batch run
  (`POST /api/batch-peaks/batch/{id}/stamp-isotopologues`); the ledger's
  records, in the API and the SDK, carry it as `diagnostic_isotopologues` and
  `stamped_isotopologue_of`, and the member rows and the ledger export as
  `stamped_isotopologue_of` and `stamp_source` (`predicted`, or `picker` where
  the sample's own run had found the line).
