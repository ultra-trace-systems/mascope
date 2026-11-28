- **A card opened from the batch ledger counts the samples that agree.** Its batch line
  now reads *in 5 samples, 2 of the 3 whose run reads a formula agree* instead of a
  share. A formula pinned by hand that no sample's run reads no longer shows *0% of
  their formulas agree*: the line gives the number of samples alone. The line's tier chip
  shows the cap mark the ledger's chip shows. The batch peaks read and the SDK's
  `batch_peaks.list` serve the two counts as `n_assigned` and `n_winner`.
