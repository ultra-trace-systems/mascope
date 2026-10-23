- **Centroid flags and scan numbers in the per-scan centroids.** Each scan of
  `GET /api/samples/centroids` and of `centroids_meta` in
  `GET /api/sample/files/{id}/metadata` now carries its scan number (`scan`),
  and each centroid its `flags`: the instrument's marks on it, as Thermo's
  bit values, 2 fragmented, 4 merged, 8 exception (a weak side-lobe peak) and
  16 reference (the lock-mass peak the scan was corrected with). A client can
  find a scan's lock mass by its flag rather than by its m/z, and join scans
  by number rather than by time, which a trailing scan at retention time 0.0
  defeated. Both routes take `exclude=reference,exception` (any of the four
  names) to leave such centroids out; nothing is left out by default, so the
  responses only gain the two keys. The SDK's `samples.get_centroids` takes
  `exclude` too. Both reader backends report the same flags. Averaged
  centroids and peak detection are unchanged.
