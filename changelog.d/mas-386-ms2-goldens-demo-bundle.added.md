- **The demo bundle carries MS2 acquisitions, and the reproducibility check
  compares their spectra.** Bundle 1.4.1 adds four hand-started,
  stepped-energy MS2 acquisitions of alpha-pinene oxidation products in three
  reagent-ion chemistries (15N-nitrate, ammonium and uronium) to the 161 MS1
  acquisitions of 1.3.2, with the three ionization modes and the reference
  collections they bind to in the seed, so a demo stack serves MS2 spectra
  and the SDK contract suite's MS2 tests run rather than skip. The bundle
  build de-identifies a hand-started acquisition's plain name (no polarity
  segment, no run index) and aliases the second source instrument to the
  demo's label, so the files join the workspace the test suites bind to.
  `mascope demo snapshot --update` writes `expected/ms2.parquet`: every
  (precursor, activation) group of each sample with fragmentation scans, with
  its averaged centroids down to the peak store's signal-to-noise floor and
  its scan count and isolation width, and a file with MS2 spectra counts as
  covered by the goldens. The reproducibility test and `mascope demo verify`
  compare it group by group, pairing centroids by m/z within the m/z
  tolerance, so a change that moves an MS2 spectrum is caught as one that
  moves an MS1 peak is.
