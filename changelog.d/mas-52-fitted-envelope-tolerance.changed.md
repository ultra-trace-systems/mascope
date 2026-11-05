- **Peak assignment reads an isotope line's height at a tolerance each run
  fits from its own confirmed envelopes.** The fit score held every matched
  line to 5% of its predicted share and charged a missing line from an
  expected signal-to-noise of 3. On an Orbitrap a weak 13C line reads low, or
  is missing, because each scan keeps a peak only where it clears that scan's
  noise: on the assignment gate's sets a median 0.72 to 0.92 of the share at
  an expected signal-to-noise of 3 to 10, with most such lines absent. A run
  now measures, on the envelopes its target library and reference lists
  confirmed, how far below its share a 13C line reads at each
  signal-to-noise and how often it is there; a line costs nothing between
  that reading and its full share, and a missing line counts only where the
  file's lines are usually present. A file with too few such lines takes the
  measurement pooled with the files of its batch acquired at the same
  microscans and resolution, then the defaults. A TOF run fits one offset and
  one spread of its lines' reading instead. Stage A's rows, the untargeted
  search and its re-score all read the fitted tolerance, and a formula
  measured on its own afterwards reads the one its sample's latest run
  recorded, or the defaults where it has none. The run records what it fitted under `envelope_tolerance` in its
  configuration (`docs/user/how-it-works/peak-assignment.md`, *The fit
  score*).
