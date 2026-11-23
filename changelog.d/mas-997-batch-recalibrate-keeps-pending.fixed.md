- **A batch Recalibrate no longer drops a recalibration it did not do.**
  Recalibrating a batch marked every other batch sharing one of its files
  for rematching, over a recalibration that batch still owed for its other
  files. A recalibration that failed for some file put the batch at rematch
  too. A refresh then cleared either batch to ready with its files still on
  the old calibration. Both now stay marked for recalibration.
