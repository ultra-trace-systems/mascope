- **A scope with a time range resolves without reading the batches outside
  it.** The batch listing (`GET /api/sample/batches`, `batches.list` in the
  SDK) now gives each batch `first_sample_datetime_utc` and
  `last_sample_datetime_utc`, the earliest and latest start of its samples'
  files, and `sample_instruments`, those files' instruments. `mascope_viz`'s
  `resolve`, and so every `sync` of the campaign store, lists the samples
  only of the batches whose span reaches the range, where it listed those of
  every batch of the dataset. A batch with no span, and every batch on an
  older server, is read as before.
