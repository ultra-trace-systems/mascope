- **A TOF peak below a signal-to-noise of 3 is now weak, as on Orbitrap.**
  On a mostly empty TOF spectrum, typically an ambient-mode file with no
  reagent ion, detection kept a peak of one or two stray ions at most unit
  masses, and nothing flagged a TOF peak weak. Over such a baseline a
  peak's signal-to-noise is about the ions it holds, so the Orbitrap's
  cutoff now leaves these peaks out of the peak list, the spectrum's peak
  marks, m/z calibration and assignment; they stay in the file's peak
  store. Spectra whose baseline holds many counts, as most reagent-ion ones
  do, keep nearly all their peaks; a sparse one loses most of its small
  peaks too. A file keeps its old flags until its peaks are detected again.
