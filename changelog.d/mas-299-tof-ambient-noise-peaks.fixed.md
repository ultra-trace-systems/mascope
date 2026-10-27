- **A TOF peak of a few ions over an empty baseline no longer reads as a
  strong peak.** Where a TOF spectrum is mostly empty, as an ambient-mode
  one often is, the noise a peak's `signal_to_noise` divides by was the
  standard deviation of a baseline of zeros and stray ions, which shrinks
  the rarer the ions are: since 1.13.0 a peak of three ions scored ten or
  more, and a peak in a window with no stray ion had no value at all.
  The noise there is now the height one ion leaves, measured over the
  spectrum's empty stretches, so such a peak scores about the ions it
  holds. TOF m/z calibration, which keeps a calibrant candidate at a
  signal-to-noise of 10 or more, no longer takes these peaks for
  calibrants. The peak list itself does not change: these peaks are still
  detected and listed. The peak-assignment engine now weighs them as weak
  ones, with a wider mass window, so a few more of them reach candidate.
  Spectra with a baseline of their own keep their values. A file's stored
  peaks keep the old values until its peaks are detected again.
