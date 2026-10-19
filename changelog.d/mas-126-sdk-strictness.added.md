- **SDK loads say whether they are whole.** A load that could not read every
  sample still returns what it read, and now says so on the frame:
  `df.attrs["complete"]` is False and `df.attrs["failures"]` names each
  sample (or stage, or peak) whose read failed, with the error. That holds
  for `load_peaks`, `load_assignments`, `load_peaks_by_stage`,
  `load_peak_timeseries` and `samples.list(batches=...)`; when every read
  failed they return an empty frame carrying the failures instead of None,
  and `mascope.concat` keeps the failures of every load it combines. The
  rows and the failures come in the order the samples were listed, the same
  on every run, rather than in the order the reads finished. The log warning
  a notebook shows is unchanged.
