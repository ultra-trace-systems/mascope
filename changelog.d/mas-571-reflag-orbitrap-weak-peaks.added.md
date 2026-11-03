- **Orbitrap peak stores written before the local floor can be flagged by it
  in place.** The maintenance script `reflag_orbitrap_weak_peaks` reads each
  raw Orbitrap file's peak store and flags weak the peaks under the floor of
  a dense stretch of their own spectrum, as a detection does now, with no raw
  file read and no new detection. It writes only the stores whose flag
  changes, reads a stitched store's overlaps again, removes the matches of
  the changed files' samples and sets their batches to rematch. It leaves
  each file's calibration as it was fit. Run it with `DRY_RUN=1`
  first: it lists how many peaks each file would turn weak and writes
  nothing. `docs/maintaining.md` has the details.
