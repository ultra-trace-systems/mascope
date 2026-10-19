- **The dashboard says what to do next where it used to go quiet.** Five
  small first-run and feedback fixes in the web app, none of which touch the
  server. The Raw files tab opens on the last 7 days rather than the last
  24 hours (a deployment's `acquisition_filter` still wins, and may name any
  `Last N days`), offers **All time** among its ranges, and an empty list
  names the range in force ("No raw files in the last 7 days") and widens to
  the next range or to all time in one click; a list the status filter
  emptied says so and clears it without touching the range, a page the
  search emptied says the match may be on another page, and the list says
  when it is still loading or could not load instead of reading as empty.
  Toasts, and the notification log with them, are headed for the reader -
  "Share link", "Composition search", "Calibration fit", "Match refresh",
  "Upload", "Processing" - with "warning" or "failed" after the heading when
  that is the news, instead of the operation's internal name and status word
  ("Shared link success", "Match compositions by mz success"); a composition
  search that finds nothing no longer toasts at all, since the search pane
  already says "No results found" in place. The Batch, Sample and Match tabs
  say what enables them when hovered while disabled ("Open a batch with
  samples", "Select a sample", "Visualize an ion match from the ion table"),
  and say so to a screen reader as well. The Create dataset, Create batch and
  Create target collection buttons spell out their label above an empty list
  and go back to icon-only beside a full one, and are named for a screen
  reader in both states. The target-collection pane says what it is for
  while no batch is open, instead of a blank box with a plus in its corner.
