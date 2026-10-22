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
