- **A peak-assignment run records the signal-to-noise it scored on.** The
  run's `pattern_scoring` snapshot says where the per-peak signal-to-noise
  came from (`snr_source`: `peak_list`, or `none` for a file that stores no
  estimate, or a column with no value in it), over how many peaks
  (`snr_peaks`), and how the column ranks against the intensity scored on
  (`snr_intensity_spearman`, read over 30 peaks or more; on a TOF the
  intensity is the peak's area, not its height). Near one is a noise estimate that follows the signal; a
  TOF file whose peaks were detected before the fix below reads near zero
  until they are detected again.
