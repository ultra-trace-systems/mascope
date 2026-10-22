- **The composite cut no longer stitches a scan range against itself.** For
  servers that run `composite_scan_streams`. A file whose polarity runs one
  scan range twice at the same microscans, with another setting the second
  time - a survey scan whose resolution changes part way through the run,
  say - was detected per stream, and the stitch gave the whole range to
  whichever stream came first: a targeted MS2 file was matched on its first
  37 survey scans and none of the 90 that followed. Such a polarity is now
  detected whole, every scan averaged together, as it is with the setting
  off. Stitched files of several ranges, and the settle-then-measure layout,
  whose measurement owns its range by its microscans, are stitched as before.
  A file already stitched this way is detected whole when it is re-processed.
  Where such streams still sit beside another range, the processing detail
  and the calibration name them apart by resolution (`m/z 40-600, R=60000`)
  instead of by the same range twice.
