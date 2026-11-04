- **A calibration fit's calibrant share of the TIC no longer shrinks with
  the file's length.** Each calibrant's share, and their sum in the fit's
  summary and stored quality block, was its scan-averaged intensity over the
  TIC summed over every scan, so a file of twice the scans read half the
  share. It is now over the mean TIC per scan, of the file or of the
  calibrant's own scan stream; a file fitted stream by stream weighs each
  stream's share by the stream's summed ion current. On TOF the share is
  also a true fraction of the ions now: a peak area carries the sample
  interval in ns, which the share divides out. Stored calibration records
  keep the old share until the sample is refitted.
