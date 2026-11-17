- **A peak read as an assigned neighbour's isotope line keeps a lower tier
  the run held it at.** In-app peak assignment reads a peak on an assigned
  neighbour's isotope line as that line, at *candidate*. A peak the run held
  below assignability came back at *candidate* that way, whether its own
  evidence held it there or the *off calibration* cap did; it now stays below
  assignability (*read as a neighbour's line*,
  `docs/user/how-it-works/peak-assignment.md`). The checks that judge a run's
  rows now make every change of reading first and only then apply the caps,
  which only lower a tier, so the order of the caps decides which one a row
  names, never its tier. The ingest fold without a run reads the lists'
  compounds in the partner rule as a run does.
