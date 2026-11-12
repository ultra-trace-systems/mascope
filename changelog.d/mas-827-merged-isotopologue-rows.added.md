- **Strip isotopologue rows on the batch ledger.** A new *Strip isotopologue
  rows* button beside *Stamp isotopologues* reads every batch peak whose
  consensus commits a formula against the 13C, 15N, 18O, 34S, 29Si, 30Si,
  37Cl and 81Br lines of every brighter one below it. Where a batch peak sits
  at such a line (within four times the batch's own mass scatter, at least
  1 ppm) and, over three or more samples, its height over the other's is a
  median 0.4 to 1.4 times the line's predicted share with fewer than one
  sample in ten above twice it, it is that compound's isotopologue: it folds
  under the compound in the *Batch peaks* ledger, with its line beside the
  offset, and in each sample its member and its row of the latest in-app run
  become the compound's isotope line, the reading they displaced kept as the
  row's first alternative. Batch peaks curated by hand, assigned by hand in a
  sample, or with a 13C line of their own are never stripped, and imported
  runs are never rewritten. A *Stripped rows* switch in the ledger's view
  menu keeps the compounds that took rows. Runs as a batch run
  (`POST /api/batch-peaks/batch/{id}/strip-isotopologues`) whose summary lists
  the stripped and the held pairs; the ledger's records, in the API and the
  SDK, carry it as `merged_isotopologue_of` and `merged_isotopologues`.
