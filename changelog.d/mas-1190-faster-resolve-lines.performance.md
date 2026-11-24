- **Assign batch spends less time predicting isotope lines.** Turning an
  ion's fine structure into the lines a file of its resolving power shows
  took half the CPU time of a pass. It now reads the resolving power once per
  m/z, groups each line with only the lines near it, and reads the lines of
  one member all at once, about twice as fast. The lines it gives are the
  same to the last bit, so nothing the engine decides changes.
