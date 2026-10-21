- **The SDK lists the ionization modes.** `mascope.ionization.modes()`
  gives each mode's ID, name, polarity, file-name token and mechanisms, so
  a script can turn a sample's `ionization_mode_id` into the mode's name.
  `GET /api/ionization/modes` and `GET /api/ionization/modes/{id}` now
  answer an API token as well as a signed-in browser; before, a token got
  401 there. Changing a mode still needs the web app.
