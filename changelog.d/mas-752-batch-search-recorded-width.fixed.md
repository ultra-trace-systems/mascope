- **The batch's *Search untargeted* ranks a sample's candidates at that
  sample's own mass width.** It ranked them at the instrument class's width
  (0.3 ppm on an Orbitrap, 3 ppm on a TOF). A run of the sample ranks at the
  width it measured on the sample, which can be far from the class's, so a
  run and the batch search could rank close candidates differently. The
  search now ranks at the width the sample's latest run measured, and at the
  class's only where that run measured none or the sample has no run.
