- **Run history no longer opens a run with no ledger to show.** Picking a
  failed, cancelled or running batch run showed an empty Batch peaks ledger
  as that run's, which read as "that run left nothing". Such a run is still
  listed, but cannot be opened, and its row says why: a failed run left the
  ledger as the run before it, a cancelled run's writes stand in the current
  ledger, and a running run has not finished.
