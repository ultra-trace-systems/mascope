- **A sample cut inside an MS2 group names its spectra as its summary does.**
  When a sample's time window holds only some of a precursor's fragment
  scans, its averaged MS2 spectra are still read from the raw file, but now
  over the window's scans of each group the MS2 store made of the whole
  file. Before, the window's scans were grouped again, so a precursor whose
  m/z drifts from scan to scan could get another parent m/z there than in
  the summary, and asking for that group's scans answered 404.
