- **An MS2 group's fragments as formulas, over the ions you name.**
  `GET /api/samples/{id}/ms2/readings?group=<key>&ions=<formula>,...` reads
  every peak of a group's spectra as a sub-formula of each named window ion:
  its formula, the neutral loss and the molecules it splits into, the ppm, the
  rank among a peak's readings and how many window ions can explain it, or
  why it has none (`unexplained`, a `harmonic` ghost, a side lobe
  `satellite`). The group's window is read whole, every collision energy of
  its precursor together, and recalibrated once on its target's located
  precursor. Window ions are named, never guessed: each must lie in the
  isolation window and one of them at the group's key, and one that does not
  is refused with the key it belongs to. Of two named ions too close to tell
  apart, only the first is read, and the other is named as left out. The
  readings are cached in the file's MS2 store under the composition core's
  version (`core_version`), and `cached` says when an answer came from it.
