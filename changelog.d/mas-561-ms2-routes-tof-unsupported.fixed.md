- **A TOF sample's MS2 reads answer instead of failing.** The MS2 centroids,
  fragment timeseries, group scans and MS1 centroids routes of a TOF sample
  used to fail with a server error. They now answer an empty result with
  `reason: "unsupported"`, as the MS2 summary already does: only raw
  Orbitrap files are read. The three MS2 reads also answer `source: null`.
  Each of the four responses now carries `reason`, null for a sample that
  is read.
