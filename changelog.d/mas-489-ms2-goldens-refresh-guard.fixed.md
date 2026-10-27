- **`mascope demo snapshot --update` no longer drops a bundle's MS2
  goldens without a word.** When the prior manifest has MS2 goldens and
  the rebuilt database yields no MS2 spectra for a file they covered, the
  refresh now refuses, as it does for a file with no goldens at all, and
  leaves the prior goldens and snapshot in place. A file taken out of the
  raw set on purpose still drops its MS2 goldens, with a warning.
