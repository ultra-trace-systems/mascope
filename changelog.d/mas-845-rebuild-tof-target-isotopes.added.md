- **A maintenance script rebuilds the stored TOF isotope lines of older target ions.**
  `rebuild_tof_target_isotopes` computes each target ion's TOF lines again and
  replaces the stored ones that differ, so an ion made before the current
  grouping is matched and calibrated on the lines a new ion gets. It removes
  the matches of the TOF samples in the batches those ions reach and sets
  those batches to rematch; Orbitrap samples and their lines are left as they
  are. `DRY_RUN=1` reports first, per collection, with each calibration
  mode's isotope hash before and after; run it on an instrument's calibration
  collections before its `recalibrate_tof` pass (#3131).
