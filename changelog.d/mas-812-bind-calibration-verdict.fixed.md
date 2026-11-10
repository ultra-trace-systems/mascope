- **A file whose chemistry was chosen by hand is no longer matched when its
  m/z calibration finds no calibrant.** **Choose chemistry** ran the file's
  calibration so that a fit with no calibration peaks counted as verified:
  the file ended **Done**, matched on its uncalibrated axis, and its status
  said nothing of the calibration. Such a file now retries at wider
  tolerances as an uploaded file does, ends **Calibration failed** when none
  finds a calibrant, and is not matched. A calibration that ends without a
  verdict at all no longer counts as verified either.
