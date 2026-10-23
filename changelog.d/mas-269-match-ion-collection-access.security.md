- **Match ion and isotope records check the target collection they are asked
  for.** `POST /api/match/records/ion`, `POST /api/match/records/ion/series`
  and `GET /api/match/records/isotope` checked the caller's access to the
  samples or batch and nothing else, so a guest of any batch could name a
  target collection of another workspace and read the names and formulas of
  its compounds and ions. A named collection now needs the guest role in its
  workspace, as `GET /api/target/collections/{id}` does, and each named target
  ion must be in a global collection or one of a workspace the caller is a
  member of. Either is refused with 403 otherwise.
