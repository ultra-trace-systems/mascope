- **A shifted decoy run opens the channels and claims the reagent lines its
  control did.** A decoy moved 0.35 Da (`mz_shift_da`) looked for the source's
  own ions on the moved axis and found none there: it searched only the mode's
  own channels, and offered the source's brightest ions to the stages as
  analytes. The secondary channels' carriers and the reagent pre-pass's lines
  are now looked for on the axis as stored, so a shifted arm differs from its
  control only where the sample's formulas sit. The arm's record says so
  (`source_ions` in `decoy_arm`).
