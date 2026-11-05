- **`mascope_viz` turns a campaign store into the frames charts draw.**
  `mascope_viz.data.views` has seven views over a synced store: `series`
  (each key over time at a cadence, with a percentile band), `diurnal`
  (the hour-of-day cycle, split by month, season or weekday if asked),
  `periods` (the distribution per day, week or month), `coverage` (files
  and samples per hour by polarity and mode, the gaps, the files no batch
  holds), `composition` (the assigned formulas per period, or their class
  budget), `spectrum` (a period's average spectrum, and its difference to
  another) and `top` (the brightest keys, the biggest changers, the new
  ones). The auto cadence bins at the samples' median spacing rounded up
  to whole minutes, so no bin falls empty between samples. A gap or a
  value below detection stays NaN. Values can be divided by the TIC or by
  a reagent ion, sample by sample. Times are in the instrument's zone, and
  each frame names its zone, cadence, normalisation and any change of
  engine run in its `attrs`.
