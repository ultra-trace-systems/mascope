- **A file's MS2 scans are read once, when it is converted, and kept.** The
  converter writes an MS2 store beside the peak store (`ms2.zarr`) for a raw
  Orbitrap file whose scan census holds fragmentation scans, an MS2-only part
  included: each (polarity, precursor, activation) group's scans, their
  records and centroids, and the group's averaged spectrum. The MS2 summary,
  centroid and timeseries routes answer from it instead of opening the raw
  file on every request, with the same numbers, and each response says
  `source: "store"` or `"raw"`. A file converted before this, or whose store
  could not be written, is read from the raw file as before, and
  re-processing a file writes its store again. The store's groups are made at
  0.001 Da: a request at another `parent_peak_tolerance` is answered from
  them, and the response gives the tolerance its groups were made at. A
  sample whose time window holds only some of a group's scans has its
  averaged spectra read from the raw file.

- **The scans behind an MS2 spectrum can be read.**
  `GET /api/samples/{id}/ms2/scans?group=137.0960@hcd25.00` returns each scan
  of one (precursor, activation) group: its time, precursor, activation,
  collision energies, isolation width, scan range, injection time and TIC,
  where each was read from, and its centroids at or above `noise_threshold`.
  A group is keyed by its parent m/z with four decimals, `@` and its
  activation.
