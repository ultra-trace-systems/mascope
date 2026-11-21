- **The batch's readings and groups on an assignment's detail and the batch ledger.**
  An assignment's detail now carries four more of the batch's readings of its
  batch peak, as the batch ledger shows them: the homologous series with its
  root, the ratio-stability grade (partial or stale), the known-species and
  water-ladder decisions, and the probable-blend flag. It also lists the rows
  each reading spans (`batch_groups`): a series' batch peaks with their steps,
  the channels of one neutral, a co-variation reading's partners and the batch
  peak a batch check names, each with its formula, ionization, m/z, tier and
  this sample's own peak where it holds one. The batch-peaks read serves the
  same groups for the whole batch. The SDK's `peak_assignments.detail` passes
  them through.
