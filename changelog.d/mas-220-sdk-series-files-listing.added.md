- **The SDK reads a batch's series in one call, and lists files by time.**
  `batches.ion_series(batch_id)` returns each sample's matched intensity of
  each target ion of the batch's collections (or of `collection=` or
  `ions=`), and `batch_peaks.series(batch_id)` each sample's intensity of
  each batch peak, with the run it was read from on `attrs["run_id"]`: a
  request or two per batch where `load_peaks` makes one per sample.
  `sample_files.list(datetime_min=, datetime_max=, instrument=)` lists every
  file acquired in a time range, in a batch or not, paging through the whole
  range. All three return long frames stamped with the server's provenance,
  as the loaders' are.
