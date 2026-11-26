- **A peak assigned by hand as an isotope line no longer disappears.** Assigning a
  peak as one of a formula's isotope lines (`M+2`, `M+4`) on a sample whose run holds
  no main line of that formula left the row out of the **Assignments** ledger and its
  tier counts. It is now a row of its own, its line beside the formula. It is stored as
  the line, so a rebuild of the batch ledger reads it as a line of its compound rather
  than as the compound at the line's m/z, and the line's batch peak keeps its family.
