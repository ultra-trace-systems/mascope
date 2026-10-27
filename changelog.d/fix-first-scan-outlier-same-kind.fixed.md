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
  file it writes. A single-polarity file without MS2 scans reads as before.

  The peaks stored for a file whose first scan comes back were summed
  without it, so they no longer match the scans the file reads. Every read
  that divides those sums by the file's scans refuses such a store as
  stale, so nothing averages low unnoticed: listing the file's peaks or
  aligning a batch that holds it is refused, with a message asking for its
  peak detection. Matching and the sample's spectrum read the store
  otherwise and queue nothing, and a sample already matched is not read
  again, so nothing else repairs these files. Re-processing does: a
  re-processed file that has outgrown its pooled store has its peaks
  detected again before it is calibrated and matched. After the upgrade,
  list the files with
  `mascope prod db script run report_stale_peak_stores` and re-process
  them (*Re-process* in Raw files, 25 at a time); a file with samples a
  person made is refused by a re-processing, and *Detect peaks* rebuilds it
  instead (`docs/maintaining.md`, "Peak stores a file has outgrown").

  Operators: cached averaged profiles move to a new generation
  (`...-g6.zarr`), since one averaged before can lack that first scan. Every
  raw Orbitrap sample is averaged anew on its first view, single-polarity
  files to the profile they had. Reclaim the space the old caches take with
  `python -m mascope_backend.db.admin.filestore delete-stale-sum-signal`.
  The MS2 store moves to version 2, since an older store's index can lack
  the first scan too. Run `backfill_ms2_store` again (`docs/maintaining.md`,
  "The MS2 store"); until then those files' MS2 routes read the raw file.
