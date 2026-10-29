- **A batch's peak lists in one request, and a snapshot of them.**
  `GET /api/batch-peaks/batch/{sample_batch_id}/peaks` answers every peak of
  every sample of a batch as one Parquet table, each sample read as the
  per-sample peaks route reads it. It is open to the guest role and to API
  tokens. The SDK's `load_peaks` reads each batch this way: on the demo
  batches one request instead of 80 or 81, in about half the time. It reads
  a server older than the route one sample at a time, as before. The new
  `batch_peaks.peak_lists` is the read itself, and can keep the satellite
  peaks the per-sample read leaves out, flagged in `is_satellite`. With a
  sample filter, `load_peaks` names the chosen samples in the request, and
  the server reads only those. A batch of more than a hundred samples is
  read a hundred at a time, two requests at once, so no request takes the
  server long enough to pass the read timeout; a read that does time out is
  not sent again.
  `load_peaks(..., save_path=...)` writes the frame and its `attrs` as a
  Parquet file that `pd.read_parquet` reads back without a server.
