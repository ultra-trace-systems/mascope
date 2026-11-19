- **A stitched sample's TIC is its stitched spectrum's.** A sample stitched
  from several scan ranges took the mean TIC over the scans of every range
  pooled, which divided each range's ion current by scans that record other
  m/z: on a five-range acquisition it read under a third of the spectrum's
  TIC, and the batch charts drew the reagent ion above the TIC trace. It is
  now each range's mean TIC per scan, for the share of that range's signal
  in the m/z it supplies to the stitched spectrum. New samples get it at
  once; the operator runs `db script run fix_sample_item_tic` again after
  the upgrade to rewrite the stored ones (`docs/maintaining.md`).
