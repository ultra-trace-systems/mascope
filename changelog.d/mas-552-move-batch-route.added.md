- **A sample batch moves into another dataset.**
  `POST /api/sample/batches/{id}/move` takes the destination `dataset_id` and
  moves the batch whole: it keeps its ID, its samples, their matches and its
  target collections. It needs the editor role on the batch and on the
  destination dataset, and the global admin role for a locked batch or a
  batch leaving a locked dataset; a locked destination takes none.
  ACQUISITION batches and datasets are refused, as is a collection of the
  batch scoped to a workspace other than the destination's. The route
  answers an SDK token.
