- **`mascope-viz` flags a run change only where the drawn formulas join
  two readings.** A view's `mixed_runs` and `run_changes` now follow the
  batches of the window's samples, kept by their middle time, in the
  polarity the formulas are read in. A change of reading between the other
  polarity's batches no longer warns on a chart, and a window that starts
  inside a batch's last file now sees that batch's reading. A store's
  `status().mixed_runs` follows the polarities the same way, a `+-` batch
  counting in both, and `mascope-viz store status` says "one reading per
  polarity" when each polarity is read by one run
  (`libraries/viz/README.md`).
