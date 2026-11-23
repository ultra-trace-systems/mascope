- **A refitted TOF file still says how far off it was acquired.** A TOF
  fit's calibration record now keeps the acquisition software's axis
  through every refit, and the time it was written. A refit used to replace
  the converter's coefficients and observe its lines on the axis the fit
  before set, so the calibration trend showed a refitted file 0 ppm off and
  the acquisition-drift badge forgot an offset below its 50 ppm limit. The
  trend now reads each line's offset on the acquisition axis, the same on a
  refit as on the first fit, places a calibrant collection change when the
  new version was first fitted rather than when its oldest file was
  acquired, and serves each line's offset beside its residual. A file
  fitted before this keeps what its record knew on its next refit: the axis
  read again from its TofDaq file, or its lines' offsets where that file is
  not in the filestore. The badge's drift is read from the same offsets,
  with its sign. No m/z moves.
