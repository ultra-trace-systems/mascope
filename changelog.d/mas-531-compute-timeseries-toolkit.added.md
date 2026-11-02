- **`mascope_ai` computes the time series a stage load left out.** A stage
  of `load_peaks_by_stage` leaves out every peak whose time series the
  server has not computed, and says so. The new `compute_timeseries(sample,
  mz_range=None, max_peaks=)`, also an MCP tool, has the server compute
  them, so the stage loaded again is whole. Each peak is one request, so it
  counts first and refuses, before computing any, a call over `max_peaks`
  (500 by default, and at most the ceiling `Toolkit(max_peaks=)` sets).
  Filling this cache is treated as a read: no object changes.
