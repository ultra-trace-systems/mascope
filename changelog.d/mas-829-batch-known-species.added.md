- **Decide known species on the batch ledger.** A new *Decide known species*
  button beside *Strip isotopologue rows* decides each ion of the source's
  own library a sample's reagent pass claimed, and each compound a reference
  list tags as background (the cyclic siloxanes first), once for the batch.
  A species whose peak is in half the samples or more, and that one sample
  read as the species on its own evidence, is present: its member in every
  sample, and the sample's row in its latest in-app run, becomes the
  species, a library ion as the reagent pass's claim with its isotope lines,
  a listed compound as the list's reading at candidate (a sample that read
  it below candidate is lifted, its fit and lines kept), the reading it
  displaced kept as the row's first alternative. Samples assigned by hand,
  named by the target library, or reading the peak as another ion's line or
  claim are kept and listed with their readings, and imported runs are never
  rewritten. A checklist mark beside the formula in the *Batch peaks* ledger
  shows the batch's verdict. A sample's next run claims a present library
  ion on the batch's word. Runs as a batch run
  (`POST /api/batch-peaks/batch/{id}/decide-known-species`); the ledger's
  records, in the API and the SDK, carry it as `known_species`.
