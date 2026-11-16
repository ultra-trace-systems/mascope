- **A batch can place its targets' batch peaks at the target ions' m/z.**
  With the batch's new `seed_batch_peaks` setting on, a sample's peak that
  starts a new batch peak, and that its assignment matched to a target ion of
  the batch's collections as a candidate or better, starts it at the ion's monoisotopic m/z rather than
  where the sample measured it, so the target's trace sits at the ion's m/z.
  Such batch peaks are marked `seeded` in the ledger. A target no sample's
  peak was matched to gets no batch peak. Off by default; turning it on for a
  batch whose ledger exists moves no batch peak already there.
