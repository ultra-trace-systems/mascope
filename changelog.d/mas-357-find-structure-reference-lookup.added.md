- **Find structure in *Edit compound*.** The dialog's *Structure* group has a
  *Find structure* action: it lists the compounds of the active reference
  lists that have the compound's formula and a structure, the ones with its
  CAS number first and then the ones with its name, each with its InChIKey,
  CAS number, list and licence. *Use* on a row fills the InChIKey, InChI and
  SMILES from it, and the CAS number when the row records one; *Save* keeps
  them, with the InChI checked against the formula as before. Picking from
  the compounds of the formula keeps a user from pasting another isomer's
  identifier; pasting stays possible. The same lookup is
  `GET /api/target/compounds/structures` and, in the SDK,
  `compounds.find_structures(formula, cas_number=, name=)`.
