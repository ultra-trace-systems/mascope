- **`mascope_viz`'s `series` view bins hourly and daily series from the
  store's rollups.** At `cadence="1h"` in UTC or a zone a whole number of
  hours from it, and at `1D` in UTC, the view reads the hourly and daily
  rollups the store computes at sync instead of every sample's values:
  about six times faster on a synthetic month, and a year no longer reads
  a year of rows. The frame is the same either way. Where the rollups would
  give another answer (normalisation, another statistic or band, a window
  that cuts a bin, a formula two batch peaks carry, two engine readings in
  one bin, a sync that stopped before its rollups), the view bins the rows
  as before. The frame's `attrs["source"]` says which (`rollups` or
  `rows`).
