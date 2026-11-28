- **A row's rival set says which model named its members.** The
  `provenance.rival_set` block of a committed row gains `named_at_version`
  beside `model_version`: the model the formula search ranked at and named
  the set's members by, and the one they were measured at. Where a run took
  its refitted model the two differ, so a reader sees that the membership is
  older than the measurement. A new run option, `count_scored_rivals`, off by
  default and meant for the assignment bench alone, counts in the run's
  search scope how many of the other candidates the search scored would tie
  a reading at the model the run's caps read (the refitted one where the run
  took it, the first otherwise); it changes no row.
