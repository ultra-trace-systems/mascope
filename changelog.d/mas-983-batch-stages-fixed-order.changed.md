- **The batch's steps run as stages of one fixed order.** Water ladders,
  known species, the untargeted search, the isotope lines (strip, then
  stamp), the series, the co-variation, the ratio stability, the batch
  checks and the probable blends now run in that order, so the order the
  buttons are pressed in no longer changes the batch ledger. Each step's
  button starts the stages at its own, or at an earlier stage that is not
  up to date, and the stages after it run again on what it wrote.
  `POST /api/batch-peaks/batch/{id}/run-stages` starts them at a named
  stage, or at the first stage that is not current, and the batch ledger
  read says where every stage stands (`stages`, `stale_stages`): a sample
  folded in again, a rebuild or an import makes every stage that ran stale,
  and a stage that stops partway is stale itself. Running the stages again
  brings a sample folded in since up to the batch's readings: its rungs and
  known species claimed, its rows stripped to their parent's line, and the
  search's compositions measured on its members that read nothing.

- **What a batch stage writes in a sample is judged as a run judges it.**
  After a stage rewrites a sample's members, the sample's readings are
  judged again through the same stages a run of the sample goes through,
  and each member the batch wrote takes the tier the run's rules give its
  reading: a reading another channel of the same compound corroborates is
  lifted, and the mass gate and the other caps apply to a known species'
  claim, which a step used to put at candidate directly. A series that
  proposes the formula a member already reads now lifts the member right
  after *Detect series*, with no new run. Each member a stage rewrote
  carries the stage's mark, and the ledger's records carry the version of
  the stage run that wrote them.
