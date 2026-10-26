- **`mascope_tools` reads MS2 fragment peaks as formulas.** The new
  `mascope_tools.composition.ms2` package reads every peak of an MS2 spectrum
  as a sub-formula of every ion of its isolation window. It keeps a reading
  only when the neutral loss splits into at most three molecules - library
  ones, and at most one unnamed but plausible one - within max(5 ppm,
  0.3 mDa) of the recalibrated m/z. `read_fragments` ranks the readings of
  each peak and window ion, and `ladder_gate` admits a fourth loss molecule
  only when the same ion shows the step before it at some energy. The rules
  are peaky's, ported with peaky's known answers as tests, so both read one
  spectrum alike. Formulas are written with 15N as `[15N]`; peaky's `^N` is
  read too. `libraries/tools/docs/ms2.md` documents every rule and threshold.
