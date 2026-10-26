- **The deposit package no longer publishes the instrument computer.**
  `mascope export package` leaves the `machine` block, the host name and
  Windows machine GUID, out of each `raw/<file>.mascope.json`; the database
  keeps the record whole. The package README and the user guide also say that
  raw file headers are copied unchanged and may name the instrument.
