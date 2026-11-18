- **Fewer false probable blends in the batch ledger.** The probable-blend
  test now skips two formulas when the smaller holds under 3% of the batch
  peak's samples at candidate or better, or the two together under 60%. A
  parting in acquisition time must also step the peak's level by 1.25 times
  where the reading switches. On a copy of real batches the flags fell from
  92 to 57. The ledger's `blend` record carries `shares` and the time
  reading's `level_step`. A batch shows the new flags after
  *Rebuild batch ledger*.
