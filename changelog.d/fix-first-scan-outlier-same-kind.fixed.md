- **An ordinary first scan of a raw Orbitrap file with MS2 scans, or one
  that switches polarity, is no longer left out as a TIC outlier.** The
  reader drops a file's first scan when its TIC is five times the median of
  the others, a guard against a Thermo quirk. Read without a scan stream, the
  others were every other scan of the file. MS2 scans carry 100 to 10,000
  times less ion current than MS1 scans, and in most MS2 files they
  outnumber them, so the first MS1 scan read as an outlier and was missing
  from the file's spectrum, its TIC and its peak timeseries. The same
  happened where one polarity's scans are much weaker than the other's. The
  first scan is now compared with the other scans of its own MS order and
  polarity, as a stream's first scan already was with its stream's. Only
  a selection that holds the first scan logs its exclusion, so
  `backfill_ms2_store` no longer prints the line six times for nearly every
  file it writes.

  The peak stores of the files whose first scan comes back no longer match
  the scans their file reads: opening such a sample or matching it queues
  its peak detection, and re-processing the files does the same at once. A
  single-polarity file without MS2 scans reads as before. The MS2 store
  moves to version 2, since an older store's index can lack that first scan:
  until `backfill_ms2_store` runs again (`docs/maintaining.md`, "The MS2
  store"), those files' MS2 routes read the raw file.
