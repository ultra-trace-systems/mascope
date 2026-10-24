- **The file converter stores the axis fingerprint of every raw Orbitrap
  file.** As it converts a file, it reads back from the stored profile the
  m/z offset that the file's mass calibration left in each frequency band of
  the Orbitrap axis, and keeps that document in the file's `.props` for
  clients that correct the axis themselves. Mascope alters no m/z with it.
  It adds about a second to a conversion and never fails a file: a
  computation that fails is stored as an `error` document and logged as a
  warning. `axis_fingerprint = "off"` in the `[backend]` section of the
  config switches it off, and a TofDaq file has none.
