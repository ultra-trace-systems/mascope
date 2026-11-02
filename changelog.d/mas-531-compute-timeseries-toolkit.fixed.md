- **The SDK's `samples.compute_peak_timeseries` works against a server.**
  It lists a sample's peaks before computing anything, and that listing
  asked for neither areas nor heights. The server refuses such a read with
  an HTTP 400, so the call failed before it computed a single time series.
  It now asks for heights.
