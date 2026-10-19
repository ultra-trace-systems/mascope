- **An API token reads target collections, one dataset, one batch and the
  aggregate match preview.** `GET /api/target/collections` and
  `/api/target/collections/{id}`, the collections associated with a batch,
  `GET /api/sample/batches/{id}`, `GET /api/workspaces/{ws}/datasets/{id}` and
  the non-saving `POST /api/match/aggregate/sample/{id}` and `/batch/{id}` now
  answer an SDK token as well as a signed-in browser, with the same workspace
  permissions; before, a token got 401 there. They are the reads the AI
  toolkit's planned `find` and `describe` need. The set of routes that take a
  token is now pinned by a test, so a route gains or loses token access only
  on purpose.
