- **The SDK sends a write once.** A timeout, a lost connection or a 502 to 504
  on a create, a save, a copy, a move, a delete or a batch run import now
  raises after one try instead of sending it again, since the server may
  already have acted: `matching.resave_batch` no longer rebuilds a batch's
  matches twice, and `collections.create` no longer makes a second collection.
  Reads, the matching previews and the updates that set fields to given
  values are still retried.
