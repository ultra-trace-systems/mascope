- **A thin Orbitrap file no longer pools its own scans twice.** The pooled envelope
  curve of a file too thin for its own read a copy of the sample in its batch, or an
  overlapping time window of the same file, as one of its six neighbours, so it counted
  the file's own lines twice. Such samples are now left out; a window of the same file
  that shares no scans still counts as a neighbour, placed in time by its own start, and
  the run's record marks it as being of the same file.
