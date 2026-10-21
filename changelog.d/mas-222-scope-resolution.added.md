- **`mascope_viz` resolves a scope of data to its batches and files.**
  `Scope(dataset=, batches=, instrument=, start=, end=, polarity=, mode=,
  collection=, tz=)` names what a digest is about, and
  `resolve(client, scope)` returns its batches in time order (each with its
  sample count and first and last acquisition time), every file acquired in
  its range whether or not a batch holds it, the target collections on its
  batches and the instrument's time zone. A time without a zone is read in
  the instrument's acquisition zone unless `tz=` names another. Names match
  as literal text (a compiled pattern by regex); a name that matches several
  objects returns them as candidates, and one that matches none fails with
  the names there are. A resolved scope has a short stable key, the same for
  every spelling of it, for the campaign store to name its directory by.
