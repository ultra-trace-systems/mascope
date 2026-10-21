- **A target compound can say which structure it is.** Compounds take an
  optional InChIKey, InChI and SMILES, accepted by the compound routes and
  the SDK. In the app they are set in a *Structure* group with the CAS
  number, in *Edit compound* and in the collection dialog's *Create new*, and
  a spreadsheet paste takes them as three more columns. A pasted structure is
  not applied to a compound that already exists, and its row says so.
  The structure says what the target stands for, and a match on its formula
  does not confirm it. An InChI must describe the compound's formula, or the
  save is refused; a formula change makes the compound another substance and
  clears all three, unless the update gives them again. The collection dialog
  finds compounds by InChIKey, and the *Match compounds* sheet of the batch
  spreadsheet export appends CAS number and InChIKey columns. For compounds
  stored before, the `backfill_target_compound_identity` maintenance script
  fills the identity from the active reference chemistry, where a compound
  with no identity yet has a CAS number and formula that match exactly one
  InChIKey; run it with `DRY_RUN=1` first.
