- **Each scan range of a stitched file has an instrument function of its
  own.** For servers that run `composite_scan_streams`. A file whose
  polarity is stitched from several scan ranges was fitted one peak shape
  and one resolution function over all of them, though each range is
  acquired at its own settings. Each range is now fitted on its own summed
  signal as well, when its peaks are detected: its peaks' areas are
  integrated under its own peak shape, and what reads a peak's expected
  width - the calibration's overlap filter, batch alignment, batch peaks
  and the peak assignment's mass gate - reads it from the range the peak
  came from. A range with too few peaks to fit takes the fit of the
  nearest range of the same resolution setting, else the whole file's.
  The fits are kept in the file's peak store, and the file's processing
  detail says each range's resolution at m/z 200 beside the whole file's.
  Files detected whole, which is every file of a single scan range, are
  fitted and read as before; a stitched file converted earlier keeps its
  one function until it is processed again.
