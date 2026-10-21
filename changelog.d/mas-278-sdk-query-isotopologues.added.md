- **`cheminfo.query_by_mz` reads a peak at any line of its ion.** The SDK's
  composition query now searches every isotopologue line by default, so an
  m/z that is a 13C or 81Br line, or a dibromide's brighter 79Br81Br line,
  finds its formula. This changes what an existing call returns: candidates
  read at another line come back too, with that line's m/z as their
  `target_isotope_mz`. Pass keyword-only `isotopologues=False` for the old
  monoisotopic-only answer. `known_only=True` keeps only formulas of a known
  reference compound. A server that predates the flags ignores them and
  searches the monoisotopic line only. The docstring now says the server
  ignores `limit`.
