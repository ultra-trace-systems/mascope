- **Peak assignment reads one formula's isotope lines the same way
  everywhere.** Stage A, the re-score of the search's readings, the
  composition fit and every isotope-line check now predict a target ion's
  lines at the file's own resolving power, as the untargeted search predicts
  a candidate's. Before, Stage A read a TOF file's lines merged at a fixed
  resolving power of 10,000 and an Orbitrap file's lines one isotopologue at a
  time, so one reading could be ranked on one envelope and tiered on another.
  A list compound's own isotope lines now count against a rival where their
  mass error follows the compound's within three mass widths, the bar the mass
  gate reads an isotopologue at, and at the expected signal-to-noise the run
  counts an absent line at. A reagent ion's isotope line takes the nearest peak
  in its window rather than the brightest.
