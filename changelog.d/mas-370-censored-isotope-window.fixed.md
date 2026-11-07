- **Faint 13C lines on Orbitrap files are found, not counted as missing.**
  Untargeted assignment took a line read more than 40% under its share for
  another compound's peak, and charged the formula for a missing line. On a
  file whose run fitted its own response curve, the cut now opens down to that
  curve's low reading at the line's expected signal-to-noise, so a dim line
  censored scan by scan is matched. Above the share, and on files without a
  curve, the 40% cut is unchanged.
