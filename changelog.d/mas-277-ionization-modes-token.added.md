- **The SDK lists the ionization modes.** `mascope.ionization.modes()`
  gives each mode's ID, name, polarity, file-name token and mechanisms, so
  a script can turn a sample's `ionization_mode_id` into the mode's name.
  `GET /api/ionization/modes` and `GET /api/ionization/modes/{id}` now
  answer an API token as well as a signed-in browser; before, a token got
  401 there. Changing a mode still needs the web app.

- **A `mascope_viz` scope names its ionization mode by name.**
  `Scope(mode="Bromide")` now matches a mode's name as the scope's other
  names match: literal text, a compiled pattern by regex, an ID first. A
  name two modes share, such as one per instrument, returns both as
  candidates; with `instrument=`, only that instrument's modes and the
  shared ones are looked at.
