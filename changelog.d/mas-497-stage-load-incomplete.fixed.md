- **`mascope_ai`'s `load_peaks_by_stage` no longer says a stage short of
  peaks is complete.** A stage is a time-ranged read, and the server leaves
  out of it every peak whose time series it has not computed yet, typically
  the peaks no target matched. The Frame said `complete=True` all the same,
  with the shortfall only in its `warnings`. It is now incomplete, and each
  such stage is in its `failures` as `TimeseriesNotComputed`, with how many
  peaks it left out (`excluded_peaks`).
