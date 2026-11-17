- **Every assigned peak says what you may state of it: its identification
  level.** Beside the tier, each committed row of an assignment run now
  carries a level in the scale of Schymanski et al. (2014), shared with the
  reference engine: 5b where the reading is refuted by its isotopes,
  implausible or untestable, 5a where a rival on the peak's shortlist was not
  excluded, 4b where the ion's formula holds but its split into molecule and
  adduct is open, and 4a where the neutral formula holds. Each row says what
  earned its level and what would lift it, and lists each rival with the test
  that put it out, by mass, by isotopes or by a second channel, or as open.
  The peak inspector shows it as a chip beside the tier; both ledgers gain a
  **Level** column and filter; the ledger export, the SDK's ledger frames
  (`peak_assignments.get(level=...)`) and the API carry `level`,
  `level_evidence` and `level_lift`; and a batch import can carry the other
  engine's `engine_level`, `engine_evidence` and `engine_lift`. The level never moves a tier. Runs made before it
  show none.
