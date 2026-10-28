- **Editing an ionization mode makes its next version.** A change to its
  polarity, its mechanisms or either collection no longer rewrites the mode
  in place: it adds version 2, 3 and so on, and the samples already processed
  keep the version they were processed under, so their results still say
  which adduct panel and collections produced them. The rematch or
  re-calibration the edit flags their batches for moves them onto the new
  version, as does an assignment run or reprocessing their file; until then
  they read as before. A rename, a new filename token or a new instrument
  scope changes no result and is still made in place. The ionization
  settings show a mode's version once it has been edited, method bindings
  follow the new version, and the batch ledger keeps one set of anchors per
  mode across its versions. `GET /api/ionization/modes` lists the current
  versions, each with the ids of its earlier ones in `superseded_ids`; an
  earlier version is still read by its id, and cannot be edited or deleted
  on its own. A mechanism or a target collection an edit took off a mode can
  be deleted once no sample is left on a version that uses it; until then,
  the refusal names that version.
