- **An Orbitrap file averaged over many microscans no longer keeps the floor
  around its reagent ion as peaks.** Around a dominant ion, such a spectrum
  holds a floor of maxima, a centroid every few ppm over several Da, which
  the instrument reads far above its own noise; a file could keep tens of
  thousands of them. An Orbitrap peak is now also measured against the peaks
  of its own spectrum within 3000 ppm: where they are dense (300 or more), a
  peak under five times their median height is flagged weak. Files already
  processed keep their peak lists until they are processed again.
