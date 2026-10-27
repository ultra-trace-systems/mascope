- **The composite cut is on by default.** A raw Orbitrap file whose
  acquisition method measures several m/z ranges in a polarity now has the
  peaks of each range detected over that range's own scans and stitched into
  one spectrum per polarity, with each range calibrated and fitted an
  instrument function of its own, as on the servers that have run
  `composite_scan_streams` since 1.11.0. Until now the default pooled every
  MS1 scan of a polarity, so an ion that a short range measured read low,
  divided by the scans of every range. A file with one range in each
  polarity, which is nearly every file, is processed as before. Only files
  converted after the upgrade change: a file already converted stays pooled
  until it is re-processed, so a batch that mixes the two shows a step where
  they meet. "The composite cut" in `docs/maintaining.md` has the paced
  runbook for re-processing older files. A server that has to keep pooling
  sets `composite_scan_streams = false` in the `[backend]` section of its
  env's `prod.mascope.toml`.
