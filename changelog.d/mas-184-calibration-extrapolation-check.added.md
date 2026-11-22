- **A TOF calibration that extrapolates far past its calibrants is flagged.**
  A TOF fit now records how far the file's strong peaks reach (S/N 20 or
  more), and the calibration badge and dialog warn *extrapolated* when they
  reach more than 1.25 widths of the calibrant span past either end, on the
  square-root scale the TOF axis is linear in: "Calibrated on 41–148 Th;
  peaks to 450 Th are extrapolated." It marks the record below the quality
  bar, which the default gate only warns about. Files fitted before this
  release are not judged on it until they are re-calibrated.
