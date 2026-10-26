- **API clients read the axis fingerprint of a raw Orbitrap file, and of a
  sample.** `GET /api/sample/files/{id}/axis_fingerprint` serves the document
  the converter stored, whatever its status, and `missing` for a file
  converted before it existed. `GET /api/samples/{id}/axis_fingerprint` adds
  what the sample reads of it: the scans its peak list is built from, the
  mode they belong to with its lock mass shares over those scans, and the
  calibration factors Mascope multiplies into the m/z it serves. Both take an
  API token, and the SDK reads them with
  `sample_files.get_axis_fingerprint(sample_file_id)` and
  `samples.get_axis_fingerprint(sample_id)`.
