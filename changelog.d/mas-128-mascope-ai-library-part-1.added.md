- **The SDK lists and reads target collections.** `collections.list()`
  returns the client's workspace's collections and the global ones as a
  frame (`batch_id=` narrows it to the collections a batch is matched
  against, `all_workspaces=True` widens it to every workspace the user
  belongs to), and `collections.get(collection_id)` returns one collection
  with its compounds and batches. Until now collections surfaced only as the
  `target_collection_ids` and `target_collection_names` columns of peaks.

- **`mascope_ai`, a toolkit for AI agents over the SDK.** A new library
  (`libraries/ai`) that finds, describes and loads Mascope's data as
  handles rather than tables, the first part of the AI layer in
  `docs/dev/ai_layer.md`. `find` matches names as literal text (a compiled
  pattern by regex) across workspaces, datasets, batches, samples and target
  collections and returns candidates rather than failing on several;
  `describe` sums up one object in a few lines. The loaders (`load_peaks`,
  `load_timeseries`, `load_spectrum`, `load_peaks_by_stage`) return a
  `Frame` whose printed form is its schema, size, completeness and five rows,
  however large the table; its rows stay in Python behind `.df`, kept as
  parquet in a per-conversation session directory and loaded again from the
  recorded query when evicted. A `max_rows` cap holds during the load and
  stops it with the rows reached and the samples left, loads never prompt,
  and failures come back as structured errors that say what to do next (a
  403 names the object and the roles).
