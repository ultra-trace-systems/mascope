- **`mascope_viz` coverage names each ionization mode.** The campaign
  store now records each sample's mode name beside its ID, from the
  server's modes listing at sync, and `views.coverage` gives it as
  `mode_name` beside `mode`. `mascope-viz view coverage` prints the name
  in its MODE column. A store written before this is fetched again by its
  next sync. A server that refuses the token on the modes listing leaves
  the names empty, and the IDs are shown.
