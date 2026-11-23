- **`mascope_viz` draws a campaign's composition: a spectrogram, a period
  spectrum and a Van Krevelen.** Three tier-1 chart kinds register in
  `mascope_viz.charts.registry`. `spectrogram` is a heatmap of time by
  m/z on a fixed grid, 1 Th rows by default: a cell is the mean over its
  time bin's samples of the sum of the assigned peaks in its row, so a bin
  with more samples is not brighter for it. A bin without samples is drawn
  as a gap, and the colour runs on a log scale between the cells' 1st and
  99th percentiles. `period_spectrum` draws a period's mean spectrum at the
  ions' m/z, labelled with the neutral formulas. Against a second period it
  mirrors the two and draws their difference by sign, a peak seen in one
  period only marked. `van_krevelen` places the assigned formulas at their
  neutral O/C and H/C, sized by median signal, coloured by carbon number,
  class or amplitude; candidates are drawn hollow only when asked for.
  Each chart says what it was drawn from: the variable (peak area or
  height, or the normaliser), a change of engine run, and how often a
  stick's peak was seen. Its summary is plain JSON, with each period's
  tallest peaks, the increases and decreases apart, and the weighted and
  bulk mean ratios. The views gain `spectrogram` and `amplitude`;
  `spectrum` and `composition` give `n_samples` beside `n`, and
  `composition` each formula's tier. A heatmap panel takes `rules`, and
  sticks take a `hover` text.
