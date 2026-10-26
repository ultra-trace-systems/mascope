- **An Orbitrap file whose scans store centroids alone is imported with its
  peaks.** A method can store each scan's centroids without its profile. Such
  a file has no profile to average, so it used to be filed as a blank
  measurement, with no peaks to calibrate, match or assign; under the Thermo
  library its centroids stood in for a profile instead. Its peaks are now the
  instrument's centroids, averaged over its scans as every Orbitrap file's
  are, and its spectrum draws each one as a line. Its instrument function is
  read off the resolution its centroids report, with a Gaussian peak shape,
  since no peak shape can be fitted without a profile. Files converted
  before stay as they were (#1241).
