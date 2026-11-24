- **`mascope-viz view` lists a polarity-switching batch's run change once.**
  The text output printed a `+-` batch's run change twice, as two identical
  lines, and counted it twice. Entries of one moment, batch, before and
  after are now one line that names its polarities ("polarity -",
  "polarities + and -"), and the count is of lines. `--json` keeps one
  entry per polarity.
