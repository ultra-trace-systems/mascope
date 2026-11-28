- **A sample with no run of its own shows its batch peak's level, marked as the batch's.**
  The sample ledger's Level column and the inspector's Identification level section
  read the level from one rule: a live verdict's (the sample's own, else a batch-level
  one that reaches the row), else the run's, else, on a row the batch ledger serves,
  the batch peak's own. Every level
  from the batch now wears the batch's `#` mark, a batch-level verdict's included, with
  a hover that says whose it is. Where such a row shows no level, the card says the
  true reason: the sample has no run of its own, not that the batch states none. The
  API serves the batch peak's level on such a row as `batch_level`.
