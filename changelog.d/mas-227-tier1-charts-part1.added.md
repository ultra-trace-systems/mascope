- **`mascope_viz` has its first chart kinds: `coverage`,
  `instrument_signal`, `scope_summary` and `top_species`.** `coverage`
  draws when the instrument acquired, by polarity and mode, with the gaps
  shaded and the files no batch holds marked. `instrument_signal` draws the
  TIC and the reagent ions named, with their sum and each one's share of it
  when there are two or more, and gives each trace's drift and largest
  step. `scope_summary` is a digest's cover table, a run change marked.
  `top_species` lists the brightest species, the biggest changers and the
  new ones. Each figure's summary holds the numbers a caption may cite, as
  JSON. `views.top` ranks only the keys present in at least half the
  samples compared (`min_present=`), and gives each key's detections and
  presence.
