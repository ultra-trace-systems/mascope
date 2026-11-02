- **A script fills the model of instruments registered before it was read.**
  An instrument whose files were all registered before the `instrument`
  table existed has no model, and the instrument list, the batch export and
  a deposit package show none for it. `mascope prod db script run
  backfill_instrument_model` gives each such Orbitrap instrument the model
  its newest file names, from the file's props or, where no props name one,
  the kept raw file's header, with the model's PSI-MS term beside it. It
  never overwrites a model already recorded, so running it again changes
  nothing; `DRY_RUN=1` shows what it would fill.
