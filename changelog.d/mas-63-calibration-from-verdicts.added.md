- **The confidence curve can be refit from one reagent profile's verdicts.**
  `POST /api/peak-assignments/calibration/{instrument}/recalibrate` takes
  `profile=`, records on the curve the profile its verdicts share, and refits
  the curve's adduct corroboration weights from them, each a smoothed
  likelihood ratio. A run under another profile still reads the curve, with
  a warning, and records `profile_mismatch`. The answer adds the profile,
  the verdicts on rows the formula search assigned, and the weights refit.

- **`bench review` turns the engines' disagreements into verdicts.** It
  writes the in-app readings on the peaks where the reference's tier and
  this server's differ to a sheet for a person to judge, and
  `bench review --record` posts the verdicts and can refit the curve of
  each instrument class they hold.
