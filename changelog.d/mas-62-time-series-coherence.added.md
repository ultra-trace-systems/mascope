- **Read co-variation on the batch ledger.** A new *Read co-variation* button
  beside *Detect series* reads, for every compound the batch sees through two
  ionization channels or more, how the channels' intensities follow each
  other across the batch: Pearson r of the log intensities, divided by each
  spectrum's reagent signal, with the batch's common mode taken out. At r 0.6
  or more two channels co-vary; a pair held in fewer than ten spectra
  together, or with a channel that hardly moves, is untestable. The reading
  shows in a *Co-variation* column of the *Batch peaks* ledger and in the peak
  inspector, and moves no tier. Runs as a batch run
  (`POST /api/batch-peaks/batch/{id}/read-co-variation`); the ledger's
  records, in the API and the SDK, carry it as `co_variation`, and a sample's
  assignment detail as `batch_co_variation`.
