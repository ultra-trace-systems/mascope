- **A batch's peak lists in one request, and a snapshot of them.**
  `GET /api/batch-peaks/batch/{sample_batch_id}/peaks` answers every peak of
  every sample of a batch as one Parquet table, each sample read as the
  per-sample peaks route reads it. It is open to the guest role and to API
  tokens. The SDK's `load_peaks` reads each batch this way: on the demo
  batches one request instead of 80 or 81, in about half the time. It reads
  a server older than the route one sample at a time, as before. The new
  `batch_peaks.peak_lists` is the read itself, and can keep the satellite
  peaks the per-sample read leaves out, flagged in `is_satellite`.
  `load_peaks(..., save_path=...)` writes the frame and its `attrs` as a
  Parquet file that `pd.read_parquet` reads back without a server.
