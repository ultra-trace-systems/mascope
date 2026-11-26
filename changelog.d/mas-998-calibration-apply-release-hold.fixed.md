- **A calibration apply that fails no longer leaves its batches locked.**
  When applying an m/z fit to a file raised, every batch with a sample of
  that file stayed at `processing` until the backend restarted, and a batch
  waiting on a recalibration then lost that wait. The batches now go back to
  `recalibrate` or `rematch` at once.
