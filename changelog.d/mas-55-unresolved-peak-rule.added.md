- **A peak assignment whose peak the instrument did not separate from a
  neighbouring peak is held at candidate.** Two lines show as two peaks only
  where their sum has two maxima: 0.85 widths at half maximum apart for two
  lines of equal height, 1.65 for a pair at 1:50, read at the width the file's
  resolving power gives at the peak's m/z. Closer than that, a TOF's peak picker
  can still report both lines, but neither one's mass nor its height is a single
  ion's own. Every committed formula is asked, one from a list included; every
  other peak of the spectrum counts as a neighbour except a line of the row's
  own isotope pattern. The one escape is another ionization channel committing
  the same neutral on a peak the instrument did separate. The new reason *peak
  not separated* names the nearest such neighbour, its distance in widths and
  its height against the peak's, and the peak inspector links it beside the
  reason. A hand assignment is not asked, nor a file whose resolving power the
  run did not read. On an Orbitrap it seldom fires. Tiering rule set 13; runs
  made before it are unchanged until run again
  (`docs/user/how-it-works/peak-assignment.md`, *Why a row holds its tier*).
