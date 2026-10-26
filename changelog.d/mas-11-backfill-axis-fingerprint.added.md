- **A maintenance script computes the axis fingerprint of Orbitrap files that
  have none.** `mascope prod db script run backfill_axis_fingerprint` reopens
  the raw data kept beside each converted Orbitrap file and stores its
  fingerprint document, for files converted before the fingerprint existed or
  while `[backend] axis_fingerprint` was `"off"`. It also recomputes a document
  whose status is `unavailable` or `error`, or that another version of the
  algorithm or other parameters computed. Nothing is re-converted and no
  sample changes. A run is bounded (`AXF_LIMIT`) and resumable: a file it
  could not compute is passed over next time until `AXF_RETRY=1`, and
  `DRY_RUN=1` says what it would do. It runs only under the OpenTFRaw reader,
  1.4.1 or later.
