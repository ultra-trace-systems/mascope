- **The TOF calibration audit says why it could not read a file.**
  `audit_tof_calibration` counted each file it could not audit by the
  exception's type alone, so a mode whose files were all stored as blanks
  read only as a column of `ValueError`. It now counts and lists them by
  cause: stored as a blank (an empty peak store and no instrument
  functions), no peak store, no instrument functions, or instrument
  functions it could not read. `TOF_AUDIT_JSON=1` lists each such file with
  its cause under `unreadable`.
