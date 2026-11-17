- **A TOF instrument's calibration trend shows its calibrants over time.**
  In the **Raw files** tab, **Calibration trend** beside the instrument
  selector draws what a TOF instrument's calibration records say, file by
  file over 30, 90 or 365 days of one polarity: how far each axis was off
  before the fit, the fit's mean residual, how many calibrant lines it kept,
  and each line's residual, kept or left out. A dashed line marks each change
  of the calibrant collection: a new version of one, or another taking over.
  On a busy instrument each panel draws the median per hour or day over the
  middle half of the files, and a window of more than 5,000 files is summed
  up in the database, so a year of one is shown whole.
  **Export CSV** downloads it as a table, one row per line of each file.
  `GET /api/calibration/trend` and `GET /api/calibration/trend/export` serve
  both, for a guest of the instrument's acquisition workspace.
