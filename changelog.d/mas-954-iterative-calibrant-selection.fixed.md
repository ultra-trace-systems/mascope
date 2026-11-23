- **One misread calibrant line no longer drops a sound one beside it on a
  TOF file.** With more than five matched lines, the TOF calibration used to
  fit them all once and drop every line outside the 15 ppm tolerance. A
  misread line could pull that fit far enough to push its neighbour out too.
  The fit now drops the worst line, refits, and repeats until every line kept
  is within the tolerance. The calibration step's version is now 4, so the
  TOF recalibration refits files fitted by an earlier version (#3281).
