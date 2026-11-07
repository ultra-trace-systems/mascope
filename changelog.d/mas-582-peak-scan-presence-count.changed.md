- **Peak assignment reads a dim Orbitrap isotope line over the scans that
  hold it.** On a file whose peaks carry their scan counts, the fit score
  reads a line's share over the scans that hold the line, against the main
  line over the scans that hold that, and holds it to its share; the
  untargeted search's 40% window reads the same share. A missing line counts
  against a formula where the peak list would have held it, judged from the
  scans that hold the main line and the noise floor of a scan: from an
  expected signal-to-noise of about 4.2 on a file of four scans and 9.2 on
  one of a hundred, where it counted from 3. The low reading and the
  presence each run measures from its own envelopes stay the reading for a
  file without the counts (`docs/user/how-it-works/peak-assignment.md`, *The
  fit score*).
