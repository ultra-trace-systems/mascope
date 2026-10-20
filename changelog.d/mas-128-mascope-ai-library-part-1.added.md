- **The SDK lists and reads target collections.** `collections.list()`
  returns the client's workspace's collections and the global ones as a
  frame (`batch_id=` narrows it to the collections a batch is matched
  against, `all_workspaces=True` widens it to every workspace the user
  belongs to), and `collections.get(collection_id)` returns one collection
  with its compounds and batches. Until now collections surfaced only as the
  `target_collection_ids` and `target_collection_names` columns of peaks.
