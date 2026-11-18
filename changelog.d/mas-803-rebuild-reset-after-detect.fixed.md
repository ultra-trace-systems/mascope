- **A stale peak store that cannot be rebuilt no longer resets its batches.**
  `rebuild_stale_peak_stores` now detects a file's peaks before it sets the
  batches holding its samples to rematch and removes their matches. A file
  with no instrument configuration, or whose detection fails, keeps its
  batches and matches, however many runs fail on it (#3095).
