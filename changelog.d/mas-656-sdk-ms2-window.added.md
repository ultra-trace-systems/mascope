- **The SDK reads each MS2 group's isolation window.**
  `mascope.samples.ms2(sample_id).get_window()` wraps
  `GET /api/samples/{id}/ms2/window`: one record per group with the MS1
  centroids in its window and its located precursor, and the `reason` a
  sample lists none. `width=` draws every window at one width.
