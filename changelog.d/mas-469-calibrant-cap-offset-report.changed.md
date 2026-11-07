- **The Orbitrap m/z calibration leaves out its brightest calibrants, and
  reports the offset its calibrants carry.** A calibrant line carrying more
  than 28 % of the ion current reads 0.6 to 4 ppm above the weaker lines of
  its file on the assignment gate's Orbitrap sets, and a fit that is the
  median of its lines lands between them. Such a line is now left out of the
  fit; on a file calibrated range by range, by its share of its own range's
  ion current. Two new optional fit parameters set the cap,
  `calibrant_tic_share_max` (28 % on an Orbitrap by default, off on a TOF)
  and `peak_intensity_max` (off), and a line over either is left out. A cap
  that would leave too few lines for a fit is set aside, and the fit made on
  every line as before. The fit's quality block records the cap, how many
  lines it left out and whether it was set aside (`calibrant_cap`), and an
  Orbitrap fit's block also reports, without applying it, the offset in mDa
  beside the factor over the calibrants it kept (`offset`), or why the
  calibrants span too little to tell the two apart.
  A new fit moves only a file whose calibrants include such a line; a file
  already calibrated keeps its axis until it is fitted again
  (`docs/user/how-it-works/calibration.md`, *The brightest calibrants are
  left out*).
