- **An Orbitrap file's peak list says how many scans hold each peak.** An
  Orbitrap scan keeps a peak only where it clears that scan's noise, so a dim
  isotope line is missing from some scans, and its height, averaged over all
  of them, reads low by the share it missed. Peak detection now counts, per
  peak, the scans it is present in, and `GET /api/samples/{id}/peaks`, the
  batch's peak table, `samples.get_peaks` and `load_peaks` serve it as
  `n_scans` beside `n_scans_total`, the scans averaged; `height *
  n_scans_total / n_scans` is the peak's height over the scans that hold it.
  Both are empty for a TOF file and for an Orbitrap file detected before this
  release, until its peaks are detected again: *Detect peaks*, or
  `rebuild_stale_peak_stores` with `FILL_SCAN_COUNTS=1`, which counts such
  files in its dry run and sets their batches to rematch
  (`docs/maintaining.md`, *Peak stores a file has outgrown*).
