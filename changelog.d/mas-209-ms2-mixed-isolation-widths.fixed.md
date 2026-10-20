- **The MS2 summary answers for a file whose MS2 events use different
  isolation widths.** A targeted method sets the width per event, as one
  with a wide event and a narrow one does; `GET /api/samples/{id}/ms2/summary`
  refused such a file with a 400 although its centroids and timeseries
  answered. Each entry of `groups` now carries its own `isolation_width`,
  and the top-level `isolation_width` is `null` when the scans do not share
  one (unchanged when they do). A file none of whose MS2 scans stores a
  width is still refused, now with a message that ends in one full stop.
  The MS2 notebook toolkit reads each group's width, and the file's one
  from an older server. (#2421)
