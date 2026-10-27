- **Mascope measures and serves the axis fingerprint of every raw Orbitrap
  file.** On some Orbitrap models, the Q Exactive Plus among them, each mass
  calibration leaves one m/z offset in each frequency band of the Orbitrap
  axis, which a lock mass cannot remove. As the file converter converts a
  raw Orbitrap file, it reads those offsets back from the stored profile,
  per acquisition mode and with the lock mass each scan was corrected with,
  and keeps the document in the file's `.props` for clients that correct the
  axis themselves. Mascope alters no m/z with it: its own calibration stays
  the one-factor scaling. It adds about a second to a conversion and never
  fails a file: a computation that fails is stored as an `error` document
  and logged as a warning. It needs the OpenTFRaw reader, 1.4.1 or later; a
  TofDaq file has none, and `axis_fingerprint = "off"` in the `[backend]`
  section of the config switches it off.

  `GET /api/sample/files/{id}/axis_fingerprint` serves the stored document,
  whatever its status, and `missing` for a file converted before it existed.
  `GET /api/samples/{id}/axis_fingerprint` adds what the sample reads of it:
  the scans its peak list is built from, the mode they belong to with its
  lock mass shares over those scans, and the calibration factors Mascope
  multiplies into the m/z it serves. Both take an API token, and the SDK
  reads them with `sample_files.get_axis_fingerprint(sample_file_id)` and
  `samples.get_axis_fingerprint(sample_id)`.

  The `backfill_axis_fingerprint` maintenance script
  (`mascope prod db script run backfill_axis_fingerprint`) computes the
  document for files that have none: those converted before the fingerprint
  existed or while the setting was `"off"`. It reopens the raw data kept
  beside each file, and also recomputes a document whose status is
  `unavailable` or `error`, or that another version of the algorithm or
  other parameters computed. Nothing is re-converted and no sample changes.
  A run is bounded (`AXF_LIMIT`) and resumable: a file it could not compute
  is passed over next time until `AXF_RETRY=1`, and `DRY_RUN=1` says what it
  would do. The reader pipeline notes
  (`libraries/thermo/docs/reader_pipeline.md`), the calibration page of the
  user guide and the SDK docs describe what is measured and how to read it.
