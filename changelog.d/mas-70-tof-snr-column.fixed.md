- **A TOF peak's signal-to-noise follows its height.** The noise a TOF
  peak's `signal_to_noise` divides by was the standard deviation of the
  summed spectrum between one and ten peak widths of it, which is the peak's
  own wings and its neighbours at the same unit mass: the noise grew with
  the heights around the peak, a strong peak beside a stronger one read
  near zero, and over a file's peak list the column did not track height.
  The noise is now the spread (MAD) of the baseline within half a unit of
  mass, with every fitted peak's core cut out. On synthetic TOF spectra
  the column's rank correlation with height rises from about 0.7 to 0.92
  or above. The peak-assignment engine and the v2 match score read the
  column, and so does TOF m/z calibration, which keeps a calibrant
  candidate at a signal-to-noise of 10 or more: that gate now means ten
  times the baseline's noise. Each calibrant still anchors on the most
  intense candidate within its window, so the gate no longer drops a strong
  calibrant beside a stronger peak, and it lets through weaker but clearly
  detected peaks it used to drop. A TOF file's stored peak list keeps the
  old values until its peaks are detected again. Orbitrap files are
  unchanged: a raw file's signal-to-noise is the instrument's own, and one
  without its raw file keeps the earlier estimate.
