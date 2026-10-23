- **The assignment mass gate also calibrates on the source's own lines.** A run
  measures its mass calibration over its corroborated commits and needs eight of
  them to fit one. The reagent pre-pass claims the source's ions before either
  stage sees them, and on a charge-transfer source those ions are the mode's
  calibration compounds: on a certified-cylinder set four of seven files kept
  seven commits and ran with no calibration at all. The fit now pools the
  monoisotopic lines the pre-pass claimed (the reagent's ladder, the air's ions
  and the calibrant beam) with the commits, in a run and in the ingest fold
  alike. The centre's mass trend is still drawn over the commits alone.
  `config.mass_calibration` counts every point the fit used in `anchors` and
  the source's lines among them in `reagent_anchors`. A source's brightest lines
  can read above its analytes' axis, so on such a sample the constant centre
  moves toward them.
