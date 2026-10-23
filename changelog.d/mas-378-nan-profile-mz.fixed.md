- **An Orbitrap file with a scan the reader gives no m/z for is refused.**
  OpenTFRaw 3.x, the reader this release moves to, gives no m/z for the
  profile of a scan whose calibration layout it does not know, where 2.0.0
  gave the scan's frequencies, which Mascope drew as m/z. Unchecked, the
  averaged profile of a selection holding such a scan would come back as a
  single point, every other scan's signal lost with it. Reading such a
  scan's profile now refuses the file, with the scan and how many of its
  points have no m/z in its status. No file measured so far has such a
  scan: none of the demo bundle's files, the two committed sample files or
  the internal regression corpus's Orbitrap files is refused (GitHub #2518).
