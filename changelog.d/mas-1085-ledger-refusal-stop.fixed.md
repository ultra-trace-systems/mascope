- **The TOF re-calibration runner says how to start again after another run.**
  When another run holds the ledger, `recalibrate_tof`'s refusal now says to
  remove `STOP` once that run has ended and to start again with
  `--skip-backup`; when a rollback holds it, it says to wait for the rollback,
  which `STOP` does not end. A run started while `STOP` is still there warns
  that it will refit nothing.
