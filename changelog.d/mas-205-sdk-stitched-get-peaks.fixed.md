- **The SDK reads the peaks of a stitched sample again.** Since 1.12.0 the
  peak listing names a stitched sample's segments beside its peaks, one
  entry per scan stream, and `samples.get_peaks` put that list into the
  frame with the per-peak columns, so every read of a stitched sample
  raised `ValueError: All arrays must be of the same length` - and so did
  `load_peaks`, `load_peaks_by_stage` and `load_peak_timeseries` over one.
  The segments are now in `df.attrs["segments"]` (None for a sample that is
  not stitched) beside the per-peak `segment` column, the loaders carry
  them per sample, and anything else the server sends beside the per-peak
  lists goes to `df.attrs` instead of the frame.
