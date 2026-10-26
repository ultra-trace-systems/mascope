- **The TOF calibration audit no longer reads a family offset from two or
  three peaks.** A band's family offset is a reading of the axis only where it
  rests on at least `TOF_AUDIT_FAMILY_MIN_PEAKS` peaks (default 10). A handful
  of offsets has a small IQR by chance, so a band of two peaks could be
  reported as a -16 ppm reading. Below the minimum the report now says "no
  reading: too few peaks", and the JSON has `"peaked": false` beside the
  band's `n` and `min_peaks`.
