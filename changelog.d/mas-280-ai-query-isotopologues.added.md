- **`mascope_ai`'s `query_by_mz` takes `isotopologues` and `known_only`.**
  `isotopologues=False` reads the m/z as the ion's monoisotopic line only;
  by default it may be any line above the server's floor, a 13C or 81Br line
  among them. `known_only=True` keeps only the formulas of known reference
  compounds. Both reach the SDK's mass query and the session's manifest.
