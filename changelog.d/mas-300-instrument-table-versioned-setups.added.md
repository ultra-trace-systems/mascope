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

- **An assignment run and a confidence curve say which ionization setup they
  were made under.** A run's stored config, as `GET
  /api/peak-assignments/sample/{id}/runs` returns it, now carries
  `ionization_setup`: the version of the sample's ionization mode it
  searched under (`ionization_mode_id` and `version`), or `null` for a
  sample with no mode. A refitted curve records in `ionization_setups` the
  versions its verified assignments were scored under. A run under a version
  its instrument's curve was not fit under still uses that curve, but the
  server log warns once per curve and version, and the run's calibration
  record says so (`setup_mismatch`). The upgrade records version 1 on
  existing in-app runs and, on existing curves, the versions of the
  verifications their fit could have drawn on.
