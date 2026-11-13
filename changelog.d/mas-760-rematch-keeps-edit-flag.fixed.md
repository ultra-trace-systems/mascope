- **A batch edited while its rematch runs keeps the edit's flag.** A rematch
  that finished wrote its own outcome over a flag set meanwhile, such as a
  target collection, target ion or compound edit, or a calibration apply, so
  the batch showed as up to date over matches that predated the edit. Now the
  edit's flag stays, and a recalibration the run still owes is kept over a
  rematch flag. A second rematch asked for while the first still runs is
  refused as locked, even after an edit took the batch's processing status,
  so the older run can no longer write its outcome over the newer one's.
