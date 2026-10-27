- **A sample's axis fingerprint lists its scan streams, each with its scans
  and its calibration factor.** `GET /api/samples/{id}/axis_fingerprint`
  (and the SDK's `samples.get_axis_fingerprint`) adds `item.streams`: the
  sample's own stream, or each stream that owns some m/z of a stitched
  sample. A stitched sample's peaks are on the factor of their own stream,
  not on the one `item.mz_calibration_factor` gives, which is the file's; a
  client joins a peak to its factor by the stream its peak listing names.
  `streams` is null for a sample over every MS1 scan of its polarity, whose
  peaks are all on the file's factor.
