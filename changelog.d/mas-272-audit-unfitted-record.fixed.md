- **The TOF calibration audit no longer scores a file converter's own
  calibration as the stored fit.** `audit_tof_calibration` read every stored
  record as a fit at the peak store's TOF index, but the converter's record
  counts samples from the start of the raw file's own axis. On a mode without
  calibrants, where every file carries one, its `stored` errors ran to
  hundreds of thousands of ppm. Such files are now counted as `unfitted`, per
  mode and per file, and have no `stored` fit.
