- **Confirming on a reference standard, MS/MS or orthogonal evidence needs a
  structure.** Those are statements about a structure, so the verify routes
  answer 422 to such a confirmation without a `claimed_inchikey`, and the SDK's
  `peak_assignments.verify` raises before sending it. A confirmation on the
  isotope/adduct pattern or a visual check needs none.
