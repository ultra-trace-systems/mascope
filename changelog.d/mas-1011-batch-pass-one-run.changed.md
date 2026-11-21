- **A pass of the batch stages is one batch run.** Each step's button on the
  *Batch peaks* ledger starts a pass at its stage, and the pass is now one
  run in the run selector, with one snapshot, where it used to add up to
  eight. The history keeps the last five passes before the current one, not
  the clicks of one. The run lists every stage it ran, with its status,
  start, end and result, and a pass that a cancel or a server shutdown
  stops is closed as cancelled instead of holding the batch until a restart.
