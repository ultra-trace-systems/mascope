- **A sample file's scan table says which mass calibration it was acquired
  on.** `GET /api/sample/files/{id}/scans` adds `calibrations` to its
  `stats_per_file`: per scan stream, the median Conversion Parameters B and
  C, C/B² with its range, and the scans counted. Files whose C/B² agree to
  within 1e-11 share a calibration. Where the reader gives the instrument's
  status log, `calibration_status` adds the overdue mass calibrations the
  first and last scan's record names, with the time each was made.
