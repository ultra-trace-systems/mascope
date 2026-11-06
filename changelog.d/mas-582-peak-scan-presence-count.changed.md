- **Peak assignment reads a dim Orbitrap isotope line at its mean per
  scan.** A scan keeps a dim line only where it clears the scan's noise, so
  the peak list reads it low by the scans it missed, and the scans that keep
  it read it high. On a file whose peaks carry their scan counts, the fit
  score estimates the line's mean over every scan from its count and its
  signal-to-noise (the censored-normal estimate), reads it against the main
  line read the same way, and holds it to its share; the untargeted search's
  40% window reads the same share, and its lower end does not follow the
  file's fitted response for such a line, since the estimate has already
  taken that loss out. A missing line counts against a formula as before,
  from the presence the run measures from its own envelopes; that
  measurement, and the low reading it fits, stay the reading of every line
  of a file without the counts
  (`docs/user/how-it-works/peak-assignment.md`, *The fit score*).
