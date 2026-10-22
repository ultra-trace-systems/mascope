- **The batch's *Search untargeted* no longer reads an isotope line as a
  compound of its own.** A compound's 13C line is often brightest in another
  sample than the compound itself, and each batch peak is searched in its
  brightest sample. There the compound is not searched, so the line was read
  as a formula of its own, and whether the batch peak kept that or the line
  reading depended only on the order the samples were searched in. The line
  reading now wins, as it does within one sample, the other reading is
  dropped and that sample is searched again without the peak. On a 12-sample
  Orbitrap batch 134 batch peaks had both readings. A batch searched before
  keeps its readings until its ledger is rebuilt and searched again.

- **A composition measured on its own is scored at the instrument's width.**
  A batch peak's *Use this*, the inspector's measured evidence and its
  alternatives measure a handful of formulas against a sample, too few lines
  to fit the sample's own mass width from. They were scored at a generic 2 ppm,
  several times an Orbitrap's, so a formula well off the peak still read
  as a good fit there while a search of the same sample read it as a poor one.
  They are now scored at the instrument class's width, as an assignment run
  scores a sample whose library fits no width.
