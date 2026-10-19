- **A targeted MS2 file with a named inclusion list reads as what it
  recorded.** In a version-66 Orbitrap file a targeted MS2 scan stores its
  inclusion-list entry's name in its scan-event record, so the records of
  such a file are not all one size, and OpenTFRaw 2.0.0 reads them all at
  one inferred size. From the second record on it decoded noise: the file
  was stored with both polarities and parked, most of its MS2 scans read as
  MS1, and the profile of nearly every scan was drawn on a wrong m/z axis.
  The OpenTFRaw backend now reads these records itself, each at its own
  size, and only for a file whose records walk exactly to the end of the
  stream and differ from what the reader reads; every other file is read as
  before (`mascope_thermo/v66_scan_events.py`). Such a file logs one
  warning, and one whose scan events still cannot be right - an analyzer an
  Orbitrap-only instrument does not have, or one scan event read as two MS
  orders - is refused with the reason in its status. An interim until the
  pinned reader reads these records (GitHub #2391); files parked by it need
  processing again.
