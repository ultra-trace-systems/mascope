- **The TOF calibration audit no longer reports a family offset that reads nothing.**
  `audit_tof_calibration` now narrows the family window to half the spacing between CcHhOo
  formulas where that is under 30 ppm, so an offset that would alias onto the next formula is
  not counted. A band whose offsets fill the window reads "no reading: flat" instead of a
  median. Two new settings tune it: `TOF_AUDIT_FAMILY_WINDOW_SPACING` and
  `TOF_AUDIT_FAMILY_PEAKED_IQR`.
