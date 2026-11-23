- **A TOF re-calibration matches against the mode's current collections.**
  `recalibrate_tof` now gives each acquisition batch of a re-calibrated
  file the calibrant and diagnostic collections of the current version of
  its ionization mode before it matches the file, as ingest does. A batch
  whose collections changed goes to `rematch` and is rematched in the same
  run. Before, a batch made under an earlier version of the mode kept that
  version's collections, and its samples were matched against them.
