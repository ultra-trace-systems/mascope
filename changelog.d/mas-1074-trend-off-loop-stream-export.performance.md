- **The calibration trend no longer holds up other requests while it is built.**
  The per-file trend parses its records and builds its answer in a worker
  thread, and **Export CSV** streams its table a few hundred files at a time
  instead of building it whole in memory: on 20,000 synthetic files the
  export's peak memory fell from about 520 MB to about 4 MB, and a light
  request sent to the same worker waited at most 0.08 s instead of 4.9 s.
  An export that fails part way ends with a line that says so, which the
  SDK's `read_export_csv` refuses, rather than look like a whole table.
