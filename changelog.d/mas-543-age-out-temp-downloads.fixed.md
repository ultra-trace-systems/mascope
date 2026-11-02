- **Downloads no longer pile up on the server until it restarts.** A
  download the app writes - a spreadsheet, a CSV, the raw-file bundle, a
  deposit package - is now kept for 24 hours, and each new export removes
  the older ones. The raw-file bundle and the deposit package copy a
  dataset's raw files, so on a long-running server they could fill the disk
  (#2726).
