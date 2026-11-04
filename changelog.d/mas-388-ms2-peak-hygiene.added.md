- **MS2 readings flag harmonic ghosts and side lobes.** Two kinds of MS2
  centroid are no ions and read as sub-formulas of almost anything: an
  Orbitrap harmonic ghost of a strong line, near m/z / 4 or m/z / 9 of it, and
  a side lobe a few line widths beside a much brighter line.
  `mascope_tools.composition.ms2` flags both, per peak, with `flag_harmonics`
  and `flag_satellites`, and never deletes one. `read_window` reads one
  isolation window's spectra and makes each flagged peak one row with no
  formula, its `role` `harmonic` or `satellite`: in no fragment and no ladder
  step, and kept out of the TIC and the peak count by `spectrum_counts`, which
  counts it apart. Every reading row now carries its `role`. `keep=True` reads
  every peak, for debugging. The rules are peaky's, ported with its known
  answers as tests; `libraries/tools/docs/ms2.md` documents them.
