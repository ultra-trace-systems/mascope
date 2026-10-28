- **`load_peaks` reads no matches unless asked, from a server with the batch read.**
  Its `matches` now defaults to off where each batch comes in one request,
  and to on where an older server is read sample by sample, as before. Pass
  `matches=True` for the match columns from either. The frame of the batch
  read also carries `is_satellite`, False throughout. The SDK now requires
  `pyarrow` and pandas 2.1 or newer.
