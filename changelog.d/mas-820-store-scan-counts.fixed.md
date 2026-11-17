- **An Orbitrap ion's fit score read from a stored match now agrees with
  the one computed from the file.** The fit score (match score version 2)
  reads a dim isotope line at its mean per scan, from how many of the scans
  averaged into its peak hold it. Peak assignment and the instant compound
  match had those counts, but the stored match did not keep them, so the
  batch and sample views scored the same ion without them, and differently
  wherever a line was missing from some of the scans. Each stored line now
  keeps its peak's `n_scans` and `n_scans_total` (two nullable columns on
  `match_isotope`, added without a backfill). A match stored before the
  upgrade scores as before until it is matched again.
