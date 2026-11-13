- **A batch ledger can seed its batch peaks from the batch's target ions.**
  With the batch's new `seed_batch_peaks` setting on, the first sample to
  fold in each ionization mode and instrument type first puts a batch peak
  at every target ion's monoisotopic m/z, so a target's trace sits at the
  ion's m/z rather than where the first sample measured it. Seeded batch
  peaks are marked `seeded` in the ledger. Off by default; a batch whose
  ledger exists is seeded by *Rebuild batch ledger* only when asked
  (`reseed=true`), and no batch peak already there moves.
