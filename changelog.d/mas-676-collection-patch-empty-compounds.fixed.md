- **A collection update that sends `sample_batch_ids` with an empty
  `target_compound_ids` is now refused, not run.** `PATCH
  /api/target/collections/{id}` refuses a request that changes a collection's
  compounds and its batches together, but it took an empty
  `target_compound_ids` for an absent one. The update then removed every
  compound from the collection and set its batches to rematch. Such a request
  now answers 422 and changes nothing. An empty `target_compounds_create`
  still creates nothing and is let through beside `sample_batch_ids`.
