- **The batch's *Search untargeted* ranks a sample's candidates at that
  sample's own mass offset.** It ranked them at no offset at all. A run of the
  sample ranks at the offset it scores the sample at, so on a sample whose
  mass axis sits beyond the instrument class's width, a run and the batch
  search could pick different formulas for the same peak. The search now
  ranks its candidates at the offset the sample's latest run scored at, or at
  its reagent ions' where it had no run, as the run's own search does.
