- **A thin Orbitrap file's envelope pool reads time windows as the scan readers do.**
  Peak assignment leaves out of a thin file's pool any sample of the same file that
  reads some of its scans. It used to treat two windows that touch, one ending where
  the next starts, as sharing none, though both read the scan at that time, so each
  pooled the other with that scan counted twice. A window now includes the scans at
  both its bounds, a zero-length window inside a wider one counts as sharing its scan,
  and a window bound that is not a number counts as open, whichever sample is read.
