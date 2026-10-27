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

- **An ionization mode carries its labelled reagent's isotopic purity.**
  `label_purity` on a mode (0.98 by default, the figure every run used
  until now) is what a run of its samples reads a labelled ion's envelope
  at - the 15N of a 15N nitrate in the reagent pre-pass, the reference
  lines, the untargeted search and the tiering - so a site whose reagent is
  99% can say so. It is set through `POST` and `PATCH
  /api/ionization/modes`, also on a mode Mascope ships, and changing it
  makes the mode's next version. The target library's own isotopes are
  still computed at the default.
