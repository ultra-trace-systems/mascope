- **An instrument is kept as a row of its own.** Deleting an instrument's
  last file no longer removes everything known about it. The new
  `instrument` table holds one row per instrument name, with its class,
  vendor and, for an Orbitrap, the model its files report; a file's
  registration fills it and moves its `last_seen_utc`. Every table that names
  an instrument (sample files, datasets, ionization modes, method bindings,
  instrument functions, paired devices, notifications, dataset tombstones)
  links the row through a new `instrument_id` beside its `instrument`
  column, and the upgrade makes a row for every name already in the
  database. The name columns stay, and every read still uses them.
