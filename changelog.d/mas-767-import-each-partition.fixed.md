- **An engine's batch result reaches both instrument types of a mixed batch.**
  A batch that holds Orbitrap and TOF files keeps a batch peak of each type for
  one species, and an import landed each row on only the nearer of the two: the
  other type's batch peak kept no annotation and no engine verdict. A row now
  lands on the nearest batch peak of each instrument type, each measured on its
  own type's samples, and is skipped only when it landed on neither. The run's
  summary adds `rows_landed` and `anchors_matched_by_instrument_type`. A batch
  of one instrument type imports as before. (#3049)
