- **A raw Orbitrap file's first scan 1.5 to 5 times the rest of its
  experiment is now left out, not only one five times or more.** Thermo raw
  files often open on a scan whose whole spectrum reads a fixed factor above
  the scans that follow, and the factor is not one value: on one customer
  instrument's 92,600 acquisitions it ran from a few percent to 25 times, and
  the bound of five kept every first scan below it, plain spikes in the peak
  timeseries of their files at up to 4.98 times. The first scan is now also
  left out where, among its own experiment's scans (the same scan filter,
  method segment and scan event), it stands at least 1.5 times above both
  their median and the very next of them, and ten robust standard deviations
  above them. The spread keeps the first scan of a signal that moves, the next
  scan that of a signal opening on a plateau, and the floor of 1.5 times sits
  above the 5 to 40% excess some methods open every file with, where a lower
  bound would keep the first scan of one file and leave out the next. A first
  scan five times the median of the others is left out as before
  (`docs/user/how-it-works/peak-detection.md`, *The First Scan (Orbitrap)*).
  On that instrument the rule leaves out 4,669 first scans more than the 3,188
  it did; applied to each file's second scan as a control, it left out one
  of 92,620, a spike of its own.

  The peak store of a file whose first scan is now left out holds a scan its
  file no longer reads, and every read that divides its sums by the file's
  scans refuses it as stale, as a store of the 1.15.0 change was. So did, and
  still do, the stores of 403 files on that instrument built while the reader
  kept a first scan of five to 25 times the rest that it leaves out now, a
  spike in each of their samples. A re-processing refuses a file
  a person made a sample from, which every file copied into a workspace's
  batch is, and *Detect peaks* repairs one file at a time. The new
  `rebuild_stale_peak_stores` script rebuilds every stale store in place:
  each file's peaks are detected again by the decision and on the m/z
  calibration it has, every sample in every workspace keeps its definition,
  their TICs and the file's MS2 store are read again, and the batches holding
  its samples are set to rematch with their matches removed. It replaces
  `report_stale_peak_stores`, which listed the files for a re-processing
  (`docs/maintaining.md`, "Peak stores a file has outgrown").

  Operators: after the upgrade, run
  `DRY_RUN=1 mascope prod db script run rebuild_stale_peak_stores`, then
  without `DRY_RUN`, then rematch the batches it set to rematch. Cached
  averaged profiles move to a new generation (`...-g7.zarr`), since one
  averaged before can hold the first scan: every raw Orbitrap sample is
  averaged anew on its first view. Reclaim the space the old caches take with
  `python -m mascope_backend.db.admin.filestore delete-stale-sum-signal`.
  The MS2 store moves to version 4, since an older store's index can hold
  that first scan. Run `backfill_ms2_store` again (`docs/maintaining.md`,
  "The MS2 store"); until then those files' MS2 routes read the raw file.
