- **Samples carry the m/z range they read, and a copy says where it came
  from.** The sample listings (`GET /api/samples`, and the samples the SDK
  loads) gain `mz_min` and `mz_max`: the scan range of the scan stream a
  sample reads, or its raw file's m/z range where it reads none. A raw file's
  record gains the same two fields, the ends of its `range` as numbers. A
  sample copied into a batch from now on also carries `origin_key`, which
  says which part of the acquisition record it was copied from and never
  changes afterwards. These are what the sample finder and batches built from
  a filter will read. Copies made before have no key: the
  `backfill_origin_keys` script gives one to each whose window was not
  edited (docs/maintaining.md, "Origin keys of older copies").
