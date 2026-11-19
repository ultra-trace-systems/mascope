- **A sample only folded into the batch ledger is read at the model its
  fold fitted.** The ingest fold and *Rebuild batch ledger* keep the model
  they scored a sample's rows at, so the inspector, *Use this* and the
  batch's *Search untargeted* read the sample there rather than at the
  instrument class's width and the default line tolerance.

- **The batch search reads a sample's width, centre and line tolerance from
  one run.** It read each from its own query for the latest run, so a run
  completing mid-search could mix two runs' numbers.
