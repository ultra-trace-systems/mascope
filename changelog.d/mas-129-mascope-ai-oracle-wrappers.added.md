- **`mascope_ai` scores candidates through Mascope and records what each
  answer rests on.** `query_by_mz` returns the formulas Mascope's mass query
  proposes for an m/z, the closest first. `match_compounds` has Mascope score
  candidate formulas in a sample. It condenses Mascope's compound to ion to
  isotopologue tree to one verdict per candidate: the compound score, the
  status (probable, possible or unmatched), each ion's score and the peaks
  attributed to it. The tree itself stays a `Frame`. A model's own
  suggestions are scored the same way, and a formula Mascope refuses or
  returns nothing for is listed as unscored, with the reason. Each session
  now keeps a `manifest.jsonl`. It records every call with the objects it
  touched, the versions and the server's provenance block. It also records
  each `answer()`, with its code and its values split into those Mascope
  returned, those the SDK computed client-side (a peak load's ion and
  compound scores), unscored candidates and the model's own statements.
  `derive()` keeps a table computed from Frames as a Frame whose sources are
  theirs.
