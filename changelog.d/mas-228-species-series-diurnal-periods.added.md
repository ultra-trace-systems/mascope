- **The species charts: `species_series`, `diurnal` and `period_boxes`.**
  Three more `mascope_viz` chart kinds. Each turns a campaign store into a
  Figure with its data frame and a summary a caption may cite.
  `species_series` draws species over time, as small multiples or
  overlaid. `diurnal` draws the hour-of-day cycle, optionally per month,
  season or weekday. `period_boxes` draws a box per day, week or month.
  All three take species by key, name, formula or `top:N`, and draw
  normalised values by the TIC, a reagent ion or the sum of a reagent's
  ions. They label the y axis as the signal the server gives and mark a
  change of engine run. `top:N` draws an ion two species read once, and
  names the other.
  The `diurnal` and `periods` views now also give `n_samples`.
