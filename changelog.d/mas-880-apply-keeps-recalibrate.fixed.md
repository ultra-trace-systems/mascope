- **Applying an m/z calibration to one file no longer drops its batch's
  pending recalibration.** The apply set every batch it touched to rematch,
  so a batch waiting on a recalibration lost the flag, and a refresh then
  cleared it without its other files being recalibrated. Such a batch now
  stays at recalibrate, and so does one a forced rematch flagged for a
  recalibration while the apply ran.
