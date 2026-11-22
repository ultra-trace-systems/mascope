- **A completed Assign batch no longer leaves the isotope lines out of date.**
  The series stage runs after the isotope lines, and it could move a batch
  peak the isotope lines were read against (its stripped rows, or its
  predicted lines) to the formula a series proposed. The lines' record then stood for a reading the peak no longer
  held, so every pass ended with the isotope lines stale, and the next pass
  started there to the same end. The series now yields to the isotope lines:
  such a batch peak keeps its reading, and a series proposes it nothing but
  that reading. The series' run summary counts these peaks as
  `anchors_yielded`.
