- **A command line for `mascope_viz`: `mascope-viz`.** `scopes` resolves a
  scope (a dataset, batches, an instrument, a time range, a polarity, a mode,
  a collection) and lists its batches, the files acquired in its range and
  how many no batch holds, and its span, or the candidates when a name
  matches several objects. `store sync`, `store status` and `store rm` fill,
  describe and delete the scope's campaign store. `view` computes one of the
  seven views over the store and prints its summary: its rows and span, and
  per key the median and the maximum with its time; `--csv` writes the
  frame, which is never printed. Every command prints JSON with `--json`,
  and the output is ASCII. The URL, token and workspace are read as the SDK
  reads them.
