- **A TOF sample whose calibrants scatter no longer fails calibration with an
  error.** With more than five calibrants matched, the residual filter could
  keep only one, and the fit refused it as a server error. Such a sample is
  now reported as not having enough calibration peaks, retried with a wider
  tolerance like any other, and left uncalibrated if that does not help.
