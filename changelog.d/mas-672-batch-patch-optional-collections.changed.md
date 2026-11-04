- **`batches.update` in the SDK renames a batch without its collections.**
  `target_collection_ids` is optional: left out, it is not sent and the batch
  keeps its target collections, so `batches.update(batch_id, name=...)`
  only renames it. A list still replaces them. An older server still
  requires the list.
