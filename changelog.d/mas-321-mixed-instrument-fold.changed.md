- **A batch of Orbitrap and TOF files keeps a batch-peak ledger per instrument type.**
  An Orbitrap's intensities are heights and a TOF's areas, so the fold no longer puts the
  two in one batch peak: each type's samples fold into batch peaks of their own. The
  *Batch peaks* ledger then offers **Orbitrap** or **TOF** and shows one at a time, and the
  batch chart draws only that type's selection. The ledger route and the SDK name each
  batch peak's `instrument_type`. A batch of one instrument type folds as before; an
  older mixed batch separates on *Rebuild batch ledger*.
