- **The run selector marks a run whose peaks the sample no longer has.** Once a
  sample's peaks are found again (peak detection run again, an operator refit, a
  restored filestore), its earlier assignment runs name peaks it no longer has.
  The run selector now marks such a run *old peaks* before it is opened, and the
  runs listing says so in `peaks_current`, so an SDK client taking the newest
  completed run can tell too. Nothing is stored or pruned: the flag is computed
  when the runs are listed.
