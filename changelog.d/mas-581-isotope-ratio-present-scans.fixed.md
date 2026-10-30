- **Targeted matching no longer drops dim Orbitrap isotope lines.** An
  Orbitrap scan keeps a peak only where it clears that scan's noise floor, so
  a dim isotope line is missing from some scans, and its stored intensity
  divides its sum by all of them. The matcher read the line's ratio to the
  ion's most abundant line from those diluted intensities, and a line missing
  a fifth of the scans or more fell outside the isotope ratio tolerance and
  scored zero. The ratio is now read over the scans where both lines are
  present, and the Match tab's isotope table shows the measured ratio and the
  number of scans it was read over. TOF files are unchanged.
