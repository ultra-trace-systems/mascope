- **A sample file's peak read gives every peak its height and area.**
  `GET /api/sample/files/{id}/peaks` averaged each peak's per-scan
  timeseries, which is computed only once something asks for it, so every
  other peak of a raw file came back `null` - on routine files most of them,
  the strongest ions included, and more or fewer from one day to the next.
  It now reads each peak's stored sums, as `GET /api/samples/{id}/peaks`
  does, and gives the same values: a peak averaged over its own polarity's
  scans in a file that holds both. A file with no source data lists the
  peaks it used to leave out. The batch peak CSV export, which summed the
  same timeseries and wrote 0 for those peaks, writes their stored sums.
