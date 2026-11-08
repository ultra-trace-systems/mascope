- **A whole-file calibration fit of a polarity-switching file takes the
  calibrant share of the TIC of the fitted polarity's scans alone.** The
  calibrants' intensities were averaged over that polarity's scans, but the
  mean TIC they were divided by was read over every MS1 scan of the file,
  so the other polarity's ion current moved the share. A file fitted
  stream by stream was not affected. Stored calibration records keep the
  old share until the sample is refitted.
