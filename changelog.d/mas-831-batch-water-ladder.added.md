- **Measure the reagent ions' water ladders on a batch's own samples.** A
  soft-ionization source carries its reagent ions hydrated, and on a humid
  stretch of a batch the higher hydrates read as organics. *Measure water
  ladders*, a new batch operation in the **Batch peaks** ledger, reads each
  reagent core's ladder, Br-.(H2O)n, NO3-.(H2O)n and the like up to 45
  waters, per stretch of acquisition. A hydrate that is in half the
  stretch's samples with every hydrate below it, three times as often as
  at the masses beside it, and on a TOF shows no carbon, is the reagent's
  ion. Its peak is claimed as such in that stretch's samples, in the batch
  and in each sample's latest in-app run, its isotope lines with it.
  Samples assigned by hand, named by a target or reference list, or read
  as another ion's line are kept. A hydrate claimed this way is the batch's
  word to *Decide known species*, which does not spread it over the
  stretches where it did not pass. A drop mark beside the formula gives the
  rung and how often it was seen, and the run's summary reports how far
  each ladder reached, the rows that left the ledger as analytes, and the
  share of signal nothing explains before and after. `batch_peaks.list`
  serves the reading as `water_ladder`; POST
  `/api/batch-peaks/batch/{id}/measure-water-ladders` runs it as a batch
  run.
