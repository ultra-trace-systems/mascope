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

- **Peak assignment holds an isotope line's height to the search's cut.**
  Stage A and the re-score of the search's readings take a peak for an ion's
  line when its height is within 40 % of the line's share, opened downward on
  a file with a measured response, the cut the untargeted search reads a
  candidate's lines by. Before, they held it to the match parameters' isotope
  ratio tolerance (0.2 on an Orbitrap), so a merged Orbitrap line that read
  low, such as a siloxane's silicon lines, left its ion and was read as a
  compound of its own. Target matching keeps the isotope ratio tolerance.
