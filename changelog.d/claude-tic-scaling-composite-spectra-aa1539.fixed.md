- **A stitched sample's TIC is its stitched spectrum's.** A sample stitched
  from several scan ranges took the mean TIC over the scans of every range
  pooled, which divided each range's ion current by scans that record other
  m/z: on a five-range acquisition it read under a third of the spectrum's
  TIC, and the batch charts drew the reagent ion above the TIC trace. It is
  now each range's mean TIC per scan, for the share of that range's signal
  in the m/z it supplies to the stitched spectrum. New samples get it at
  once; the operator runs `db script run fix_sample_item_tic` again after
  the upgrade to rewrite the stored ones (`docs/maintaining.md`).

- **The assignment's calibrant cap reads a stitched sample's lines against
  their own scan range.** The mass gate leaves a source line brighter than
  the calibrant cap out of its anchors, by its share of the ion current.
  On a sample stitched from several scan ranges that share was taken of
  the polarity's ion current pooled over every range's scans, where the
  line is averaged over its own range's: a bright reagent line read as
  several times its share and a line of a narrow window as a fraction of
  its own. Each line's share is now of its own range's ion current, as the
  m/z calibration already measures each range's calibrants.
