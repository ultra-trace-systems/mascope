- **A collection edit naming unknown compound ids is refused.** Creating or
  updating a target collection with a `target_compound_ids` entry that no
  compound in the library has now answers 422, listing the unknown ids, and
  changes nothing. Such ids used to be dropped quietly, so an update naming
  only stale ids emptied the collection and sent its batches to rematch.
