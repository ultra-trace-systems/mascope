- **The calibration trend marks a collection change without walking the
  instrument's history.** To tell whether a collection version in the window
  is new, the trend looks back at the files acquired before it. A version or
  a collection never used before, the very one that gets a change mark, made
  that look read every earlier record of the instrument: seconds per version
  on a busy TOF, for the trend and again for its export. Two partial indexes
  on `sample_file` now answer each look in one read. The upgrade builds them
  while it holds off writes to `sample_file`: well under a second on a
  history of small records, about 25 s per 100,000 TOF records of 40 lines.
