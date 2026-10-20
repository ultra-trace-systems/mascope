- **The SDK can change data, not only read it.** Write methods over the
  routes an API token may change: `mascope.datasets` create, update, move
  and delete; `mascope.batches` update, copy and delete; `mascope.samples`
  update, move and copy; the new `mascope.collections` (create, update,
  associate with batches, delete) and `mascope.compounds` (add, update,
  remove); and `mascope.matching` runs a sample's or a batch's matching
  without saving it, saves it, or rebuilds a batch's saved matches. Each is
  one call to one route, which checks the fields and the role as it does for
  the web app. A copy, a move and a batch delete run in the background and
  return the task's `process_id`; a write clears the client's cache. The
  SDK is version 2026.10.20.
