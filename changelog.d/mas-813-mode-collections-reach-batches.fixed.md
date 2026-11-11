- **A mode's new collections reach the batches made before the edit.** A
  daily acquisition batch keeps a copy of its mode's calibrant and diagnostic
  collections, and matching reads that copy. After an edit gave the mode
  another collection, the batch's re-match still matched against the old
  one. The re-match, and a file reprocessed into the day's batch, now swap
  the collections the earlier version gave the batch for the new version's,
  and flag the batch for a re-match where reprocessing changed them. A
  collection attached to the batch by hand stays, and an analysis batch
  keeps its collections.
