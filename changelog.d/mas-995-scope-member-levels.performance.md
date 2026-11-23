- **A filtered Batch peaks ledger loads faster.** The ledger's identification
  levels are read for the rows the filters keep, not for the whole batch: a
  ledger narrowed to a tier on a batch of several hundred thousand members
  loads 3.5 to 13 times faster, and an unfiltered one as fast as before.
