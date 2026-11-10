- **A capped `rebuild_stale_peak_stores` run gets past files it cannot rebuild.**
  With `REBUILD_LIMIT` set, a file whose rebuild failed used up a slot of the
  cap and stayed at the top of the list, so enough of them stopped every later
  run before any older stale store. Failures no longer count against the cap,
  and a run left with nothing but failures says so instead of advising another
  run.
