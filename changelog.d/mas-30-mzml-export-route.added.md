- **Sample files and samples export as mzML.** `POST /api/file/mzml`
  writes a sample file whole, or a sample as its time window: its file's
  scans of its polarity between its start and end. Each spectrum holds the
  scan's centroids, the peaks Mascope reads. It runs in the background like
  the raw download, and its notification names one `.mzML` file or a zip.
  Thermo raw files only.
