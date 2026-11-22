- **TOF files in a mode without calibrants take their neighbours'
  calibration.** On a multi-scheme TOF, a file whose ionization mode has no
  calibration collection, such as an ambient or a zero mode, used to keep
  the acquisition software's axis and go unmatched. It now takes the
  calibration of the nearest fitted files of the same instrument and
  polarity within 30 minutes, the one before it and the one after it
  interpolated in time, and is matched on it. Its record says
  `status: "transferred"` with the source files and the time gap, and the
  badge and the calibration dialog say so. A file with no fitted neighbour
  that close stays unfitted, and a fit of the file itself replaces a
  transfer.
