- **A batch peak whose samples split between two formulas along intensity or
  acquisition time is marked a probable blend.** Two compounds sharing one m/z
  part along an axis: one holds the bright samples or the early ones. After
  every consensus vote, the two leading formulas are tested, intensity by a
  Mann-Whitney rank test and acquisition time by an exact runs test, over the
  samples reading each at candidate or better; two formulas reading one ion are
  never a blend. A probable blend carries an intersect mark beside its formula
  in the *Batch peaks* ledger, whose tooltip names both formulas and says how
  they part, and **Probable blends** in the ledger's view menu keeps only
  those. The batch chart names such a trace by both formulas. Shown, never
  weighed: the consensus and its tier are unchanged, and splitting a blend into
  two traces is not built yet. The ledger's records and the SDK's
  `batch_peaks.list` carry the reason as `blend`.
