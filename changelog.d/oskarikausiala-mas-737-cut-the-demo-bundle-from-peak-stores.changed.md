- **The demo dataset is now bundle v1.5.0**
  ([10.5281/zenodo.24526135](https://doi.org/10.5281/zenodo.24526135)),
  1.4.4's raw files rebuilt with this release's peak detection and a
  corrected seed. Every Orbitrap peak store now says how many of its averaged
  scans hold each peak (`n_scans`, `n_scans_total`), which 1.4.4's stores left
  empty. Five uronium targets that read a lighter oxygenate's urea adduct as a
  nitrogen-bearing neutral's ammonium adduct now name the oxygenate (C9H18O to
  C9H18O4, C9H20O3), m/z 171.1492 is C9H18N2O through `[M+H]+`, and C9H15NO
  and C6H17NO4 (triethylene glycol's ammonium adduct written as a neutral) are
  gone. 41,425 golden peaks against 42,424 and 17,435 ions against 17,394:
  every row that left or arrived is one of those compounds, and every golden
  peak the two bundles share keeps its height. The seed now carries the
  shipped reference lists, so the Docker demo's rebuild reads them as
  `mascope demo --rebuild` always did, and takes about three times as long;
  the reproducibility workflow gives it 90 minutes. 1.4.4 stays registered so
  a pinned run still resolves.
