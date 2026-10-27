- **`GET /api/samples/centroids` puts each scan of a stitched file on its own
  stream's calibration.** A file whose peaks are detected per scan stream is
  calibrated stream by stream, and its peaks carry each stream's factor. The
  route multiplied every scan's masses by the file's factor instead, so a
  stream's scans sat a few ppm off the sample's own peaks. Each scan now goes
  by the factor of the stream it is of; an MS2 scan, and a stream the
  calibration does not name, by the file's. Each scan also says the factor
  its masses are on, as `mz_calibration_factor`, null for a file never
  calibrated. Files calibrated as a whole read as before.
