- **A file the converter can no longer store fails at once, with a reason.**
  The converter keeps who uploaded each file in memory, so after a restart
  it cannot store a file left over from before it. Such a file used to be
  read and fitted first and then fail with "not registered in file converter
  service". It now fails before it is read, with a reason in the converter's
  log, and error monitoring gathers every such file under one issue with how
  long the file had waited. A file written moments ago still gets a few
  seconds for its upload's record to arrive.
