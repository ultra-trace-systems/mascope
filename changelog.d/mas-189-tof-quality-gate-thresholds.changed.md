- **A TOF calibration is held to a tighter quality bar, judged by its
  model.** A TOF fit is now flagged below the bar when its mean m/z error
  after calibration is above 7 ppm, where the limit was 10 ppm. It must also
  keep one calibrant ion more than its model has parameters: three for the
  square-root model and four for the three-parameter one, where any two
  ions were enough. The limits come from the fleet's TOF baseline. The bar
  stays warn-only unless a server sets `calibration_quality_gate` to
  `enforce`.
